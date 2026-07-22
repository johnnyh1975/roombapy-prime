"""Manual, observed check of named shadows -- specifically aimed at
finding where battery/charging status actually lives.

BACKGROUND: a person's own native-binary symbol analysis found the
real app subscribes to a wildcard covering every named shadow. Five
were found this way -- classic/unnamed, "rw-settings", "rw-constatus",
"rw-schedule", "rw-software" -- all now confirmed live (chairstacker).
None contain battery/charging data: "rw-constatus" (the leading
candidate, from its plausible link to the app's "SetEchoCommand") is
MQTT/AWS-IoT connection status, not battery -- see
ConnectionStatusShadow/ScheduleShadow/SoftwareStatusShadow
(models/robot_info.py) for the full field lists, and RobotStatusV2's
own docstring for the complete correction.

NEW LEAD (this session, a separate native-analysis track, prompted by
this exact five-shadow dead end): a class this project had never
looked at, MQTTTopics.java, builds topics for FOUR MORE shadows this
project never knew existed -- "ro-currentstate", "ro-services",
"ro-configinfo", "ro-stats" (the "ro-" prefix meaning read-only, as
opposed to the "rw-" ones already checked). These never appeared in
the app's own command config specifically because that config only
lists commands, and nothing writes to a read-only shadow -- a real,
identifiable reason the earlier wildcard-based enumeration missed
half of what actually exists, not just bad luck.

"ro-currentstate" specifically is now the strongest lead this
investigation has had: the name itself describes exactly the kind of
data being searched for (live, device-reported, read-only state), and
its very existence cleanly explains every prior negative result --
wrong shadows, wrong topics, and wrong REST endpoints were all being
checked, not a "doesn't exist" case.

NOT YET TESTED against a real device as of this writing -- this is a
hypothesis, however well-reasoned, until someone actually runs this
script and sees what (if anything) comes back.

PURELY PASSIVE: every shadow fetched here (get_state(), get_settings(),
and every candidate via get_named_shadow()) is a read. Nothing is
sent to the robot, no confirmation gate is needed, and the robot is
never moved -- unlike verify_mission_commands.py/verify_mission_timeline.py's
--start-mission mode. Safe to run at any time.

WHAT SUCCESS LOOKS LIKE: any shadow returning a payload containing
something recognizable as battery/charging data (a percentage, a
boolean, a charging-state string). This script reports the exact keys
seen in any payload, not just "worked" or "didn't" -- important here
specifically, since a right-shadow-wrong-field-name result would
otherwise look identical to a clean miss.

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
import sys
import webbrowser
from typing import Any

import aiohttp

from .diagnostics import Report, _redact_raw_capture, build_issue_url, redact_aws_url_secrets
from .prime_factory import PrimeFactory

# The five shadows already confirmed queryable (content known, none
# battery-related), included as a baseline/sanity-check so a tester can
# immediately see these still work exactly as before -- and the four
# never-before-queried "ro-" (read-only) candidates from MQTTTopics.java,
# never listed in the app's own command config for the obvious reason
# that nothing writes to a read-only shadow. "ro-currentstate" listed
# first: the name itself matches exactly what's being searched for
# (live, device-reported, read-only state), making it the strongest
# lead this investigation has had.
KNOWN_SHADOWS: list[str | None] = [None, "rw-settings", "rw-constatus", "rw-schedule", "rw-software"]
CANDIDATE_SHADOWS: list[str] = ["ro-currentstate", "ro-stats", "ro-services", "ro-configinfo"]


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


async def run(
    username: str, password: str, country_code: str, blid: str, delay_seconds: float = 0.0
) -> tuple[Report, dict[str, Any]]:
    """delay_seconds: NEW (this session, prompted by a real field
    report -- chairstacker's own account has shown a genuine,
    unresolved connectivity issue across several runs: an original
    session with only 5 shadows queried had ALL 5 succeed; after 4
    more candidates were added (9 total), later runs showed 1 of 9,
    then 0 of 9, then 3 of 9 succeeding, in that order over time.

    CORRECTED (this session, self-review after a direct challenge):
    an earlier version of this docstring described this as "a strong
    signature of AWS IoT-side throttling" based on looking at only the
    MOST RECENT run's ordinal pattern (first 3 succeed, rest fail) in
    isolation. Checked against the FULL history above, that framing
    doesn't hold up -- a fixed-count throttle would show the same
    cutoff every time, not 1, then 0, then 3. The actual pattern looks
    more like a gradually-changing (recovering, or still partially
    degraded) connection between the robot/account and AWS IoT
    itself, consistent with the original "MQTT connection dropped"
    report this whole investigation started from -- not a per-
    connection request-count limit.

    This delay is kept as a LOW-COST, still-worth-trying option
    regardless of the exact mechanism (it can't make things worse,
    and there's a real chance any timing-sensitivity in a still-
    recovering connection benefits from more spacing between
    requests) -- but it should NOT be read as confirming a specific
    "throttling" theory. Genuinely unresolved as of this addendum."""
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
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

        print("\n== Checking candidate shadows (never queried before) ==")
        for name in CANDIDATE_SHADOWS:
            await _fetch_and_report(robot, name, report, raw_capture)
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

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
    parser.add_argument("--blid", default=os.environ.get("ROOMBAPY_PRIME_BLID"), help="The exact target device -- no 'first device found'. Falls back to ROOMBAPY_PRIME_BLID env var.")
    parser.add_argument("--output", default=None, metavar="PATH")
    parser.add_argument("--dump-config", default=None, metavar="PATH")
    parser.add_argument("--no-issue-link", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument(
        "--delay-seconds", type=float, default=0.0,
        help="Wait this long between each shadow query (default: 0, no delay). NEW: "
        "prompted by a real, still-unresolved field report of a gradually-changing "
        "connectivity issue between a robot/account and AWS IoT -- NOT a confirmed "
        "throttling limit (checked against the full run history, a fixed-count "
        "throttle doesn't fit the data). Low-cost to try regardless of the exact "
        "cause -- worth trying a few seconds if some but not all shadows are timing "
        "out for you.",
    )
    args = parser.parse_args()
    if not args.blid:
        print("Aborted: --blid is required (or set the ROOMBAPY_PRIME_BLID env var).")
        sys.exit(1)

    username = args.username or input("iRobot account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Password: ")

    print(f"\nTARGET DEVICE: {args.blid}")
    print("This run only reads shadows -- it never sends commands to this device.")

    report, raw_capture = asyncio.run(run(username, password, args.country_code, args.blid, args.delay_seconds))
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
