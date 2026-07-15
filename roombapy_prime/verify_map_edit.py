"""Manual, observed verification of map editing against a real Prime/V4
robot's actual, in-use map.

DELIBERATELY SEPARATE from diagnostics.py, for the same reason
diagnostics.py itself NEVER edits maps automatically, and for the same
reason verify_mission_commands.py exists as its own script rather than
being folded into diagnostics.py: this actually changes real, saved
data about a robot the person genuinely uses. It exists only because
there needs to be a way to test that ONCE, deliberately, while
watching -- not to make the automated diagnostics script "safer".

WHY THIS IS MORE CAUTIOUS THAN verify_mission_commands.py, DELIBERATELY:

Mission commands, before their own live test, had two independently
converging pieces of evidence for the correct transport (this
project's own native disassembly, and a third-party project reporting
success). Map editing (`edit_map()`, the V1 command family) has NO
such corroboration -- the exact JSON envelope around each command's
`to_v1_command_body()` output (see models.py's V1 section) is an
ANALOGY assumption from the V2 pattern, never independently confirmed
by anyone. A wrong guess for a mission command was safely observable
as "nothing happened" (confirmed: zero response, not a crash). A wrong
guess for a map edit command could, in principle, be accepted by the
server in a way that changes map data unexpectedly -- lower
probability than "cleanly rejected", but not zero, and unlike a
mission command, a botched map edit could persist and be annoying to
manually clean up in the real app afterward.

For that reason, THIS SCRIPT DELIBERATELY ONLY TESTS ONE OPERATION:
renaming an existing, already-named room to a clearly-marked test name,
and immediately renaming it back to its original name. Nothing else
from the V1 vocabulary (SplitRoom, MergeRooms, SetPermanentAreas,
DeletePermanentAreas, SetVirtualWalls, AdjustFurniture) is attempted
here -- those either aren't cleanly reversible at all (a merge/split
can't be undone by calling the inverse operation, since the original
boundary information is gone) or carry meaningfully higher risk for a
first live test. If this rename test succeeds cleanly, that's still
useful evidence about the general V1 envelope shape, but does NOT by
itself confirm any of the other command types.

SAFETY DESIGN (same doubly-secured pattern as verify_mission_commands.py):
1. --i-understand-this-will-edit-my-map must be explicitly set at
   startup, or the script aborts immediately, before it even logs in.
2. An interactive confirmation is asked before EVERY step that changes
   anything (the test rename, and separately the revert) -- including
   a display of exactly what's about to be sent, and which room
   (by its current, real name) is about to be affected.

FLOW:
  1. Fetch active map versions, list rooms that currently HAVE a name
     (a room with no name is skipped entirely for this test -- see
     _pick_test_room()'s docstring for why: there'd be no reliable way
     to revert it back to "no name" afterward).
  2. Let the user choose which named room to test on, showing the
     room_id and current name explicitly.
  3. Rename it to "{original name} [roombapy-prime-test]".
  4. Ask the user to confirm in the real app that the name actually
     changed -- an accepted HTTP response is not proof the edit had
     any real effect, only that the server didn't reject the request.
  5. Immediately rename it back to the original name, and ask for the
     same confirmation.

The result is summarized as a markdown report just like
verify_mission_commands.py, including a pre-filled GitHub issue link
and --dump-config support (same redaction logic).

USAGE:
  roombapy-prime-verify-map-edit \\
      --username you@example.com --country-code US --blid BLID123 \\
      --i-understand-this-will-edit-my-map

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

from .diagnostics import Report, _redact_raw_capture, _report_topic_prefix_status, build_issue_url
from .models import RenameRoomV1
from .prime_factory import PrimeFactory
from .verify_mission_commands import _confirm

_TEST_SUFFIX = " [roombapy-prime-test]"


def _pick_test_room(map_versions: list[Any]) -> tuple[str, str, str] | None:
    """Returns (p2map_id, room_id, current_name) for the first named
    room found across all active map versions, or None if no named
    room exists anywhere.

    Deliberately requires an EXISTING name, not just any room: this
    script's whole safety design rests on being able to revert to a
    known-good original value. RenameRoomV1's `name` field is a plain
    required string (see models.py) -- there's no confirmed way to
    "clear" a name back to none, so a room that currently has no name
    is not a safe candidate for this test at all, regardless of how
    the test itself goes."""
    for version in map_versions:
        p2map_id = getattr(version, "p2map_id", None)
        rooms = getattr(version, "rooms_metadata", None) or []
        for room in rooms:
            name = getattr(room, "name", None)
            room_id = getattr(room, "room_id", None)
            if name and room_id and p2map_id:
                return p2map_id, room_id, name
    return None


async def run(username: str, password: str, country_code: str, blid: str) -> tuple[Report, dict[str, Any]]:
    report = Report()
    raw_capture: dict[str, Any] = {}

    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")
        await robot.connect()
        report.add("MQTT connection", "OK")

        _report_topic_prefix_status(report, robot)
        raw_capture["Discovery deployment object (for irbt_topic_prefix)"] = robot.deployment

        print("\n== Finding a room to test on ==")
        try:
            map_versions = await robot.get_active_map_versions()
        except Exception as exc:  # noqa: BLE001
            report.add("Fetching active map versions", "FAILED", f"{type(exc).__name__}: {exc}")
            await robot.disconnect()
            return report, raw_capture
        report.add("Fetching active map versions", "OK", f"{len(map_versions)} map version(s) found")
        raw_capture["Active map versions"] = [getattr(v, "__dict__", str(v)) for v in map_versions]

        picked = _pick_test_room(map_versions)
        if picked is None:
            report.add(
                "Finding a named room to test on",
                "SKIPPED",
                "no room with an existing name was found across any active map version -- "
                "this test needs a room to already have a name, so it has something known-good "
                "to revert back to. Nothing was sent.",
            )
            await robot.disconnect()
            return report, raw_capture
        p2map_id, room_id, original_name = picked
        report.add(
            "Finding a named room to test on", "OK",
            f"room_id={room_id!r}, current name={original_name!r} (p2map_id={p2map_id!r})",
        )

        test_name = f"{original_name}{_TEST_SUFFIX}"
        print(f"\n{'=' * 60}")
        print(f"TEST ROOM: {original_name!r} (room_id={room_id})")
        print(f"About to temporarily rename it to: {test_name!r}")
        print("This sends a real edit_map() call -- the exact envelope format is unconfirmed, ")
        print("see the module docstring for why this is riskier than the mission-command test.")
        if not _confirm("Proceed with the rename?"):
            report.add("Rename room (test)", "SKIPPED", "not confirmed by user")
            await robot.disconnect()
            return report, raw_capture

        try:
            result = await robot.edit_map(p2map_id, RenameRoomV1(room_id=room_id, name=test_name))
            raw_capture["edit_map response (rename to test name)"] = result
            print(f"  Server response: {result}")
        except Exception as exc:  # noqa: BLE001
            report.add("Rename room (test)", "FAILED", f"{type(exc).__name__}: {exc}")
            print(
                "\nThe rename call itself failed -- this is actually useful, safe information "
                "(the envelope guess is likely wrong), not a dangerous outcome. Not attempting "
                "the revert, since nothing should have changed."
            )
            await robot.disconnect()
            return report, raw_capture

        renamed_confirmed = _confirm(
            f'Please check the real app now: does the room show as "{test_name}"? (y/n)'
        )
        if renamed_confirmed:
            report.add("Rename room (test)", "OK", f"confirmed by user in the real app: now {test_name!r}")
        else:
            report.add(
                "Rename room (test)", "FAILED",
                "server accepted the request without error, but the name did NOT actually change "
                "in the app -- the envelope is likely being silently ignored server-side",
            )

        print(f"\n{'=' * 60}")
        print(f"Reverting room_id={room_id} back to its original name: {original_name!r}")
        if not _confirm("Proceed with the revert?"):
            report.add(
                "Revert room name", "SKIPPED",
                f"NOT confirmed by user -- room_id={room_id} may still show the test name "
                f"{test_name!r}. Please revert this manually in the app.",
            )
            await robot.disconnect()
            return report, raw_capture

        try:
            result = await robot.edit_map(p2map_id, RenameRoomV1(room_id=room_id, name=original_name))
            raw_capture["edit_map response (revert to original name)"] = result
            print(f"  Server response: {result}")
        except Exception as exc:  # noqa: BLE001
            report.add(
                "Revert room name", "FAILED",
                f"{type(exc).__name__}: {exc} -- room_id={room_id} may still show the test name "
                f"{test_name!r}. Please revert this manually in the app.",
            )
            await robot.disconnect()
            return report, raw_capture

        reverted_confirmed = _confirm(
            f'Please check the real app again: is the room back to "{original_name}"? (y/n)'
        )
        if reverted_confirmed:
            report.add("Revert room name", "OK", f"confirmed by user in the real app: back to {original_name!r}")
        else:
            report.add(
                "Revert room name", "FAILED",
                f"server accepted the revert without error, but the app does NOT show "
                f"{original_name!r} -- room_id={room_id} may need manual correction in the app",
            )

        await robot.disconnect()

    return report, raw_capture


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manual, observed verification of map editing (room rename only, see the module "
            "docstring for why this is deliberately narrow) against a REAL Prime/V4 robot's "
            "REAL, in-use map."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument(
        "--blid",
        required=True,
        help="Required -- the exact target device must be chosen deliberately, no 'first device found'.",
    )
    parser.add_argument(
        "--i-understand-this-will-edit-my-map",
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
            "Aborted: --i-understand-this-will-edit-my-map is missing. This script edits a REAL "
            "room name on a REAL device's REAL map -- see the module docstring."
        )
        sys.exit(1)

    username = args.username or input("Prime account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Password: ")

    print(f"\nTARGET DEVICE: {args.blid}")
    print("This script is about to temporarily rename one room on this device's real map, then")
    print("rename it back. See the module docstring for why only this one operation is tested.")
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
        print(f"Redacted raw responses saved to {args.dump_config}")

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Feedback for the maintainers ==")
        print("If you'd like to share this report:")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


if __name__ == "__main__":
    main()
