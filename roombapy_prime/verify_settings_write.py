"""Systematic test package for RobotSettings writes (set_setting()) --
whether toggling a specific setting has real, observable effect on the
robot, not just whether the write is accepted by the server.

CONTEXT: set_setting() itself, as a generic write mechanism, is
already confirmed to produce a real, accepted response -- carpet_boost's
own switch in ha_roomba_plus has shipped using exactly this mechanism.
What's NOT confirmed is whether flipping any GIVEN field's value
causes a real change in robot behavior -- carpet_boost itself shipped
with this exact caveat unresolved ("write mechanism confirmed, effect
on the robot not" per that switch's own docstring).

FIVE TARGET SETTINGS, all confirmed real fields via a live capture
(RobotSettings, models/robot_info.py) but with UNCONFIRMED real-world
behavioral effect: child_lock, eco_charge, sched_hold, no_auto_passes,
vac_high.

SPECIAL CASE -- sched_hold: this field exists in TWO PLACES --
rw-settings.schedHold (RobotSettings) AND the classic/unnamed shadow's
OWN schedHold (ClassicShadowState) -- confirmed via a real capture
(chairstacker) to update independently of each other, not necessarily
in sync (different metadata timestamps for the two, in the one real
capture seen so far). This script's --toggle sched_hold specifically
re-fetches BOTH sources after the write, to surface whether they move
together or diverge -- the actual open question raised this session,
not just "does set_setting() work at all".

STAGED APPROACH:

  Stage 0 (--list-settings): read-only, sends nothing. Shows current
  values of all five target settings from rw-settings, plus BOTH
  schedHold sources side by side for the sched_hold-specific
  cross-check described above.

  Stage 1 (--toggle KEY): flips ONE named setting to its opposite
  boolean value, shows the exact payload, asks for confirmation, sends
  it, then reads back rw-settings (and, for sched_hold specifically,
  the classic/unnamed shadow too) to show whether the write actually
  stuck. This is READ-BACK confirmation only, NOT confirmation of real
  robot BEHAVIOR -- does the child lock actually engage? does the
  schedule actually pause? Still needs a human present to observe the
  robot directly and report back separately; this script cannot see
  that on its own.

  Running --toggle KEY a second time reverts it: the toggle always
  flips to whatever is NOT currently set, so two consecutive runs
  return the setting to its original value. No separate --revert
  needed -- deliberately kept this simple rather than adding another
  flag/code path for something the existing one already does.

WHY THIS IS A DIFFERENT RISK PROFILE THAN verify_region_commands.py:
toggling a boolean setting has no immediate physical effect the way a
mission command does -- nothing starts moving. The risk here is
subtler and slower: a setting silently NOT taking effect while the
user believes it did (child_lock believed engaged, isn't; sched_hold
believed paused, schedule fires anyway regardless). That's exactly why
this script's read-back step exists, and why it explicitly does not
claim to confirm real robot behavior on its own -- only that the
write was accepted and (for sched_hold) whether both sources agree.

TWO SAFETY GATES (same reasoning as verify_schedule_write.py -- the
underlying set_setting() mechanism itself is already confirmed working
via carpet_boost, so no separate "experimental transport" flag is
needed on top of the change-acknowledgment one):
  1. --i-understand-this-changes-a-real-setting
  2. An interactive y/N confirmation showing the exact payload,
     immediately before sending it.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

import aiohttp

from .diagnostics import Report
from .prime_factory import PrimeFactory

# wire key <-> RobotSettings/ClassicShadowState attribute name, for the
# five settings this script can toggle. Kept as a single source of
# truth here rather than duplicated across list/toggle -- both read
# from this same dict.
_TARGET_SETTINGS: dict[str, str] = {
    "child_lock": "childLock",
    "eco_charge": "ecoCharge",
    "sched_hold": "schedHold",
    "no_auto_passes": "noAutoPasses",
    "vac_high": "vacHigh",
}


def _confirm(prompt: str) -> bool:
    """Interactive confirmation -- ONLY "j"/"ja"/"y"/"yes" (case
    doesn't matter) counts as approval, anything else (including just
    pressing Enter) aborts. Same convention as this project's other
    diagnostic scripts."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


async def list_settings(username: str, password: str, country_code: str, blid: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing."""
    from .models import ClassicShadowState, RobotSettings

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)

        print("\n== rw-settings (the five target settings) ==")
        settings_response = await robot.get_settings()
        settings = RobotSettings.from_json(settings_response.payload["state"]["reported"])
        for attr_name, wire_key in _TARGET_SETTINGS.items():
            print(f"  {attr_name} ({wire_key}): {getattr(settings, attr_name)!r}")

        print("\n== classic/unnamed shadow's OWN schedHold (cross-check) ==")
        state_response = await robot.get_state()
        classic_state = ClassicShadowState.from_json(state_response.payload["state"]["reported"])
        print(f"  classic/unnamed.sched_hold: {classic_state.sched_hold!r}")
        if classic_state.sched_hold != settings.sched_hold:
            print(
                "  NOTE: these two values DIFFER right now -- confirmed possible "
                "(this session, real capture): the two sources update independently."
            )

    print(
        "\nTo test a toggle: roombapy-prime-verify-settings-write --toggle KEY "
        "--i-understand-this-changes-a-real-setting"
    )
    print(f"Valid KEYs: {', '.join(_TARGET_SETTINGS)}")


async def send_toggle(username: str, password: str, country_code: str, blid: str, key: str) -> None:
    """Stage 1 -- flips one setting to its opposite value. See this
    module's own docstring for the full staged-risk explanation and
    why running this twice in a row reverts the change."""
    from .models import ClassicShadowState, RobotSettings

    if key not in _TARGET_SETTINGS:
        print(f"ERROR: unknown key {key!r}. Valid keys: {', '.join(_TARGET_SETTINGS)}")
        return
    wire_key = _TARGET_SETTINGS[key]

    report = Report()
    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")

        print("\n== Fetching current value ==")
        settings_response = await robot.get_settings()
        settings = RobotSettings.from_json(settings_response.payload["state"]["reported"])
        current = getattr(settings, key)
        if current is None:
            print(f"ERROR: {key} is currently None (unexpected) -- aborting, no safe toggle direction.")
            return
        new_value = not current

        print(f"\n{key} ({wire_key}): {current!r} -> {new_value!r}")
        print(json.dumps({"key": wire_key, "value": new_value}, indent=2))

        if not _confirm("\nSend this EXACT change now? This changes a real robot setting."):
            print("Aborted by user -- nothing sent.")
            return

        print("\n== Sending ==")
        await robot.set_setting(wire_key, new_value)
        report.add("set_setting()", "OK", f"{wire_key} -> {new_value!r}")

        print("\n== Reading back rw-settings ==")
        settings_response2 = await robot.get_settings()
        settings2 = RobotSettings.from_json(settings_response2.payload["state"]["reported"])
        readback = getattr(settings2, key)
        if readback == new_value:
            report.add(f"Read-back: {key}", "OK", f"confirmed {readback!r}")
        else:
            report.add(
                f"Read-back: {key}", "FAILED",
                f"expected {new_value!r}, got {readback!r}",
            )

        if key == "sched_hold":
            print("\n== Cross-checking classic/unnamed shadow's OWN schedHold ==")
            state_response = await robot.get_state()
            classic_state = ClassicShadowState.from_json(state_response.payload["state"]["reported"])
            if classic_state.sched_hold == new_value:
                report.add(
                    "classic/unnamed.schedHold cross-check", "OK",
                    f"moved in sync: {classic_state.sched_hold!r}",
                )
            else:
                report.add(
                    "classic/unnamed.schedHold cross-check", "FAILED",
                    f"rw-settings now {new_value!r}, but classic/unnamed still shows "
                    f"{classic_state.sched_hold!r} -- these two sources do NOT necessarily "
                    "stay in sync (confirmed this session, real capture)",
                )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")
    print(
        "\nIMPORTANT: this confirms the write was accepted and, per the read-back above, "
        "whether it stuck in rw-settings -- it does NOT confirm the robot's actual physical "
        "behavior changed. Please observe the robot directly (does the child lock actually "
        "engage? does the schedule actually pause?) and report back separately. Run "
        f"--toggle {key} again to revert."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Staged test package for RobotSettings writes (set_setting()) -- whether toggling "
            "a specific setting has real, observable effect, not just whether the write is "
            "accepted. See this module's own docstring before using --toggle."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument(
        "--blid", default=os.environ.get("ROOMBAPY_PRIME_BLID"),
        help="The exact target device -- no 'first device found'. Falls back to ROOMBAPY_PRIME_BLID env var.",
    )
    parser.add_argument(
        "--list-settings", action="store_true",
        help="Stage 0: show current values of all five target settings, plus the sched_hold "
        "cross-check against the classic/unnamed shadow. Sends nothing.",
    )
    parser.add_argument(
        "--toggle", metavar="KEY", default=None,
        help=f"Stage 1: flip one setting to its opposite value. Valid KEYs: {', '.join(_TARGET_SETTINGS)}. "
        "Running this again with the same KEY reverts it.",
    )
    parser.add_argument("--i-understand-this-changes-a-real-setting", action="store_true")
    args = parser.parse_args()
    if not args.blid:
        print("Aborted: --blid is required (or set the ROOMBAPY_PRIME_BLID env var).")
        sys.exit(1)

    if not (args.list_settings or args.toggle):
        print("Nothing to do -- pass --list-settings (safe, sends nothing) or --toggle KEY.")
        return

    if args.toggle and not args.i_understand_this_changes_a_real_setting:
        print("Aborted: --i-understand-this-changes-a-real-setting is missing.")
        sys.exit(1)

    username = args.username or input("iRobot account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("iRobot account password: ")

    if args.list_settings:
        asyncio.run(list_settings(username, password, args.country_code, args.blid))
        return

    if args.toggle:
        asyncio.run(send_toggle(username, password, args.country_code, args.blid, args.toggle))
        return


if __name__ == "__main__":
    main()
