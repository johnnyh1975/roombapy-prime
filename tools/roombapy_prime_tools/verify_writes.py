"""One entry point for the write operations that have no verifier yet.

WHY ONE TOOL AND NOT TWELVE.

Nine of this library's twenty-one write operations have a dedicated
verify script. The other twelve were built from decompiled APK code and
have never been sent to a real robot -- the same starting position that
made `set_virtual_wall` fail for months while looking complete.

Twelve more scripts, each with its own name and its own arguments, would
not get run. A tester wrote "it's all just Greek to me" about a single
existing tool with three stages, and he is right: the barrier is not
willingness, it is having to work out which command to type.

So: one command, a list, and a risk level per entry.

    roombapy-prime-verify-writes --list
    roombapy-prime-verify-writes set_map_name --new-name "Test"

A CHECK MUST DERIVE ITS PAYLOAD, NEVER INVENT ONE.

Both failures in the first field run came from the same mistake, and
neither was the robot's:

  - the quiet-hours check resent an empty settings object, because that
    account had no quiet hours set. HTTP 400.
  - the schedule check built a schedule from a name and nothing else --
    no robot, no days, no time, no commands. HTTP 500.

Both were reported as "this endpoint does not work". Both endpoints were
fine. A check that makes up a payload tests the server's willingness to
accept nonsense, which is not the question being asked.

So every write check here now reads the current value first and sends
that back, changed minimally or not at all. When there is nothing to
read, the check SKIPS and says why. A robot with no schedules cannot
test schedule creation, and that is an honest result rather than a
reason to guess.

WHAT IS DELIBERATELY NOT OFFERED.

`delete_map`, `reset_robot` and `reset_robot_parts` are absent, and that
is not an oversight.

A tester who accidentally deletes a map loses weeks of mapping, every
zone and every room name on it -- to confirm a command nobody wants to
use. `reset_robot_parts` takes no part argument and appears to reset all
consumable counters at once, irreversibly. `reset_robot` is a factory
reset.

The confirmation prompt is not enough protection for those. Somebody
reading a list of things to try, in a language they only half follow,
will eventually try one. The safest interface is the one that does not
list them.

They can still be called directly from the library by anyone who has
read what they do.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from ._cli import (
    add_account_arguments,
    confirm,
    connected_robot,
    require_blid,
    resolve_credentials,
    run_script,
)


def _failure_detail(exc: BaseException) -> str:
    """The exception, plus whatever the SERVER said about it.

    RestError already carries the response body in `raw_response`, and
    this layer threw it away: the message is only "HTTP 500 from <url>",
    which is what a tester pasted back after a failed run.

    An HTTP 500 with no body is nearly useless -- and the body was
    sitting in the exception object the whole time. Same shape as the
    two bugs in b5/b6: the information existed, the reporting layer did
    not show it.
    """
    detail = f"{type(exc).__name__}: {exc}"
    body = getattr(exc, "raw_response", None)
    if body:
        detail += f"\n      server said: {str(body)[:800]}"
    return detail


@dataclass(frozen=True)
class NoResult:
    """A check that ran cleanly and produced no evidence either way.

    "0 household(s) on this account" was reported as a passing check for
    two field rounds. It was neither a pass nor a failure -- the check
    had not answered its own question -- but the only two outcomes this
    tool could express were OK and FAILED, so it picked OK.

    Reuses the existing SKIPPED status rather than adding a new one:
    SKIPPED already means "this run produced no result", it is already
    counted separately from OK in Report.summary(), and a status the
    shared Report class does not know about would print as "?".
    """

    detail: str


def _indent_json(data: Any, indent: int = 3) -> str:
    """Pretty-printed JSON, indented to sit under the current output.

    Falls back to repr() for anything not JSON-serialisable: this
    function exists to make a response visible, so it must not be the
    thing that raises on an unexpected response.
    """
    import json

    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(data)
    pad = " " * indent
    return "\n".join(pad + line for line in text.splitlines())


def _raw_containers(raw: Any) -> list[Any]:
    """The `household_schedules` list, whatever shape `raw` turns out
    to be.

    FOUND IN THE b6 BUG HUNT, introduced by b6 itself: this was
    `(raw or {}).get("household_schedules")`, which raises
    AttributeError on any truthy non-dict -- a bare list, a string, an
    error envelope. The whole point of this release is that an
    unexpected response shape must be VISIBLE, and a crash here would
    take the diagnostic output down with it. The reporting path must
    never be the thing that fails.
    """
    if not isinstance(raw, dict):
        return []
    containers = raw.get("household_schedules")
    return containers if isinstance(containers, list) else []


def _raw_schedule_count(raw: Any) -> int:
    """How many schedule objects the SERVER sent, counted without using
    this project's models at all.

    The point of the cross-check is to be independent of the parser it
    is checking, so this walks the raw structure by hand.
    """
    total = 0
    for container in _raw_containers(raw):
        if isinstance(container, dict):
            total += len(container.get("schedules") or [])
    return total


def _parse_schedules(raw: Any) -> list[Any]:
    """The raw response read through this project's own models.

    SchedulesList.schedules is list[dict] -- raw dicts, NOT
    HouseholdSchedule instances. Reading `.options` off them returns
    None on every real response, which is exactly how this check came
    to report nothing. They have to be parsed explicitly.
    """
    from roombapy_prime.models.schedules_dnd import (
        HouseholdSchedule,
        SchedulesResponse,
    )

    if not isinstance(raw, dict):
        return []
    response = SchedulesResponse.from_json(raw)
    return [
        HouseholdSchedule.from_json(entry)
        for container in response.household_schedules
        for entry in container.schedules
        if isinstance(entry, dict)
    ]


@dataclass(frozen=True)
class WriteCheck:
    """One write operation a tester can try."""

    name: str
    #: "safe" -- reversible and visible in the app
    #: "risky" -- reversible only by recreating something by hand
    risk: str
    summary: str
    #: What the tester should look at afterwards. Every check has one:
    #: a write this library reports as accepted is not the same as a
    #: write that did what the tester expected, and that difference is
    #: what the whole virtual-wall investigation turned on.
    verify_by: str
    runner: Callable[..., Any]
    extra_args: tuple[str, ...] = ()


async def _set_map_name(robot: Any, args: argparse.Namespace) -> Any:
    maps = await robot.get_active_map_versions()
    if not maps:
        raise RuntimeError("this robot has no saved maps")
    p2map_id = args.p2map_id or maps[0].get("p2map_id")
    old = maps[0].get("name")
    print(f"   renaming map {p2map_id}")
    print(f"   from {old!r} to {args.new_name!r}")
    return await robot.set_map_name(p2map_id, args.new_name)


async def _set_map_orientation(robot: Any, args: argparse.Namespace) -> Any:
    maps = await robot.get_active_map_versions()
    if not maps:
        raise RuntimeError("this robot has no saved maps")
    p2map_id = args.p2map_id or maps[0].get("p2map_id")
    # RADIANS, not degrees -- the parameter is orientation_rad. Passing
    # 90 expecting a quarter turn would rotate the map by about 14 full
    # revolutions, landing somewhere arbitrary.
    print(f"   setting orientation {args.orientation} rad on map {p2map_id}")
    return await robot.set_map_orientation(p2map_id, float(args.orientation))


async def _household_id(robot: Any, args: argparse.Namespace) -> str:
    """The household id, from the argument or the account.

    Several of these calls need it, and getting it wrong is not a
    visible failure -- the request simply addresses somebody else's
    household and comes back empty.

    SO IT SAYS WHICH ONE IT PICKED. `get_household_id()` chooses the
    household containing this robot; on an account with a Classic and a
    Prime robot there is more than one to choose from, and every empty
    answer from a household-scoped endpoint has had two possible
    readings ever since. A tester's output that does not name the
    household cannot distinguish them, and that ambiguity has now cost
    three rounds.
    """
    if args.household_id:
        print(f"   household {args.household_id} (from --household-id)")
        return str(args.household_id)
    household_id = await robot.get_household_id()
    if not household_id:
        raise RuntimeError("could not determine a household id for this account")
    print(
        f"   household {household_id} (resolved from the account; override "
        "with --household-id)"
    )
    return str(household_id)


async def _set_dnd(robot: Any, args: argparse.Namespace) -> Any:
    household_id = await _household_id(robot, args)
    current = await robot.get_dnd_settings(household_id)
    print(f"   current do-not-disturb settings: {current}")
    # NOTHING SET MEANS NOTHING TO RESEND.
    #
    # @DaRealGuGu's account has no quiet hours configured, so every field
    # came back None and `status` was empty. Sending that back produced
    # HTTP 400 -- correctly, since it is not a valid settings object.
    #
    # A resend check needs something to resend. Skipping is the honest
    # outcome; inventing values would be a write, not a check.
    fields = {
        k: v for k, v in vars(current).items()
        if not k.startswith("_") and v not in (None, {}, [])
    }
    if not fields:
        print(
            "   no quiet hours are configured on this account, so there is\n"
            "   nothing to resend. Set some in the iRobot app first if you\n"
            "   want to test this one."
        )
        return None
    # RESENT FROM THE PARSED MODEL, and that is a real limitation.
    #
    # This used to read `getattr(current, "raw", None) or fields`.
    # DNDStatusResponse has no `raw` field -- it is a frozen dataclass of
    # daily_start/daily_end/ends_at/status -- so the fallback was the
    # only branch that ever ran. The line implied a fidelity this check
    # does not have.
    #
    # What that costs: anything the server sends under a key this
    # project does not model is dropped, so the "unchanged" resend is
    # subtly less complete than what came in. Exactly the failure shape
    # get_favorites_raw()'s docstring was added for. Fixing it properly
    # needs a raw DND accessor; not invented here without a reason to.
    if not confirm("Send the SAME settings back unchanged?"):
        return None
    return await robot.set_dnd_settings(household_id, fields)


async def _order_favorite(robot: Any, args: argparse.Namespace) -> Any:
    """Moves the first favourite to position 0 -- where it already is.

    order_favorite takes ONE favourite and where to put it, not a full
    ordering. So the no-op is "put the first one first", which is the
    least destructive call this endpoint allows.
    """
    favorites = await robot.get_favorites()
    if not favorites:
        raise RuntimeError("this account has no favorites to reorder")
    first = favorites[0]
    favorite_id = getattr(first, "favorite_id", None) or getattr(first, "id", None)
    if not favorite_id:
        raise RuntimeError(f"could not read a favorite id from {first!r}")
    print(f"   moving favorite {favorite_id} to position 0 -- where it already is")
    return await robot.order_favorite(str(favorite_id), insert_at=0)


#: Keys whose values are masked before the household response is
#: printed. Both are account-level identity, neither is needed to
#: answer the question this check asks, and the output is meant to be
#: pasted into a public issue.
#:
#: Deliberately short. Everything else -- household_id,
#: household_robots[].robot_id, household_name -- IS the evidence: the
#: open question is which household holds which robot on a mixed
#: account, and masking the identifiers would remove the answer along
#: with the risk. The tool already prints BLIDs unmasked in its header.
_HOUSEHOLD_MASK_KEYS = frozenset({"owner_cognito_id", "household_users"})


def _masked(data: Any) -> Any:
    """Recursively masks _HOUSEHOLD_MASK_KEYS. Structure is preserved:
    a masked key still shows that it was present, which is itself a
    fact about the response shape."""
    if isinstance(data, dict):
        return {
            k: ("[MASKED]" if k.lower() in _HOUSEHOLD_MASK_KEYS else _masked(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_masked(item) for item in data]
    return data


def _household_entries(raw: Any) -> list[dict[str, Any]]:
    """The household dicts in a get_user_households() response.

    Handles exactly the two shapes PrimeRobot.get_household_id() already
    handles -- a bare list, or a single household as a top-level dict --
    and nothing else. No guessing at wrapper keys: an unrecognised shape
    is fully visible in the raw output printed above this, which is a
    better outcome than a plausible-looking guess that silently returns
    an empty list. That guess is the bug this check is being fixed for.
    """
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict) and ("household_id" in raw or "household_robots" in raw):
        return [raw]
    return []


def _schedule_line(index: int, schedule: Any) -> str:
    options = schedule.options
    start = options.start
    return (
        f"       [{index}] schedule_id={schedule.schedule_id!r} "
        f"enabled={options.enabled} "
        f"frequency={options.frequency} "
        f"start={None if start is None else (start.day, start.hour, start.min)}"
    )


async def _list_schedules(robot: Any, args: Any) -> Any:
    """Every household on the account, and what schedules each holds.

    WHY THIS EXISTS. A tester whose schedules demonstrably fire -- his
    missions carry `initiator: cloud` -- was told by two separate code
    paths that he has none. Both call get_schedules() with one household
    id, and an empty answer cannot distinguish "no schedules" from
    "wrong household".

    His account has a Classic and a Prime robot, and picking the right
    household on a mixed account has gone wrong in this project before.

    So: ask every household, print what each returns. If one of them has
    the schedule, that is the answer. If none does, the endpoint is not
    where these schedules live and that is a different finding.

    WHY IT PRINTS RAW JSON. The first version of this check did all
    three of its reads with getattr() against values that are plain
    dicts -- the households list, each household, and each schedule
    (SchedulesList.schedules is list[dict], as its own docstring says).
    Every one of those returns None on a dict, so the check reported
    "0 household(s) on this account" for every account in existence,
    and never called get_schedules() at all. It reported that as a
    passing check.

    The tester's output was byte-identical to what a working account
    produces. A whole field round produced no information, and the
    round before it had ended the same way.

    So the raw server response is printed FIRST, before anything in
    this project touches it, and the parsed reading is printed after it
    as a cross-check. When the two disagree, the disagreement is the
    finding -- and it is legible without another round trip to the
    tester.
    """
    raw_households = await robot.get_user_households()
    print("   raw get_user_households() response:")
    print(_indent_json(_masked(raw_households)))

    entries = _household_entries(raw_households)
    print(f"   {len(entries)} household(s) recognised in that response")
    if raw_households and not entries:
        print(
            "   NOTE: the response above is not empty but no household could be\n"
            "   read out of it. That is a parsing gap in this tool, not an empty\n"
            "   account -- the raw response above is the finding."
        )

    summary: list[dict[str, Any]] = []
    for household in entries:
        household_id = household.get("household_id") or household.get("id")
        if not household_id:
            print("     (a household entry with no household_id -- see raw above)")
            summary.append({"household_id": None, "error": "no household_id in entry"})
            continue

        print(f"\n     household {household_id}:")
        try:
            raw_schedules = await robot.get_schedules_raw(str(household_id))
        except Exception as exc:  # noqa: BLE001
            print(f"       {type(exc).__name__}: {exc}")
            summary.append({"household_id": str(household_id), "error": str(exc)})
            continue

        print("       raw get_schedules() response:")
        print(_indent_json(raw_schedules, indent=7))

        parsed = _parse_schedules(raw_schedules)
        print(f"       parsed: {len(parsed)} schedule(s)")
        for i, schedule in enumerate(parsed):
            print(_schedule_line(i, schedule))

        raw_count = _raw_schedule_count(raw_schedules)
        if raw_count != len(parsed):
            print(
                f"       DISAGREEMENT: the raw response above contains {raw_count} "
                f"schedule object(s),\n"
                f"       but this project parsed {len(parsed)}. The parser is wrong, "
                "not the account."
            )

        summary.append({
            "household_id": str(household_id),
            "raw_schedule_count": raw_count,
            "parsed_schedule_count": len(parsed),
        })

    if not summary:
        return NoResult(
            "no household could be read from this account -- see the raw "
            "response above; this is not the same as having no schedules"
        )
    if all(entry.get("raw_schedule_count") == 0 for entry in summary):
        return NoResult(
            f"{len(summary)} household(s) queried, none returned a schedule. "
            "If the app shows one, this endpoint is not where it lives"
        )
    return summary


def _raw_automation_count(raw: Any) -> int:
    """How many objects the SERVER sent, counted without the parser.

    Deliberately independent of parse_automations(): the point of a
    cross-check is to disagree with the thing it checks. Nobody has seen
    a response, so the container is unknown -- a bare list and a dict
    wrapping one are equally likely -- and this counts dicts in either
    rather than assuming a key name.
    """
    # ELEMENTS, not dicts. Counting only dicts was the first version and
    # it defeated the purpose: a list of two things this project cannot
    # read would have counted 0 against 0 parsed, reported no
    # disagreement, and passed as "an account with no automations".
    #
    # Found by the guard in tools/tests that enforces exactly this rule
    # -- which is the point of having it rather than remembering.
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, list):
                return len(value)
    return 0


async def _automations(robot: Any, args: Any) -> Any:
    """Third-party triggers and geofencing, if the endpoint is alive.

    NOT schedules -- a separate subsystem. The app has hard-coded
    service ids for August Home, Ecobee, Leviton, MyQ and Wyze, plus
    geofencing keys and behaviour options (continue cleaning, pause and
    notify, end job). Automations of the kind "when I leave the house,
    run favourite X".

    THE OPEN QUESTION IS WHETHER THE URL RESPONDS AT ALL. In the app it
    is a dead constant: one reference, a static initialiser, no reader
    -- the same signature as two other strings that turned out to be
    dead. Its real path is behind the native boundary. A second Home
    Assistant integration calls this URL, but swallows any error and
    never reads the result, so a 404 would be invisible there -- that is
    not evidence the endpoint answers.

    One read on an account with automations configured settles it. A
    404 closes the topic for good, which is worth as much as a success.
    """
    print("   GET /v1/user/automations")
    print("   (dead constant in the app -- this call is the test)")
    try:
        raw = await robot.get_automations_raw()
    except Exception as exc:  # noqa: BLE001
        print(f"       {_failure_detail(exc)}")
        return NoResult(
            f"the endpoint did not answer: {type(exc).__name__}. A refusal here "
            "closes the question rather than leaving it open"
        )

    print("   raw response:")
    print(_indent_json(raw))

    from roombapy_prime.models import parse_automations  # noqa: PLC0415

    parsed = parse_automations(raw)
    print(f"   parsed: {len(parsed)} automation(s)")
    for entry in parsed:
        print(f"     {entry.automation_id}: type={entry.automation_type} "
              f"enabled={entry.enabled} favorite={entry.favorite_id}")

    # COUNTED WITHOUT THE PARSER, so a shape we do not recognise cannot
    # pass as an empty account. That confusion is the whole reason this
    # tool prints raw responses at all: for three field rounds a derived
    # zero hid the fact that nothing had been read.
    #
    # The container is unknown here -- the endpoint has never answered
    # anyone -- so this counts dicts in whatever list it finds rather
    # than assuming a key.
    raw_count = _raw_automation_count(raw)
    if raw_count != len(parsed):
        print(f"   DISAGREEMENT: the raw response holds {raw_count} object(s),\n"
              f"   this project parsed {len(parsed)}. The parser is wrong, not "
              "the account.")
        return NoResult(
            f"the endpoint answered with {raw_count} object(s) that this project "
            "could not read -- the raw response above is the finding"
        )

    if not parsed:
        return NoResult(
            "the endpoint answered and held nothing readable -- either an account "
            "with no automations, or a shape with no objects in it at all; the raw "
            "response above tells them apart"
        )
    return [{"automations": len(parsed)}]


async def _clean_score(robot: Any, args: Any) -> Any:
    """Per-room cleanliness values, if the request body is right.

    The endpoint and its response keys are confirmed from the APK:
    `clean_scores[].regions[]`, each region carrying `clean_score` (a
    float from 0.0 to 1.0), `region_id` and `updated_ts`. Accumulated
    state per room rather than a mission result -- the data behind
    Smart Clean.

    WHAT IS NOT CONFIRMED IS THE REQUEST BODY. The app calls this
    through fetchCleanScoreDataForMap(), so the body sits behind the
    native boundary exactly as /v1/time-estimates did. There it turned
    out to be a single `{"robot_id": ...}`; the method name here names a
    map, so `{"p2map_id": ...}` is the analogous guess.

    Which is why this check prints the body it sends and the raw
    response it gets. A 4xx naming a field would tell us more than a
    success would, and either beats a status code on its own -- three
    field rounds have already ended that way.
    """
    maps = await robot.get_active_map_versions()
    entries = maps if isinstance(maps, list) else []
    if not entries:
        return NoResult("no maps on this robot, so there is nothing to score")

    summary: list[dict[str, Any]] = []
    for entry in entries:
        p2map_id = entry.get("p2map_id") if isinstance(entry, dict) else None
        if not p2map_id:
            continue
        print(f"\n   map {p2map_id}")
        print(f"   GET /v1/p2maps/clean-score?p2map_id={p2map_id}")
        try:
            raw = await robot.get_clean_score_raw(str(p2map_id))
        except Exception as exc:  # noqa: BLE001
            print(f"       {_failure_detail(exc)}")
            summary.append({"p2map_id": str(p2map_id), "error": str(exc)})
            continue
        print("       raw response:")
        print(_indent_json(raw, indent=7))

        # Parsed alongside the raw, never instead of it. The keys are
        # confirmed from the app's own response parser, so a mismatch
        # here means either the server changed or the confirmation was
        # misread -- and either is worth seeing in the same run rather
        # than a round later.
        from roombapy_prime.models import CleanScoreResponse  # noqa: PLC0415

        parsed = CleanScoreResponse.from_json(raw)
        rooms = [r for entry in parsed.clean_scores for r in entry.regions]
        print(f"       parsed: {len(rooms)} room score(s)")
        for room in rooms:
            print(f"         region {room.region_id}: {room.clean_score}"
                  f"  (updated {room.updated_ts})")
        raw_rooms = sum(
            len(entry.get("regions") or [])
            for entry in ((raw or {}).get("clean_scores") or [])
            if isinstance(entry, dict)
        ) if isinstance(raw, dict) else 0
        if raw_rooms != len(rooms):
            print(f"       DISAGREEMENT: the raw response holds {raw_rooms} region(s),\n"
                  f"       this project parsed {len(rooms)}. The parser is wrong.")
        summary.append({
            "p2map_id": str(p2map_id),
            "raw_room_count": raw_rooms,
            "parsed_room_count": len(rooms),
        })

    if not summary:
        return NoResult("no map carried a p2map_id to ask about")
    if all("error" in entry for entry in summary):
        return NoResult(
            "every map was rejected -- the request body is the likely reason, "
            "and the server's own words are above"
        )
    return summary


async def _create_and_delete_schedule(robot: Any, args: argparse.Namespace) -> Any:
    """Creates a schedule and offers to delete it again.

    Paired on purpose. Creating alone leaves a stray entry in the
    tester's app that they have to find and remove; deleting alone needs
    a schedule they already care about.
    """
    household_id = await _household_id(robot, args)

    # BUILT FROM AN EXISTING SCHEDULE, not from scratch.
    #
    # A first version sent ScheduleOptions(name=..., enabled=False),
    # which serialises to exactly two fields. No asset_id, no frequency,
    # no start time, no commands -- a schedule that says nothing about
    # which robot, on which days, at what time, to do what.
    #
    # The server answered HTTP 500 (@DaRealGuGu). That was reported as
    # "create_schedules does not work", and it is not: the request shape
    # was fine and the CONTENT was meaningless.
    #
    # Same mistake as the quiet-hours check, which resent an empty
    # settings object and got HTTP 400. Both were my checks inventing a
    # payload rather than deriving one from what the robot already has.
    #
    # So this copies an existing schedule, changes the name, and marks it
    # disabled. A robot with no schedules cannot run this check -- which
    # is the honest outcome, not a reason to invent one.
    try:
        raw = await robot.get_schedules_raw(household_id)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not read existing schedules: {exc}") from exc

    # PARSED, NOT READ OFF THE DICT.
    #
    # SECOND OCCURRENCE, found by sweeping for the first (b5). This read
    # `getattr(schedule, "options", None)` -- and SchedulesList.schedules
    # is list[dict], so that returned None for every schedule that has
    # ever existed. `template` was always None, so this check ALWAYS took
    # the branch below and told the tester he had no schedules.
    #
    # It is the second of the "two separate code paths" that told one
    # tester he had none. b5 fixed the other one and improved this
    # branch's WORDING, which made the message clearer while leaving it
    # just as wrong.
    parsed = _parse_schedules(raw)
    template = parsed[0].options if parsed else None

    if template is None:
        # SAY WHAT WAS ASKED, not just what came back.
        #
        # A tester with schedules that demonstrably fire got told he had
        # none. The message was worse than useless: it sent him to
        # create a schedule he already had.
        #
        # The endpoint is per-HOUSEHOLD, so an empty response can mean
        # "no schedules" or "wrong household" -- and on an account with
        # both a Classic and a Prime robot, picking the right one has
        # bitten this project before. Printing the id and the raw shape
        # lets a report distinguish them instead of guessing.
        containers = _raw_containers(raw)
        raw_count = _raw_schedule_count(raw)
        print(
            f"   get_schedules() returned {len(containers)} container(s) for\n"
            f"   household {household_id}, holding {raw_count} schedule(s).\n"
        )
        if raw_count:
            # The server sent schedules and none of them parsed. That is
            # this project's bug, and saying "you have no schedules"
            # would report it as the account's.
            print(
                "   None of them could be read. The server sent schedules and\n"
                "   this tool failed to parse them -- that is a bug here, not\n"
                "   something wrong with your account. Please report this\n"
                "   output.\n"
            )
            return None
        print(
            "   That is either an account with no schedules, or the wrong\n"
            "   household -- this endpoint is per-household, and an account\n"
            "   with more than one robot can have more than one.\n"
            "\n"
            "   If the iRobot app DOES show a schedule for this robot, that\n"
            "   is a finding worth reporting rather than a reason to create\n"
            "   another one. Re-run with --household-id to try a different\n"
            "   household."
        )
        return None

    from dataclasses import replace  # noqa: PLC0415

    # FIELDS DELIBERATELY DROPPED FROM THE COPY.
    #
    # `created_time` is assigned by the server. Copying a template means
    # replaying the ORIGINAL schedule's timestamp into a create -- in
    # @DaRealGuGu's b7 run, literally "2026-08-01T18:15:09.211030+00:00"
    # from the schedule being copied. That is now the last candidate
    # standing for the HTTP 500 that create has returned on every
    # attempt:
    #
    #   {"errorType": "AspenError.InternalError",
    #    "errorMessage": "Internal error"}
    #
    # A server crash rather than a validation error, which fits a value
    # the server expected to assign itself.
    #
    # The other two candidates are out. `initiator` is not needed: his
    # working, server-stored schedules do not carry it either. And
    # `is_smart_clean_fav`, which the server sends and this project does
    # not model, does not exist anywhere in the iRobot APK -- the server
    # added it without its own app knowing about it, so its absence from
    # a request cannot plausibly crash the server.
    #
    # If this run still returns 500, `created_time` is cleared too and
    # the next question is the endpoint itself rather than the payload.
    dropped = {"created_time": template.created_time}
    options = replace(
        template, name=args.schedule_name, enabled=False, created_time=None
    )
    print("   copied from an existing schedule, disabled, renamed")
    # PRINTED, so a failing run stays readable without another round
    # trip. Three field rounds on this one check have already ended with
    # a status code and no way to tell what was actually sent.
    # Only report a field as dropped if it was actually there. Found in
    # the b8 bug hunt: a template carrying no created_time still printed
    # "deliberately NOT sent: created_time = None", claiming an omission
    # that never happened. For output whose whole purpose is to stay
    # readable after a failure, that is the wrong kind of inaccuracy.
    actually_dropped = {k: v for k, v in dropped.items() if v is not None}
    if actually_dropped:
        print("   deliberately NOT sent (server-assigned):")
        for key, value in actually_dropped.items():
            print(f"     {key} = {value!r}")
    else:
        print("   (template carried no server-assigned fields to drop)")
    print(f"   creating a DISABLED schedule named {args.schedule_name!r}")

    # THE BODY THAT IS ABOUT TO GO OUT, printed before it goes.
    #
    # @chairstacker's run got HTTP 500 here, and neither he nor this
    # project could say what was in the request -- so the round produced
    # a status code and nothing else. Two candidates were arguable from
    # the code alone (a copied `created_time`, which the server assigns,
    # and region commands missing `initiator`, which the app adds at
    # send time and stored schedules do not carry) and there was no way
    # to tell them apart without guessing.
    #
    # Printing it costs nothing and ends that class of round. Same
    # reasoning as the raw response in `schedules`: show the wire, do
    # not infer it.
    print("   request body:")
    print(_indent_json(options.to_json(), indent=5))

    created = await robot.create_schedules(household_id, [options])
    print(f"   created: {created}")

    if confirm("Delete the schedule that was just created?"):
        schedule_id = (created or {}).get("household_schedule_id")
        if schedule_id:
            await robot.delete_schedule(household_id, str(schedule_id))
            print("   deleted again")
        else:
            print(
                "   no household_schedule_id in the response -- please remove "
                "the schedule in the iRobot app by hand"
            )
    return created


async def _time_estimates(robot: Any, args: argparse.Namespace) -> Any:
    """Reads per-room time estimates. A GET in everything but method.

    Listed here despite being read-only because this file is where a
    tester looks for "things to try", and the response shape is the one
    thing still unknown about this endpoint. The request body was traced
    from native code -- a single {"robot_id": blid} -- but nobody has
    seen what comes back.
    """
    result = await robot.get_time_estimates()
    print("   response:")
    print(f"   {result}")
    return result


CHECKS: tuple[WriteCheck, ...] = (
    WriteCheck(
        name="schedules",
        risk="read",
        summary="lists every household and the schedules in each (writes nothing)",
        verify_by=(
            "nothing to check in the app -- please paste the output if your "
            "app shows a schedule this does not"
        ),
        runner=lambda robot, args: _list_schedules(robot, args),
    ),
    WriteCheck(
        name="automations",
        risk="read",
        summary="third-party triggers and geofencing -- NOT schedules (writes nothing)",
        verify_by=(
            "please paste the whole output. This endpoint looks dead in the app, "
            "so a refusal is as useful as a success -- it closes the question. "
            "Most useful from an account that has automations set up in the app"
        ),
        runner=lambda robot, args: _automations(robot, args),
    ),
    WriteCheck(
        name="clean_score",
        risk="read",
        summary="per-room cleanliness values from an endpoint we have never called (writes nothing)",
        verify_by=(
            "nothing to check in the app -- please paste the whole output, "
            "including a failure: the request body is a guess and a rejection "
            "naming a field would tell us more than a success"
        ),
        runner=lambda robot, args: _clean_score(robot, args),
    ),
    WriteCheck(
        name="time_estimates",
        risk="read",
        summary="reads per-room time estimates (writes nothing)",
        verify_by=(
            "nothing to check in the app -- please paste the response, it is "
            "the last unknown about this endpoint"
        ),
        runner=_time_estimates,
    ),
    WriteCheck(
        name="set_map_name",
        risk="safe",
        summary="renames a map",
        verify_by="open the iRobot app and look at the map's name",
        runner=_set_map_name,
        extra_args=("--new-name", "--p2map-id"),
    ),
    WriteCheck(
        name="set_map_orientation",
        risk="safe",
        summary=(
            "rotates how a map is displayed. --orientation takes RADIANS: "
            "0 leaves it as is, 1.5708 is a quarter turn, 3.1416 is upside down"
        ),
        verify_by="open the iRobot app; the map should appear rotated",
        runner=_set_map_orientation,
        extra_args=("--orientation", "--p2map-id"),
    ),
    WriteCheck(
        name="set_dnd_settings",
        risk="safe",
        summary="resends your existing quiet hours unchanged",
        verify_by="check quiet hours in the app -- they should be unchanged",
        runner=_set_dnd,
    ),
    WriteCheck(
        name="order_favorite",
        risk="safe",
        summary="resends your favourites in their current order",
        verify_by="check the favourites list in the app -- same order",
        runner=_order_favorite,
    ),
    WriteCheck(
        name="schedule_create_delete",
        risk="risky",
        summary="creates a schedule, then offers to delete it again",
        verify_by=(
            "watch the app: a new schedule appears, then disappears. If the "
            "delete step fails, remove it in the app by hand"
        ),
        runner=_create_and_delete_schedule,
        extra_args=("--schedule-name", "--household-id"),
    ),
)

_RISK_NOTE = {
    "read": "reads only, changes nothing",
    "safe": "reversible, and resends what is already there",
    "risky": "creates something you may have to remove by hand",
}


def _print_list() -> None:
    print("\nWrite operations that have never been tested on real hardware.\n")
    for check in CHECKS:
        args = " ".join(check.extra_args)
        print(f"  [{check.risk:5}] {check.name}")
        print(f"           {check.summary}")
        print(f"           check afterwards: {check.verify_by}")
        if args:
            print(f"           options: {args}")
        print()
    print("Run one with:")
    print("  roombapy-prime-verify-writes <name> [options]\n")
    print(
        "NOT listed, deliberately: delete_map, reset_robot and\n"
        "reset_robot_parts. Deleting a map costs weeks of mapping and every\n"
        "zone on it; the resets are irreversible. Confirming those commands\n"
        "work is not worth what it costs to find out.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Try a write operation that has no verifier yet."
    )
    add_account_arguments(parser)
    parser.add_argument("check", nargs="?", help="which operation to run")
    parser.add_argument("--list", action="store_true", help="show what can be run")
    parser.add_argument("--new-name", default="Roomba+ test")
    parser.add_argument("--p2map-id", default=None)
    parser.add_argument("--orientation", type=float, default=0.0,
                        help="in RADIANS (0 = unchanged for most maps)")
    parser.add_argument("--schedule-name", default="Roomba+ test schedule")
    parser.add_argument("--household-id", default=None)
    parser.add_argument("--i-understand-this-writes-to-my-robot", action="store_true")
    args = parser.parse_args()

    if args.list or not args.check:
        _print_list()
        return

    check = next((c for c in CHECKS if c.name == args.check), None)
    if check is None:
        print(f"Unknown check {args.check!r}. Run with --list to see the options.")
        sys.exit(1)

    # A read-only check needs no write consent. Demanding it would
    # teach the habit of passing the flag without reading why -- which
    # is precisely what makes the flag worthless on the checks that do
    # need it.
    if check.risk != "read" and not args.i_understand_this_writes_to_my_robot:
        print(
            f"Aborted: {check.name} writes to your real robot "
            f"({_RISK_NOTE[check.risk]}).\n"
            "Re-run with --i-understand-this-writes-to-my-robot once you have "
            "read what it does."
        )
        sys.exit(1)

    require_blid(args)
    username, password = resolve_credentials(args)

    async def _run() -> None:
        async with connected_robot(
            username, password, args.country_code, args.blid
        ) as (robot, report):
            print(f"\n== {check.name} ==")
            print(f"   {check.summary}")
            try:
                result = await check.runner(robot, args)
            except Exception as exc:  # noqa: BLE001
                # "FAILED", not "FAIL" -- Report.add looks the status up
                # in a dict and raises KeyError on anything else.
                #
                # The effect was worse than a typo: a check that failed
                # cleanly with an HTTP error turned into a KeyError
                # traceback, and the final summary crashed on the way out
                # too. Two of @DaRealGuGu's runs ended that way, so the
                # actual finding -- HTTP 400 and HTTP 500 from two
                # endpoints -- arrived buried under our own crash.
                report.add(check.name, "FAILED", _failure_detail(exc))
                return
            if result is None:
                report.add(check.name, "SKIPPED", "nothing was sent")
                return
            # A check that ran but answered nothing is not a pass. See
            # NoResult's own docstring for the two field rounds this
            # distinction cost.
            if isinstance(result, NoResult):
                report.add(check.name, "SKIPPED", result.detail)
                print(
                    "\nThis check did not answer its own question. That is a "
                    "result worth having,\nbut only together with the raw output "
                    f"above -- please {check.verify_by}."
                )
                return
            report.add(check.name, "OK", f"response: {result}")
            # ACCEPTED IS NOT THE SAME AS CORRECT. The virtual-wall
            # investigation turned on exactly this: a server can accept a
            # request and store something other than what was meant.
            print(f"\nAccepted. Now please {check.verify_by}.")

    run_script(_run())


if __name__ == "__main__":
    main()
