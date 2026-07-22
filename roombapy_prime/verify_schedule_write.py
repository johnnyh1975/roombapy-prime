"""Systematic, staged test package for schedule writes
(create_schedules()/update_schedules()) -- never tested live before
this script existed. Read create_schedules()/update_schedules()'s own
docstrings in prime_robot.py first.

WHY THIS HAS A DIFFERENT RISK PROFILE THAN verify_region_commands.py's
OWN STAGED APPROACH: a region command's effect is immediate and
observable -- you're watching the robot when it happens, and "stop"
always works right away. A schedule write's effect is DELAYED: a
wrong write might not manifest until some LATER time (whenever the
schedule next fires), possibly when nobody is around to notice or
react. This script's own staged approach is built around that
difference specifically.

THE STAGED APPROACH:

  Stage 1 (--update-unchanged): resend an EXISTING household's own
  schedules list, completely unchanged, via update_schedules(). Same
  philosophy as verify_region_commands.py's own stage 1 -- confirms
  the write path/schema without introducing any actual change to what
  the schedule says or when it fires.

  Stage 2 (--disable): the single safest MODIFICATION available --
  setting one specific schedule's own `enabled` field to False.
  Deliberately chosen because it can only PREVENT future unexpected
  robot activity, never cause it -- the opposite risk direction from
  almost any other possible schedule change (a changed time/day could
  cause the robot to start unexpectedly; disabling a schedule cannot).

  create_schedules() (creating a brand-new schedule from scratch) and
  any schedule TIME/day change are deliberately NOT implemented by
  this script -- both carry the risk of causing NEW, unexpected future
  robot activity, which is a fundamentally different (and worse) risk
  than this script's own two stages.

TWO SEPARATE SAFETY GATES (one fewer than verify_region_commands.py's
three -- see below for why):
  1. --i-understand-this-changes-a-real-schedule (this script's own
     wording -- "will move the robot" doesn't fit a delayed-effect
     write the same way it does an immediate mission command)
  2. An interactive y/N confirmation, showing the EXACT JSON payload
     that will be sent, immediately before sending it.

Why no third, "--i-understand-this-is-experimental-and-unconfirmed"
flag: schedule reads (get_schedules()) are already confirmed live and
well-modeled; what's unconfirmed here is specifically the WRITE
path's acceptance, not the underlying schema the way region commands'
transport itself was unconfirmed. Judged not to need a distinct
"experimental hypothesis" flag on top of the change-acknowledgment one
-- reconsider this if a live test surfaces a reason to.

WHAT TO DO IF SOMETHING LOOKS WRONG AFTERWARD: re-run --list-schedules
to see the schedule's current state, and use --update-unchanged with a
KNOWN-GOOD household_schedule_id (e.g. from before you ran --disable,
if you saved that output) to restore it. There is no live, immediate
"undo" the way "stop" is for a mission command -- this is the direct
consequence of this write's own delayed-effect nature described above.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from typing import Any

import aiohttp

from .diagnostics import Report, _extract_first_id, _try_silent
from .prime_factory import PrimeFactory


def _confirm(prompt: str) -> bool:
    """Interactive confirmation -- ONLY "j"/"ja"/"y"/"yes" (case
    doesn't matter) counts as approval, anything else (including just
    pressing Enter) aborts. Same convention as this project's other
    diagnostic scripts."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


async def _discover_household_id(robot: Any) -> str | None:
    """Same extraction approach already used in diagnostics.py --
    get_user_households()'s own response shape was never independently
    confirmed (see that method's own docstring), so this is a
    best-effort search for a plausible id key, not a guaranteed read."""
    households = await _try_silent(robot.get_user_households())
    return _extract_first_id(households, ["household_id", "householdId", "id"])


async def list_schedules(username: str, password: str, country_code: str, blid: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing. Auto-discovers
    household_id, then lists every schedule with enough detail to pick
    a target for --update-unchanged/--disable."""
    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        household_id = await _discover_household_id(robot)
        if not household_id:
            print("Could not auto-discover household_id (get_user_households()'s response shape is unconfirmed).")
            return
        print(f"household_id={household_id!r}")
        response = await robot.get_schedules(household_id)

    if not response.household_schedules:
        print("No schedules found for this household.")
        return

    for entry in response.household_schedules:
        print(f"\nhousehold_schedule_id={entry.household_schedule_id!r}")
        for i, raw_schedule in enumerate(entry.schedules):
            name = raw_schedule.get("options", {}).get("name") if isinstance(raw_schedule, dict) else None
            enabled = raw_schedule.get("options", {}).get("enabled") if isinstance(raw_schedule, dict) else None
            sched_id = raw_schedule.get("schedule_id") if isinstance(raw_schedule, dict) else None
            print(f"  [{i}] schedule_id={sched_id!r} name={name!r} enabled={enabled!r}")

    print(
        "\nTo resend one household's schedules unchanged: "
        "roombapy-prime-verify-schedule-write --update-unchanged HOUSEHOLD_SCHEDULE_ID "
        "--i-understand-this-changes-a-real-schedule"
    )
    print(
        "To disable one specific schedule: "
        "roombapy-prime-verify-schedule-write --disable HOUSEHOLD_SCHEDULE_ID --schedule-index N "
        "--i-understand-this-changes-a-real-schedule"
    )


async def _confirm_show_send(robot: Any, household_id: str, household_schedule_id: str, schedules, report: Report, description: str) -> None:
    payload = [s.to_json() for s in schedules]
    print(f"\n{description}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if not _confirm("\nSend this EXACT payload now? This changes a real schedule."):
        print("Aborted by user -- nothing sent.")
        return

    print("\n== Sending ==")
    result = await robot.update_schedules(household_id, household_schedule_id, schedules)
    report.add("update_schedules()", "OK", f"response: {result!r}")


async def send_update_unchanged(
    username: str, password: str, country_code: str, blid: str, household_schedule_id: str,
) -> None:
    from .models.schedules_dnd import HouseholdSchedule

    report = Report()
    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")

        household_id = await _discover_household_id(robot)
        if not household_id:
            print("Could not auto-discover household_id -- aborting.")
            return

        print("\n== Fetching schedules ==")
        response = await robot.get_schedules(household_id)
        entry = next(
            (e for e in response.household_schedules if e.household_schedule_id == household_schedule_id), None
        )
        if entry is None:
            print(f"ERROR: no household_schedule_id={household_schedule_id!r} found.")
            return

        schedules = [HouseholdSchedule.from_json(s) if isinstance(s, dict) else s for s in entry.schedules]

        await _confirm_show_send(
            robot, household_id, household_schedule_id, schedules, report,
            f"household_schedule_id={household_schedule_id!r} -- EXACTLY as stored, nothing modified:",
        )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")


def _build_disabled_schedules(schedules: list, schedule_index: int):
    """Stage 2's core logic, pulled out of the async I/O so it's
    directly unit-testable -- same lesson as
    verify_region_commands.py's own _build_modified_command(): an
    executing test catches real construction bugs a syntax check
    can't. Returns (new_schedules_list, was_enabled) -- does not
    mutate the input list."""
    import dataclasses

    new_schedules = list(schedules)
    target = new_schedules[schedule_index]
    was_enabled = target.options.enabled
    new_options = dataclasses.replace(target.options, enabled=False)
    new_schedules[schedule_index] = dataclasses.replace(target, options=new_options)
    return new_schedules, was_enabled


async def send_disable(
    username: str, password: str, country_code: str, blid: str, household_schedule_id: str, schedule_index: int,
) -> None:
    from .models.schedules_dnd import HouseholdSchedule

    report = Report()
    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")

        household_id = await _discover_household_id(robot)
        if not household_id:
            print("Could not auto-discover household_id -- aborting.")
            return

        print("\n== Fetching schedules ==")
        response = await robot.get_schedules(household_id)
        entry = next(
            (e for e in response.household_schedules if e.household_schedule_id == household_schedule_id), None
        )
        if entry is None:
            print(f"ERROR: no household_schedule_id={household_schedule_id!r} found.")
            return
        if schedule_index >= len(entry.schedules):
            print(f"ERROR: household_schedule_id={household_schedule_id!r} has no schedules[{schedule_index}].")
            return

        schedules = [HouseholdSchedule.from_json(s) if isinstance(s, dict) else s for s in entry.schedules]
        schedules, was_enabled = _build_disabled_schedules(schedules, schedule_index)
        target = schedules[schedule_index]

        await _confirm_show_send(
            robot, household_id, household_schedule_id, schedules, report,
            f"household_schedule_id={household_schedule_id!r} schedules[{schedule_index}] "
            f"(schedule_id={target.schedule_id!r}) enabled: {was_enabled!r} -> False:",
        )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Staged test package for schedule writes (create_schedules()/update_schedules()). "
            "See this module's own docstring for the full staged-risk explanation before using "
            "--update-unchanged or --disable."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", required=True, help="The exact target device -- no 'first device found'.")
    parser.add_argument(
        "--list-schedules", action="store_true",
        help="Stage 0: list schedules for this account. Sends nothing.",
    )
    parser.add_argument(
        "--update-unchanged", metavar="HOUSEHOLD_SCHEDULE_ID", default=None,
        help="Stage 1: resend this household's own schedules unchanged.",
    )
    parser.add_argument(
        "--disable", metavar="HOUSEHOLD_SCHEDULE_ID", default=None,
        help="Stage 2: disable one specific schedule (the safest possible modification).",
    )
    parser.add_argument(
        "--schedule-index", type=int, default=0,
        help="Which schedules[N] within --disable's household to target (default: 0).",
    )
    parser.add_argument("--i-understand-this-changes-a-real-schedule", action="store_true")
    args = parser.parse_args()

    username = args.username or input("Prime account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Prime account password: ")

    if args.list_schedules:
        asyncio.run(list_schedules(username, password, args.country_code, args.blid))
        return

    if args.update_unchanged:
        if not args.i_understand_this_changes_a_real_schedule:
            print("Aborted: --i-understand-this-changes-a-real-schedule is missing.")
            sys.exit(1)
        asyncio.run(
            send_update_unchanged(username, password, args.country_code, args.blid, args.update_unchanged)
        )
        return

    if args.disable:
        if not args.i_understand_this_changes_a_real_schedule:
            print("Aborted: --i-understand-this-changes-a-real-schedule is missing.")
            sys.exit(1)
        asyncio.run(
            send_disable(
                username, password, args.country_code, args.blid, args.disable, args.schedule_index,
            )
        )
        return

    print("Nothing to do -- pass --list-schedules (safe, sends nothing), --update-unchanged, or --disable.")


if __name__ == "__main__":
    main()
