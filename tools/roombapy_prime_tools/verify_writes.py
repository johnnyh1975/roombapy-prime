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
        print(
            "   this account has no existing schedule to copy, and a schedule\n"
            "   built from nothing is rejected by the server -- it carries no\n"
            "   robot, no days, no time and no commands. Create one in the\n"
            "   iRobot app first if you want to test this."
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
            report.add(check.name, "OK", f"response: {result}")
            # ACCEPTED IS NOT THE SAME AS CORRECT. The virtual-wall
            # investigation turned on exactly this: a server can accept a
            # request and store something other than what was meant.
            print(f"\nAccepted. Now please {check.verify_by}.")

    run_script(_run())


if __name__ == "__main__":
    main()
