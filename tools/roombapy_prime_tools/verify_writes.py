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


def _raw_schedule_count(raw: Any) -> int:
    """How many schedule objects the SERVER sent, counted without using
    this project's models at all.

    The point of the cross-check is to be independent of the parser it
    is checking, so this walks the raw structure by hand.
    """
    if not isinstance(raw, dict):
        return 0
    total = 0
    for container in raw.get("household_schedules") or []:
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
    """
    if args.household_id:
        return str(args.household_id)
    household_id = await robot.get_household_id()
    if not household_id:
        raise RuntimeError("could not determine a household id for this account")
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
    raw = getattr(current, "raw", None) or fields
    if not confirm("Send the SAME settings back unchanged?"):
        return None
    return await robot.set_dnd_settings(household_id, raw)


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
        response = await robot.get_schedules(household_id)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not read existing schedules: {exc}") from exc

    template = None
    for container in getattr(response, "household_schedules", None) or []:
        for schedule in getattr(container, "schedules", None) or []:
            if getattr(schedule, "options", None) is not None:
                template = schedule.options
                break
        if template is not None:
            break

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
        containers = getattr(response, "household_schedules", None) or []
        print(
            f"   get_schedules() returned {len(containers)} container(s) for\n"
            f"   household {household_id}, with no schedule inside.\n"
            "\n"
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

    options = replace(template, name=args.schedule_name, enabled=False)
    print("   copied from an existing schedule, disabled, renamed")
    print(f"   creating a DISABLED schedule named {args.schedule_name!r}")
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
                report.add(check.name, "FAILED", f"{type(exc).__name__}: {exc}")
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
