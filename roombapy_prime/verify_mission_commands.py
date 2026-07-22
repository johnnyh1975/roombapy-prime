"""Manual, observed verification of mission commands (start/stop/
pause/...) against a real Prime/V4 robot.

DELIBERATELY SEPARATE from diagnostics.py, for the same reason
diagnostics.py itself NEVER runs mission commands automatically: this
script actually moves the real robot. It exists only because there
needs to be a way to do that ONCE, deliberately, while watching -- not
to make the automated diagnostics script "safer".

SAFETY DESIGN (doubly secured, both levels are mandatory):
1. --i-understand-this-will-move-my-robot must be explicitly set at
   startup, or the script aborts immediately, before it even logs in.
2. An interactive confirmation is asked before EVERY individual
   command (not just once at the start) -- including a display of
   exactly what's about to be sent. Enter/"j" confirms, anything else
   aborts.

FLOW (deliberately conservative, not a full cleaning cycle):
  START -> brief pause, during which the robot should react -> an
  INTERACTIVE mid-mission capture (waits for you to confirm the robot
  is actually, visibly cleaning -- not a fixed sleep, see
  _capture_mid_mission_state()) -> STOP.
  Optional, asked individually: PAUSE/RESUME, DOCK.

UPDATED (session 39): sends via send_simple_command() (the
"{irbt_topic_prefix}/things/{blid}/cmd" topic), not
send_mission_command() (the shadow-update path this script itself was
the first to confirm times out with zero response against a real
account -- see prime_robot.py's send_simple_command()/
send_mission_command() docstrings for the full evidence trail). This
simpler payload has no known way to express clean_all/regions, so a
plain verb ("start"/"stop"/"pause"/"resume"/"dock") is sent instead of
a full RoutineCommand.

Before AND after every command sent, get_state() is additionally
fetched and the raw reported state is displayed. NOTE (session 57):
the "after" snapshot around Start is only ~3 seconds post-command --
enough to prove the command was accepted, NOT enough to represent a
genuinely active mission (both real accounts on record, chairstacker
and jadestar1864, show identical top-level keys between idle-before
and seconds-after-Start). The separate, interactive mid-mission
capture between Start and Stop is what actually targets an active-
mission state and diffs it against the pre-mission baseline -- that
comparison has never been made before this.

The result is summarized as a markdown report just like diagnostics.py,
including a pre-filled GitHub issue link (same redaction logic, same
warning: credentials are removed, nothing else shared automatically).

USAGE:
  roombapy-prime-verify-commands \\
      --username you@example.com --country-code US --blid BLID123 \\
      --i-understand-this-will-move-my-robot

Credentials same as diagnostics.py: ROOMBAPY_PRIME_PASSWORD env var
or interactive prompt, never as a command-line argument.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import webbrowser
from dataclasses import asdict
from typing import Any

import aiohttp

from .diagnostics import Report, _redact_raw_capture, _report_topic_prefix_status, build_issue_url, redact_aws_url_secrets
from .models import parse_robot_status_v2
from .prime_factory import PrimeFactory


def _confirm(prompt: str) -> bool:
    """Interactive confirmation -- ONLY "j"/"ja"/"y"/"yes" (case
    doesn't matter) counts as approval, anything else (including just
    pressing Enter) aborts. Deliberately restrictive -- an accidental
    Enter must never count as approval."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


async def _show_state(robot: Any, label: str) -> dict[str, Any] | None:
    """Fetches get_state() and displays the raw reported state -- a
    state during an ACTIVE mission has never been captured before, so
    this is itself new information, regardless of the test result.

    UPDATED (session 40): also attempts to parse RobotStatusV2 out of
    the reported dict (see models/robot_info.py's RobotStatusV2 section for the
    full evidence trail and the unresolved question of whether this
    structure actually lives here at all -- the one real capture
    available before this session showed no matching fields). Reports
    a clear "not found" message rather than silently saying nothing,
    since a None result here is itself a meaningful data point (either
    for "wrong location" or "not present outside an active mission").
    Returns both the raw reported dict and the parse attempt so callers
    can include both in a diagnostic capture."""
    try:
        state = await robot.get_state()
        reported = state.payload.get("state", {}).get("reported", {}) if isinstance(state.payload, dict) else {}
        print(f"\n  [{label}] get_state().reported = {redact_aws_url_secrets(str(reported))}")
        status_v2 = parse_robot_status_v2(reported)
        if status_v2 is not None:
            print(f"  [{label}] RobotStatusV2 parsed: {status_v2}")
        else:
            print(f"  [{label}] RobotStatusV2: none of the confirmed wire keys found in this dict")
        return {"reported": reported, "robot_status_v2": asdict(status_v2) if status_v2 is not None else None}
    except Exception as exc:  # noqa: BLE001
        print(f"\n  [{label}] get_state() failed: {type(exc).__name__}: {exc}")
        return None


def _diff_reported_keys(
    baseline: dict[str, Any] | None, current: dict[str, Any] | None
) -> None:
    """Prints which top-level `reported` keys are new/missing/changed
    between two _show_state() snapshots.

    NEW (session 57, in response to the still-open RobotStatusV2
    placement question -- see models/robot_info.py's RobotStatusV2 and the
    fortieth/fifty-sixth gap-analysis addenda). Every real get_state()
    capture so far, including this script's own existing before/after
    around each command, was taken within ~3 seconds of sending Start --
    both real accounts (chairstacker, jadestar1864) show IDENTICAL
    top-level keys between idle-before and seconds-after-Start, which
    is consistent with either "wrong data source entirely" or "these
    fields only populate later, once a mission is genuinely under way".
    A real diff against a snapshot taken while the robot is confirmed,
    visibly cleaning is the one comparison this project has never had.
    Printed, not just silently included in --dump-config, so whoever
    runs this interactively sees the answer immediately."""
    baseline_keys = set((baseline or {}).get("reported") or {})
    current_keys = set((current or {}).get("reported") or {})
    new_keys = current_keys - baseline_keys
    missing_keys = baseline_keys - current_keys
    common_keys = baseline_keys & current_keys
    changed_keys = {
        key
        for key in common_keys
        if (baseline or {})["reported"].get(key) != (current or {})["reported"].get(key)
    }

    print("\n  -- Diff vs. pre-mission baseline --")
    if new_keys:
        print(f"  NEW top-level keys (absent before Start): {sorted(new_keys)}")
    else:
        print("  No new top-level keys appeared -- same shape as pre-mission idle state.")
    if missing_keys:
        print(f"  Keys present before but missing now: {sorted(missing_keys)}")
    if changed_keys:
        print(f"  Existing keys whose VALUE changed: {sorted(changed_keys)}")
    else:
        print("  No existing key's value changed either.")


async def _capture_mid_mission_state(
    robot: Any,
    report: Report,
    raw_capture: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> None:
    """Waits for explicit user confirmation that the robot is ACTUALLY,
    visibly cleaning -- not a fixed sleep -- then captures get_state()
    and diffs it against the pre-mission baseline.

    NEW (session 57). Replaces the assumption that _run_command()'s
    existing 3-second before/after window around "start" was enough to
    represent an active mission: it isn't, it only proves the command
    was accepted. This is deliberately interactive rather than a longer
    fixed sleep, since "how long until a robot is genuinely cleaning"
    varies by model/room and isn't something to guess at either."""
    print(f"\n{'=' * 60}")
    print("MID-MISSION CAPTURE")
    print("The robot should now be running. Wait until you can SEE it actually")
    print("cleaning (moving, brush/vacuum sound, an on-robot or app cleaning")
    print("indicator -- whatever your model shows) before confirming below.")
    print("Take your time -- this step waits for you, there is no timeout.")
    if not _confirm("Robot is now visibly, actively cleaning -- capture state now?"):
        report.add("Mid-mission capture", "SKIPPED", "not confirmed by user")
        return

    await asyncio.sleep(2.0)  # small buffer so a just-updated shadow has settled
    mid_mission = await _show_state(robot, "Mid-mission (actively cleaning)")
    if raw_capture is not None:
        raw_capture["Mid-mission (actively cleaning)"] = mid_mission
    _diff_reported_keys(baseline, mid_mission)
    report.add(
        "Mid-mission capture", "OK",
        "captured -- see the diff above and 'Mid-mission (actively cleaning)' in --dump-config",
    )


async def _run_command(
    robot: Any,
    report: Report,
    raw_capture: dict[str, Any],
    command: str,
    label: str,
) -> bool:
    """Explicitly asks for confirmation before sending, shows the state
    before/after, and afterward asks what the user actually observed on
    the real robot. Returns True if the user confirmed it worked as
    expected.

    UPDATED (session 39): now uses send_simple_command() (the
    "{irbt_topic_prefix}/things/{blid}/cmd" path), not
    send_mission_command() (the shadow-update path this script itself
    was the first to confirm times out with zero response). See
    prime_robot.py's send_simple_command()/send_mission_command()
    docstrings for the full evidence trail. `command` is now a plain
    verb string ("start"/"stop"/"pause"/"resume"/"dock"), not a
    RoutineCommand -- this simpler payload has no known way to express
    clean_all/regions, so that option is gone from this script for
    now."""
    print(f"\n{'=' * 60}")
    print(f"NEXT COMMAND: {label} ({command})")
    print(f'About to send: {{"command": "{command}", "initiator": "localApp"}} via the cmd topic')
    if not _confirm(f'Send "{label}" to the real robot now?'):
        report.add(label, "SKIPPED", "not confirmed by user")
        return False

    before = await _show_state(robot, f"{label}: before")
    try:
        await robot.send_simple_command(command)
        print("  Command published, no error raised (this path is fire-and-forget -- no")
        print("  server acknowledgment is expected, see send_simple_command()'s docstring).")
    except Exception as exc:  # noqa: BLE001
        report.add(label, "FAILED", f"{type(exc).__name__}: {exc}")
        return False

    await asyncio.sleep(3.0)
    after = await _show_state(robot, f"{label}: after")
    if raw_capture is not None:
        raw_capture[f"{label} (before)"] = before
        raw_capture[f"{label} (after)"] = after

    observed = _confirm(f'Did the robot actually react as expected to "{label}"?')
    if observed:
        report.add(label, "OK", "confirmed by user on the real robot")
    else:
        report.add(label, "FAILED", "command published without error, but the robot did NOT react as expected")
    return observed


async def run(username: str, password: str, country_code: str, blid: str) -> tuple[Report, dict[str, Any]]:
    report = Report()
    raw_capture: dict[str, Any] = {}

    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")
        await robot.connect()
        report.add("MQTT connection", "OK")

        # NEW (session 41): a live test (chairstacker) first showed send_simple_command()
        # failing outright because irbt_topic_prefix came back None -- report this clearly
        # UP FRONT, before wasting the user's time confirming commands that will just fail
        # with the same error every time.
        _report_topic_prefix_status(report, robot)
        if raw_capture is not None:
            raw_capture["Discovery deployment object (for irbt_topic_prefix)"] = robot.deployment
        if getattr(robot, "_irbt_topic_prefix", None) is None:
            print(
                "\nirbt_topic_prefix is missing for this account -- every mission command "
                "below would fail with the same error. Skipping the rest of this run; see "
                "the report above for the actual discovery-response keys, which is what's "
                "needed to fix this properly."
            )
            for label in ("Start", "Mid-mission capture", "Stop", "Start (for pause test)", "Pause", "Resume", "Stop (after pause test)", "Dock", "Find"):
                report.add(label, "SKIPPED", "irbt_topic_prefix missing -- see report above")
            await robot.disconnect()
            return report, raw_capture

        print("\n== Core test: Start -> Stop ==")
        started = await _run_command(robot, report, raw_capture, "start", "Start")

        if started:
            await _capture_mid_mission_state(
                robot, report, raw_capture, raw_capture.get("Start (before)")
            )
            await _run_command(robot, report, raw_capture, "stop", "Stop")
        else:
            report.add("Stop", "SKIPPED", "Start was not confirmed, Stop doesn't make sense without a running mission")
            report.add("Mid-mission capture", "SKIPPED", "Start was not confirmed")

        print("\n== Optional additional tests ==")
        if _confirm("Also test Pause/Resume? (needs a freshly started mission)"):
            if await _run_command(robot, report, raw_capture, "start", "Start (for pause test)"):
                await _run_command(robot, report, raw_capture, "pause", "Pause")
                await _run_command(robot, report, raw_capture, "resume", "Resume")
                await _run_command(robot, report, raw_capture, "stop", "Stop (after pause test)")
        else:
            report.add("Pause/Resume", "SKIPPED", "not chosen by user")

        if _confirm("Also test Dock? (sends the robot back to its charging station)"):
            await _run_command(robot, report, raw_capture, "dock", "Dock")
        else:
            report.add("Dock", "SKIPPED", "not chosen by user")

        print("\n== Locate (\"find my robot\") ==")
        print(
            "CONFIRMED WORKING (jayjay, real device test): \"find\" produces a genuine, audible "
            "chime with no robot movement. Two OTHER mechanisms (a REST endpoint, a shadow "
            "write) were tried first and confirmed NOT working -- this is the one that actually "
            "works. Does NOT require an active mission -- works regardless of whether the robot "
            "is currently cleaning, docked, or idle."
        )
        if _confirm('Also test "find" (should make the robot chime)?'):
            await _run_command(robot, report, raw_capture, "find", "Find")
            print(
                "\nDid the robot actually chime? Already confirmed working on at least one real "
                "device, but still worth noting the result each time when reporting back, since "
                "device-specific differences (region, firmware, robot model) could still matter."
            )
        else:
            report.add("Find", "SKIPPED", "not chosen by user")

        await robot.disconnect()

    return report, raw_capture


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manual, observed verification of mission commands (start/stop/pause/dock) against a "
            "REAL Prime/V4 robot. Actually moves the robot -- see the module docstring for the "
            "safety design."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument(
        "--blid",
        required=True,
        help="Required (unlike diagnostics.py) -- the exact target device must be chosen "
        "deliberately, no 'first device found'.",
    )
    parser.add_argument(
        "--i-understand-this-will-move-my-robot",
        action="store_true",
        dest="confirmed",
        help="Required. Without this flag the script aborts immediately, before any login.",
    )
    parser.add_argument("--output", default=None, metavar="PATH")
    parser.add_argument("--dump-config", default=None, metavar="PATH")
    parser.add_argument("--no-issue-link", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    if not args.confirmed:
        print(
            "Aborted: --i-understand-this-will-move-my-robot is missing. This script sends "
            "REAL mission commands to a REAL device -- see the module docstring."
        )
        sys.exit(1)

    username = args.username or input("Prime account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Password: ")

    print(f"\nTARGET DEVICE: {args.blid}")
    print("This script is about to send real start/stop commands to this device.")
    if not _confirm("Continue?"):
        print("Aborted.")
        sys.exit(0)

    report, raw_capture = asyncio.run(run(username, password, args.country_code, args.blid))
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Summary: {ok} OK, {failed} failed, {skipped} skipped ==")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Report saved to {args.output}")

    if args.dump_config:
        import json

        redacted = _redact_raw_capture(raw_capture, [username, password])
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(redacted, f, indent=2, default=str, ensure_ascii=False)
        print(f"Redacted raw responses (incl. get_state() during the missions) saved to {args.dump_config}")

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Feedback for the maintainers ==")
        print("If you'd like to share this report:")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


if __name__ == "__main__":
    main()
