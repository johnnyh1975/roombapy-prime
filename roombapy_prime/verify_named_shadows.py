"""Manual, observed check of named shadows this library has never
queried before -- specifically aimed at finding where battery/charging
status actually lives.

WHY THIS SCRIPT EXISTS: a person's own native-binary symbol analysis
(not this library's own investigation) found that the real app
subscribes to a wildcard covering every named shadow
("/things/{blid}/shadow/name/+/get/accepted" and the "update/accepted"
sibling). Five named shadows are known to exist from that pattern, but
this library has only ever queried two of them: the classic/unnamed
shadow (get_state()) and "rw-settings" (get_settings()). The other
three -- "rw-constatus", "rw-schedule", "rw-software" -- had never
been queried before this session.

RESULT (this session, chairstacker, all three checked live): the
"rw-constatus" battery/charging hypothesis is DISPROVEN. Its content
is MQTT/AWS-IoT connection status ({"connected", "connectedv2",
"echo", "svcEndpoints"}), not battery -- the name's surface
resemblance to "connection status" was accurate, but pointed at the
wrong KIND of connection (network, not power/charging). The other two
also confirmed content, neither battery-related: "rw-schedule" is the
cleaning schedule, "rw-software" is OTA/firmware update status. See
ConnectionStatusShadow/ScheduleShadow/SoftwareStatusShadow
(models/robot_info.py) for the full field lists now modeled from this
result, and RobotStatusV2's own docstring for the complete correction.
All five named shadows this wildcard pattern covers are now fully
enumerated -- none contain battery/charging/dock data. This script is
kept (cheap, already built, useful to re-run against other
devices/accounts to confirm the same content), but is no longer
expected to resolve the battery question on its own.

That same native-app analysis flagged and corrected an earlier
mistake, still worth knowing even though the hypothesis itself didn't
pan out: "rw-constatus" had previously been written off because the
app's own command config only lists a write-side SetEchoCommand (read:
false) for it -- but that config describes COMMANDS, not
SUBSCRIPTIONS. The wildcard subscribes to a named shadow regardless of
whether any explicit read command exists for it -- a distinction
worth remembering for any FUTURE named-shadow candidate too.

PURELY PASSIVE: every shadow fetched here (get_state(), get_settings(),
and the three candidates via get_named_shadow()) is a read. Nothing is
sent to the robot, no confirmation gate is needed, and the robot is
never moved -- unlike verify_mission_commands.py/verify_mission_timeline.py's
--start-mission mode. Safe to run at any time.

WHAT SUCCESS LOOKS LIKE: any shadow returning a payload containing
something recognizable as battery/charging data (a percentage, a
boolean, a charging-state string) -- not expected anymore given the
result above, but this script still reports the exact keys seen in
any payload, not just "worked" or "didn't", in case that changes on a
different device/account.

USAGE:
  roombapy-prime-verify-named-shadows \\
      --username you@example.com --country-code US --blid BLID123

Credentials same as diagnostics.py: ROOMBAPY_PRIME_PASSWORD env var or
interactive prompt, never as a command-line argument.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import webbrowser
from typing import Any

import aiohttp

from .diagnostics import Report, _redact_raw_capture, build_issue_url, redact_aws_url_secrets
from .prime_factory import PrimeFactory

# The two shadows already confirmed queryable, included as a baseline/
# sanity-check so a tester can immediately see these still work exactly
# as before -- and the three never-before-queried candidates from the
# wildcard-subscription finding, "rw-constatus" listed first since it's
# the strongest lead.
KNOWN_SHADOWS: list[str | None] = [None, "rw-settings"]
CANDIDATE_SHADOWS: list[str] = ["rw-constatus", "rw-schedule", "rw-software"]


async def _fetch_and_report(
    robot: Any, name: str | None, report: Report, raw_capture: dict[str, Any],
) -> None:
    label = name or "(classic/unnamed)"
    try:
        response = await robot.get_named_shadow(name) if name is not None else await robot.get_state()
    except Exception as exc:  # noqa: BLE001
        report.add(f"Shadow: {label}", "FAILED", f"{type(exc).__name__}: {exc}")
        print(f"\n  [{label}] FAILED: {type(exc).__name__}: {exc}")
        return

    raw_capture[f"shadow: {label}"] = response.payload
    reported = (response.payload or {}).get("state", {}).get("reported", {})
    keys = sorted(reported.keys()) if isinstance(reported, dict) else None
    if keys:
        report.add(f"Shadow: {label}", "OK", f"reported keys: {keys}")
        print(f"\n  [{label}] reported keys: {keys}")
        print(f"  [{label}] full reported payload: {redact_aws_url_secrets(str(reported))}")
    else:
        report.add(f"Shadow: {label}", "OK", "empty or unrecognized shape -- see raw capture")
        print(f"\n  [{label}] empty or unrecognized shape: {redact_aws_url_secrets(repr(response.payload))}")


async def run(username: str, password: str, country_code: str, blid: str) -> tuple[Report, dict[str, Any]]:
    report = Report()
    raw_capture: dict[str, Any] = {}

    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")
        await robot.connect()
        report.add("MQTT connection", "OK")

        print("\n== Checking known shadows (baseline) ==")
        for name in KNOWN_SHADOWS:
            await _fetch_and_report(robot, name, report, raw_capture)

        print("\n== Checking candidate shadows (never queried before) ==")
        for name in CANDIDATE_SHADOWS:
            await _fetch_and_report(robot, name, report, raw_capture)

        await robot.disconnect()

    return report, raw_capture


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Checks named shadows this library has never queried before "
            "(rw-constatus/rw-schedule/rw-software), specifically looking for battery/charging "
            "status. Purely read-only -- never sends anything to the robot, no confirmation "
            "gate needed. See the module docstring for the full reasoning."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", required=True, help="The exact target device -- no 'first device found'.")
    parser.add_argument("--output", default=None, metavar="PATH")
    parser.add_argument("--dump-config", default=None, metavar="PATH")
    parser.add_argument("--no-issue-link", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    username = args.username or input("Prime account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Password: ")

    print(f"\nTARGET DEVICE: {args.blid}")
    print("This run only reads shadows -- it never sends commands to this device.")

    report, raw_capture = asyncio.run(run(username, password, args.country_code, args.blid))
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Summary: {ok} OK, {failed} failed, {skipped} skipped ==")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Report saved to {args.output}")

    if args.dump_config:
        redacted = _redact_raw_capture(raw_capture, [username, password])
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(redacted, f, indent=2, default=str, ensure_ascii=False)
        print(f"Redacted raw shadow responses saved to {args.dump_config}")

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Feedback for the maintainers ==")
        print("If you'd like to share this report:")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


if __name__ == "__main__":
    main()
