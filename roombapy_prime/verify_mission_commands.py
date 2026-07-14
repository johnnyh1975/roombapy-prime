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
  START (clean_all=True) -> brief pause, during which the robot should
  react -> STOP. Optional, asked individually: PAUSE/RESUME, DOCK.

Before AND after every command sent, get_state() is additionally
fetched and the raw reported state is displayed -- an active mission
state has never been captured before this (every prior real response
showed a robot with a loaded map, but not actively cleaning). That's
itself new, previously unconfirmed information.

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
from typing import Any

import aiohttp

from .diagnostics import Report, _redact_raw_capture, build_issue_url
from .models import MissionCommandType, RoutineCommand
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
    this is itself new information, regardless of the test result."""
    try:
        state = await robot.get_state()
        reported = state.payload.get("state", {}).get("reported", {}) if isinstance(state.payload, dict) else {}
        print(f"\n  [{label}] get_state().reported = {reported}")
        return reported
    except Exception as exc:  # noqa: BLE001
        print(f"\n  [{label}] get_state() failed: {type(exc).__name__}: {exc}")
        return None


async def _run_command(
    robot: Any,
    report: Report,
    raw_capture: dict[str, Any],
    command_type: MissionCommandType,
    label: str,
    clean_all: bool = False,
) -> bool:
    """Explicitly asks for confirmation before sending, shows the state
    before/after, and afterward asks what the user actually observed on
    the real robot. Returns True if the user confirmed it worked as
    expected."""
    cmd = RoutineCommand(command_type=command_type, asset_id=robot.blid, clean_all=clean_all)
    print(f"\n{'=' * 60}")
    print(f"NEXT COMMAND: {label} ({command_type.value})")
    print(f"About to send: {cmd.to_json()}")
    if not _confirm(f'Send "{label}" to the real robot now?'):
        report.add(label, "SKIPPED", "not confirmed by user")
        return False

    before = await _show_state(robot, f"{label}: before")
    try:
        await robot.send_mission_command(cmd)
        print("  Command sent, no error from the server.")
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
        report.add(label, "FAILED", "server accepted the command, but the robot did NOT react as expected")
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

        print("\n== Core test: Start -> Stop ==")
        started = await _run_command(robot, report, raw_capture, MissionCommandType.START, "Start (clean_all)", clean_all=True)

        if started:
            await _run_command(robot, report, raw_capture, MissionCommandType.STOP, "Stop")
        else:
            report.add("Stop", "SKIPPED", "Start was not confirmed, Stop doesn't make sense without a running mission")

        print("\n== Optional additional tests ==")
        if _confirm("Also test Pause/Resume? (needs a freshly started mission)"):
            if await _run_command(robot, report, raw_capture, MissionCommandType.START, "Start (for pause test)", clean_all=True):
                await _run_command(robot, report, raw_capture, MissionCommandType.PAUSE, "Pause")
                await _run_command(robot, report, raw_capture, MissionCommandType.RESUME, "Resume")
                await _run_command(robot, report, raw_capture, MissionCommandType.STOP, "Stop (after pause test)")
        else:
            report.add("Pause/Resume", "SKIPPED", "not chosen by user")

        if _confirm("Also test Dock? (sends the robot back to its charging station)"):
            await _run_command(robot, report, raw_capture, MissionCommandType.DOCK, "Dock")
        else:
            report.add("Dock", "SKIPPED", "not chosen by user")

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
