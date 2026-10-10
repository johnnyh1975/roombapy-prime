"""Does the Prime app's form of a cloud call also work for a Classic robot?

WHY THIS EXISTS. roombapy-prime 0.4.0 took over ha_roomba_plus' Classic
cloud calls unchanged -- the requests its own cloud_api.py sent, which
are the ones confirmed on Classic robots. Three of those endpoints also
exist in this library in the form the PRIME app uses, with different
parameters or a different body:

  mission history   Classic: count / before       Prime: maxReports / exclusiveStartTimestamp
  favorites         Classic: no parameter         Prime: app_edition=1
  part counter      Classic: {"parts":[...]}      Prime: {"parts": [...]}

Nobody has shown that the Prime forms work on a Classic robot, so both
are kept. This script answers the question on a real Classic account.
If the Prime form holds, one variant per call can go; either way the
answer is recorded instead of assumed.

THE PART-COUNTER BODIES ARE ONE BODY SINCE 0.6.0. Until 0.5.0 the Prime
form (reset_robot_parts()) added `robot_id` and `num_parts`, read off
the response's DTO instead of the request's. Corrected from app 3.2.0,
it is the Classic body with json.dumps()'s spacing -- so this test now
measures only whether the spacing matters, and a pass is what both apps
lead one to expect.

TWO ACTIONS, and only the second one writes:

  --compare-reads
      Mission history with both parameter sets, including one page of
      paging; favorites with and without app_edition; the part counters
      as they stand. Sends nothing that changes anything.

  --reset-part-with-prime-body PART_ID
      Resets ONE part to "new" using the Prime body. ONLY FOR A PART YOU
      WANT RESET ANYWAY -- one you have just replaced. This is not a
      reversible probe: a counter is written in percent, and nothing has
      ever measured whether writing a non-zero value back restores the
      minute-exact fields. Zero is the only value the integration has
      ever written, so zero is the only value written here.

      If the Prime body leaves the counter where it was, the script
      performs the reset with the Classic body instead, so the part you
      asked to reset is reset either way.

Two gates for the write, as in the other verify scripts:
  1. --i-understand-this-resets-a-real-part-counter
  2. An interactive y/N showing the part and the exact request body.

Paste the whole output into the issue -- the lines above the summary
carry the actual comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from roombapy_prime.auth import is_prime_sku
from roombapy_prime.diagnostics import Report
from roombapy_prime.rest_client import ClassicRestClient, PrimeRestClient, RestError

from ._cli import (
    add_account_arguments,
    confirm,
    field,
    logged_in_cloud_clients,
    require_blid,
    resolve_credentials,
    run_script,
)

#: What ha_roomba_plus sends with every Classic mission-history request.
_FILTER_TYPE = "omit_quickly_canceled_not_scheduled"
_SUPPORTED_DONE_CODES = ["dndEnd", "returnHomeEnd"]


def _is_refused_as_prime(sku: str | None, report: Report) -> bool:
    """The question is about Classic robots; a Prime answer would be
    noise dressed up as a finding. Reported, not raised: the run did
    what it should by stopping, and a traceback would say otherwise."""
    if not is_prime_sku(sku):
        return False
    report.add(
        "Target robot", "SKIPPED",
        f"sku={sku} is a Prime robot -- this script measures Classic robots. Pass the --blid "
        "of a Classic one (900 series, i, s or j series).",
    )
    return True


def _mission_identity(record: Any) -> tuple[Any, Any]:
    return (field(record, "startTime"), field(record, "nMssn"))


def _favorite_identity(record: Any) -> Any:
    return field(record, "favorite_id") or field(record, "id") or json.dumps(record, sort_keys=True)


def _keys(records: list[Any]) -> set[str]:
    return {key for record in records if isinstance(record, dict) for key in record}


def _compare_lists(
    report: Report, label: str, classic: Any, prime: Any, identity
) -> None:
    """One verdict line for two answers to the same question."""
    if not isinstance(classic, list) or not isinstance(prime, list):
        report.add(
            label, "FAILED",
            f"not both lists: classic={type(classic).__name__} prime={type(prime).__name__}",
        )
        return
    same_records = [identity(r) for r in classic] == [identity(r) for r in prime]
    only_classic = sorted(_keys(classic) - _keys(prime))
    only_prime = sorted(_keys(prime) - _keys(classic))
    print(f"  {label}: classic {len(classic)} record(s), prime {len(prime)} record(s)")
    if only_classic:
        print(f"    keys only in the classic answer: {only_classic}")
    if only_prime:
        print(f"    keys only in the prime answer:   {only_prime}")
    if same_records and not only_classic and not only_prime:
        report.add(label, "OK", f"identical: {len(classic)} record(s), same keys")
    else:
        report.add(
            label, "FAILED",
            f"differ: same records={same_records}, keys only classic={len(only_classic)}, "
            f"only prime={len(only_prime)}",
        )


async def _fetch(report: Report, label: str, call) -> Any:
    """A refusal is a finding here, not a crash: the Prime form being
    rejected on a Classic robot is one of the two possible answers."""
    try:
        return await call
    except RestError as exc:
        report.add(label, "FAILED", f"{type(exc).__name__}: {exc}")
        return None


def _start_times(records: list[Any]) -> list[int]:
    times = []
    for record in records:
        value = field(record, "startTime")
        if isinstance(value, int):
            times.append(value)
    return times


def _check_page_size(report: Report, label: str, records: list[Any], asked: int) -> None:
    """Whether the server honoured the page size -- the question the
    first field run answered for Classic: count=10 asked, 33 returned."""
    if len(records) <= asked:
        report.add(label, "OK", f"{asked} asked, {len(records)} returned")
    else:
        report.add(label, "FAILED", f"{asked} asked, {len(records)} returned -- the parameter is ignored")


def _check_paging(
    report: Report, label: str, first: list[Any], page: Any, asked: int
) -> None:
    """Whether the second page continues where the first ended.

    EACH FORM PAGES FROM ITS OWN FIRST PAGE. The first version paged both
    from the Classic page's last record, assuming Classic honoured its
    page size. On a Roomba 980 it did not: the Classic page held all 33
    missions, both second pages started below the oldest one, and an
    empty Prime page could not say whether its paging worked.
    """
    if not isinstance(page, list):
        report.add(label, "FAILED", f"not a list: {type(page).__name__}")
        return
    first_times = _start_times(first)
    page_times = _start_times(page)
    if len(first) < asked:
        report.add(
            label, "SKIPPED",
            f"the first page ({len(first)} of {asked}) already reached the end of the history",
        )
        return
    anchor = min(first_times) if first_times else None
    if anchor is None:
        report.add(label, "SKIPPED", "the first page carries no startTime to page from")
        return
    overlap = set(first_times) & set(page_times)
    if overlap:
        report.add(
            label, "FAILED",
            f"{len(overlap)} of {len(page)} record(s) repeat the first page -- the paging "
            "parameter is ignored",
        )
    elif not page:
        report.add(label, "FAILED", "empty, although the first page was full")
    elif all(t < anchor for t in page_times):
        report.add(label, "OK", f"{len(page)} older record(s), none repeated")
    else:
        report.add(label, "FAILED", "records newer than the first page's oldest")


async def _compare_history(
    classic_client: ClassicRestClient, prime_client: PrimeRestClient,
    blid: str, count: int, report: Report,
) -> None:
    print("\n== Mission history: Classic parameters vs Prime parameters ==")
    app_id = f"IOS-{uuid.uuid4()}"
    classic = await _fetch(report, "History, Classic parameters", classic_client.get_mission_history(
        blid, app_id=app_id, filter_type=_FILTER_TYPE,
        supported_done_codes=_SUPPORTED_DONE_CODES, count=count,
    ))
    prime = await _fetch(report, "History, Prime parameters", prime_client.get_mission_history(
        blid, filter_type=_FILTER_TYPE,
        supported_done_codes=_SUPPORTED_DONE_CODES, max_reports=count,
    ))
    if not isinstance(classic, list) or not isinstance(prime, list):
        if classic is not None and prime is not None:
            report.add(
                "History, first page", "FAILED",
                f"not both lists: classic={type(classic).__name__} prime={type(prime).__name__}",
            )
        return
    print(f"  first page: classic {len(classic)} record(s), prime {len(prime)} record(s)")
    _check_page_size(report, "History, Classic page size (count)", classic, count)
    _check_page_size(report, "History, Prime page size (maxReports)", prime, count)

    only_classic = sorted(_keys(classic) - _keys(prime))
    only_prime = sorted(_keys(prime) - _keys(classic))
    if only_classic:
        print(f"    keys only in the classic answer: {only_classic}")
    if only_prime:
        print(f"    keys only in the prime answer:   {only_prime}")
    if classic and prime and not only_classic and not only_prime:
        report.add("History, same fields", "OK", f"{len(_keys(classic))} field(s) in both")
    elif classic and prime:
        report.add(
            "History, same fields", "FAILED",
            f"only classic={len(only_classic)}, only prime={len(only_prime)}",
        )
    else:
        report.add("History, same fields", "SKIPPED", "one of the answers is empty")

    classic_times = _start_times(classic)
    if classic_times:
        classic_page = await _fetch(report, "History paging, Classic", classic_client.get_mission_history(
            blid, app_id=app_id, filter_type=_FILTER_TYPE,
            supported_done_codes=_SUPPORTED_DONE_CODES, count=count, before=min(classic_times),
        ))
        if classic_page is not None:
            print(f"  classic second page: {len(classic_page) if isinstance(classic_page, list) else '?'} record(s)")
            # Judged against what was ASKED only when the first page
            # honoured it; a first page that ignored count is "full".
            _check_paging(
                report, "History, Classic paging (before)", classic, classic_page,
                count if len(classic) <= count else len(classic),
            )
    prime_times = _start_times(prime)
    if prime_times:
        prime_page = await _fetch(report, "History paging, Prime", prime_client.get_mission_history(
            blid, filter_type=_FILTER_TYPE, supported_done_codes=_SUPPORTED_DONE_CODES,
            max_reports=count, exclusive_start_timestamp=min(prime_times),
        ))
        if prime_page is not None:
            print(f"  prime second page: {len(prime_page) if isinstance(prime_page, list) else '?'} record(s)")
            _check_paging(
                report, "History, Prime paging (exclusiveStartTimestamp)", prime, prime_page, count,
            )


async def _compare_favorites(
    classic_client: ClassicRestClient, prime_client: PrimeRestClient, report: Report
) -> None:
    print("\n== Favorites: without app_edition (Classic) vs app_edition=1 (Prime) ==")
    classic = await _fetch(report, "Favorites, Classic request", classic_client.get_favorites_raw())
    prime = await _fetch(report, "Favorites, Prime request", prime_client.get_favorites_raw())
    if classic is None or prime is None:
        return
    _compare_lists(report, "Favorites", classic, prime, _favorite_identity)


def _print_parts(parts: Any) -> None:
    for part in field(parts, "parts", None) or []:
        print(
            f"  part_id={field(part, 'part_id')}  counter={field(part, 'counter')}% used  "
            f"count_used={field(part, 'count_used')}  "
            f"count_remaining={field(part, 'count_remaining')}  "
            f"last_updated_ts={field(part, 'last_updated_ts')}"
        )


async def compare_reads(
    username: str, password: str, country_code: str, blid: str, count: int = 10
) -> None:
    """Reads only. Nothing sent here changes anything."""
    async with logged_in_cloud_clients(username, password, country_code, blid) as (
        classic, prime, blid, sku, report
    ):
        if _is_refused_as_prime(sku, report):
            return
        await _compare_history(classic, prime, blid, count, report)
        await _compare_favorites(classic, prime, report)
        print("\n== Part counters as they stand ==")
        parts = await _fetch(report, "Part counters", classic.get_robot_parts(blid))
        if parts is not None:
            _print_parts(parts)
            report.add("Part counters", "OK", f"{len(field(parts, 'parts', None) or [])} part(s)")


def _find_part(parts: Any, part_id: str) -> Any:
    for part in field(parts, "parts", None) or []:
        if str(field(part, "part_id")) == part_id:
            return part
    return None


async def reset_part_with_prime_body(
    username: str, password: str, country_code: str, blid: str, part_id: str
) -> None:
    """Resets one part to new with the Prime body; falls back to the
    Classic body if that did nothing, so the reset happens either way."""
    async with logged_in_cloud_clients(username, password, country_code, blid) as (
        classic, prime, blid, sku, report
    ):
        if _is_refused_as_prime(sku, report):
            return
        before = await classic.get_robot_parts(blid)
        print("\n== Part counters before ==")
        _print_parts(before)
        part = _find_part(before, part_id)
        if part is None:
            report.add("Target part", "FAILED", f"part_id={part_id} is not on this robot")
            return
        if not field(part, "counter"):
            # A part at 0 % cannot tell a working reset from an ignored one.
            report.add(
                "Target part", "SKIPPED",
                f"part_id={part_id} is already at 0 % -- a reset would show nothing",
            )
            return

        prime_body = {"parts": [{"part_id": part_id, "counter": 0}]}
        print(f"\nPart {part_id} is at {field(part, 'counter')} % used.")
        print("Request, Prime body (what this test sends):")
        print(f"  POST /v1/robots/{blid}/parts  {json.dumps(prime_body)}")
        print("Fallback if it does nothing (the Classic body, confirmed on Classic):")
        print(f'  POST /v1/robots/{blid}/parts  {{"parts":[{{"part_id":"{part_id}","counter":0}}]}}')
        if not confirm(f"Reset part {part_id} to NEW now? Only for a part you have replaced."):
            report.add("Reset", "SKIPPED", "declined -- nothing was sent")
            return

        response = await _fetch(
            report, "Reset, Prime body", prime.reset_robot_parts(blid, [part_id], {part_id: 0})
        )
        if response is not None:
            print(f"  response: {json.dumps(response)}")
        after = _find_part(await classic.get_robot_parts(blid), part_id)
        print(f"  counter after: {field(after, 'counter')} % used")
        if after is not None and field(after, "counter") == 0:
            report.add(
                "Prime body on a Classic robot", "OK",
                f"part {part_id} reset to 0 % -- the Prime body works here",
            )
            return

        report.add(
            "Prime body on a Classic robot", "FAILED",
            f"part {part_id} still at {field(after, 'counter')} % -- the Prime body did not reset it",
        )
        print("\nResetting with the Classic body instead, so the part is reset as asked ...")
        await classic.set_robot_part_counter(blid, part_id, 0)
        fallback = _find_part(await classic.get_robot_parts(blid), part_id)
        if fallback is not None and field(fallback, "counter") == 0:
            report.add("Reset, Classic body", "OK", f"part {part_id} now at 0 %")
        else:
            report.add(
                "Reset, Classic body", "FAILED",
                f"part {part_id} at {field(fallback, 'counter')} % -- reset it in the iRobot app",
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Measures whether the Prime app's form of three cloud calls also works for a "
            "Classic robot. See this module's docstring before using the reset."
        )
    )
    add_account_arguments(parser)
    parser.add_argument(
        "--compare-reads", action="store_true",
        help="Mission history, favorites and part counters in both forms. Sends nothing "
        "that changes anything.",
    )
    parser.add_argument(
        "--count", type=int, default=10,
        help="Mission-history records per page for --compare-reads (default 10).",
    )
    parser.add_argument(
        "--reset-part-with-prime-body", metavar="PART_ID", default=None,
        help="Reset ONE part to new with the Prime body. Only for a part you have replaced.",
    )
    parser.add_argument("--i-understand-this-resets-a-real-part-counter", action="store_true")
    args = parser.parse_args()
    require_blid(args)

    if not (args.compare_reads or args.reset_part_with_prime_body):
        print(
            "Nothing to do -- pass --compare-reads (safe, changes nothing) or "
            "--reset-part-with-prime-body PART_ID."
        )
        return
    if args.compare_reads and args.reset_part_with_prime_body:
        print("Aborted: one action per run -- --compare-reads or the reset, not both.")
        sys.exit(1)
    if args.reset_part_with_prime_body and not args.i_understand_this_resets_a_real_part_counter:
        print("Aborted: --i-understand-this-resets-a-real-part-counter is missing.")
        sys.exit(1)

    username, password = resolve_credentials(args)

    if args.compare_reads:
        sys.exit(run_script(
            compare_reads(username, password, args.country_code, args.blid, args.count)
        ))
    sys.exit(run_script(reset_part_with_prime_body(
        username, password, args.country_code, args.blid, args.reset_part_with_prime_body,
    )))


if __name__ == "__main__":
    main()
