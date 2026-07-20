"""Manual, observed capture of whatever arrives on the mission-timeline
topic(s) during a real, actively-running mission.

WHY THIS SCRIPT EXISTS: a live idle-vs-mid-mission diff of get_state()
(chairstacker, this session) proved the classic shadow's reported state
is byte-identical whether the robot is idle or actively cleaning --
between two point-in-time GET snapshots, specifically. CORRECTION
(parallel reverse-engineering track): this was previously over-stated
as proof that live mission status doesn't flow through
get_state()/watch_state() "at all" -- the watch_state() part was never
actually tested live during a mission, only assumed by extension. A
separate investigation (native decompilation of libcorebase.so)
found a distinct topic pair believed to carry this instead:
"{irbt_topic_prefix}/things/{blid}/mission/timeline/report" (and its
"request" counterpart) -- see mqtt_client.py's mission_timeline_topic()
and prime_robot.py's watch_mission_timeline() for the full evidence
trail and exact confidence level (topic existence: confirmed from
native symbols; irbt_topic_prefix applying here: a strong inference,
not independently live-confirmed; payload shape: completely unknown).

Also watches "{irbt_topic_prefix}/things/{blid}/rejected/report" at the
same time -- a sibling topic found in the same decompilation pass,
directly complementing send_simple_command(): if a command call
appears to succeed but the robot doesn't react, this is where a
rejection reason (if the device reports one at all) would be expected
to arrive. See watch_rejected_commands()'s own docstring.

PURELY PASSIVE BY DEFAULT -- unlike verify_mission_commands.py, this
script does not need to send anything to the robot to do its job.
Start a cleaning cycle any way you like (the robot's own CLEAN button,
the iRobot app, or verify_mission_commands.py running in a separate
terminal at the same time) -- this script doesn't care how the mission
started, only that one is running while it's watching. No
"--i-understand-this-will-move-my-robot" flag is needed for that
reason.

OPTIONAL, EASIER ONE-TERMINAL MODE: pass --start-mission to have this
script send the actual 'start' (and 'stop'+'dock' at the end) itself, via the
same already-live-confirmed send_simple_command() path
verify_mission_commands.py uses -- lets a tester run one script in one
terminal instead of coordinating two. This DOES require
--i-understand-this-will-move-my-robot, same safety gate as
verify_mission_commands.py, since this mode genuinely moves the robot.
Stays watching for --post-dock-watch-seconds (default 30) AFTER sending
stop/dock, specifically to try catching any docking-related events --
an earlier version of this script stopped watching before sending
those commands, so any such events could never have been captured even
if they exist.

WHAT SUCCESS LOOKS LIKE: at least one message arriving on
mission/timeline/report while the robot is actively cleaning, ideally
containing something that looks like real mission state (a phase, a
percentage, a timestamp -- anything that changes between idle and
active). WHAT A NULL RESULT MEANS: if nothing arrives even during an
active mission, that's still valuable information -- it would mean
either the irbt_topic_prefix guess is wrong for this specific topic
family (despite the shared-factory reasoning), or the topic name
itself needs correction, or this channel simply isn't used the way the
native symbols suggest. Report it either way.

USAGE (two-terminal, fully passive):
  # Terminal 1:
  roombapy-prime-verify-mission-timeline \\
      --username you@example.com --country-code US --blid BLID123 \\
      --duration 60
  # Terminal 2, once terminal 1 says "Ready to start watching?" and
  # you've confirmed:
  roombapy-prime-verify-commands \\
      --username you@example.com --country-code US --blid BLID123 \\
      --i-understand-this-will-move-my-robot

USAGE (one-terminal, this script starts/stops the mission itself):
  roombapy-prime-verify-mission-timeline \\
      --username you@example.com --country-code US --blid BLID123 \\
      --duration 60 --start-mission --i-understand-this-will-move-my-robot

  Add --watch-wildcard (recommended) to also subscribe to
  "{irbt_topic_prefix}/things/{blid}/#" at the same time -- currently
  the only way to potentially catch robot position/pose data, whose
  topic is built dynamically rather than from a static path (see
  mqtt_client.py's notes next to rejected_report_topic() for the full
  investigation trail).

  Add --try-pose-request to also send an EXPERIMENTAL, UNCONFIRMED
  {"do": "get", "args": ["pose"], "id": 1} request on the cmd topic --
  a request format found via native decompilation, not known to work
  over this specific (AWS IoT) topic. Combine with --watch-wildcard,
  since there's no predictable topic to watch for a response
  otherwise. Asks for its own interactive confirmation regardless of
  this flag. See send_umi_get_request()'s own docstring.

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
from collections.abc import Callable
from typing import Any

import aiohttp

from .diagnostics import Report, _redact_raw_capture, _report_topic_prefix_status, build_issue_url, redact_aws_url_secrets
from .prime_factory import PrimeFactory


def _confirm(prompt: str) -> bool:
    """Interactive confirmation -- ONLY "j"/"ja"/"y"/"yes" (case
    doesn't matter) counts as approval, anything else (including just
    pressing Enter) aborts."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


async def _watch_one(
    agen_factory: Callable[[], Any],
    label: str,
    raw_capture: dict[str, Any],
    report: Report,
) -> None:
    """Consumes an async generator (from watch_mission_timeline()/
    watch_raw_topic()) for as long as the surrounding asyncio.wait_for()
    lets it run, printing and collecting every message that arrives.
    Cancellation (the normal way this ends, via the duration timeout)
    is expected and swallowed here -- it's not a failure.

    BUG FOUND AND FIXED (this session): previously printed the static
    watch `label` for every message, not response.topic (the actual
    concrete topic a message arrived on). For a specific-topic watch
    (mission/timeline/report, rejected/report) these are identical, so
    the bug was invisible there -- but for a wildcard watch
    (--watch-wildcard), EVERY one of potentially dozens of messages
    printed the SAME label (the wildcard pattern itself), silently
    discarding exactly the information that would show which distinct
    topics were actually active. A live capture with 81 wildcard
    messages (chairstacker) surfaced this: all 81 printed under one
    identical bracketed label, with no way to tell them apart by topic
    after the fact. Now prints/stores response.topic instead.

    ALSO NEW (this session, prompted by reviewing a real 81-message
    wildcard capture by hand): a distinct-topic frequency summary is
    now printed once the watch ends, for exactly the case that made the
    original bug hard to work around even after fixing it -- a
    wildcard capture with many messages is still tedious to scan by
    eye one at a time. This doesn't replace reading the individual
    messages (their PAYLOADS still need a human to interpret), but
    immediately shows which distinct topics were actually active and
    how often, before diving into any of them."""
    captured: list[dict[str, Any]] = []
    agen = agen_factory()
    try:
        async for response in agen:
            print(f"\n  [{response.topic}] {redact_aws_url_secrets(str(response.payload))}")
            captured.append({"topic": response.topic, "payload": response.payload})
    except asyncio.CancelledError:
        pass
    finally:
        await agen.aclose()
    raw_capture[f"watch: {label}"] = captured
    if captured:
        report.add(f"Watch {label}", "OK", f"{len(captured)} message(s) captured -- see raw capture")
        topic_counts: dict[str, int] = {}
        for item in captured:
            topic_counts[item["topic"]] = topic_counts.get(item["topic"], 0) + 1
        if len(topic_counts) > 1:
            print(f"\n  -- {len(topic_counts)} distinct topic(s) under \"{label}\" --")
            for topic, count in sorted(topic_counts.items(), key=lambda kv: -kv[1]):
                print(f"    {count:>3}x  {topic}")
    else:
        report.add(
            f"Watch {label}", "OK",
            "no messages arrived during the watch window -- itself a meaningful result, see the "
            "module docstring's \"what a null result means\" section",
        )


def _build_watch_specs(
    robot: Any, watch_wildcard: bool, watch_shadow_delta: bool
) -> list[tuple[Callable[[], Any], str]]:
    """Factored out of run() specifically so it's unit-testable on its
    own -- run() as a whole has no dedicated test (needs a full
    aiohttp session + PrimeFactory login mock), this way the watch-spec
    selection logic still does.

    watch_shadow_delta is NEW (this session, parallel reverse-
    engineering track) -- see its own --help text and watch_state()'s
    docstring for the full reasoning: watch_state() has existed for a
    while but was never run live during an active mission (only a
    get_state() snapshot diff was ever tested).

    REMOVED (this session, real field incident): a --watch-aws-tree
    flag briefly existed here, wildcard-subscribing to the entire
    "$aws/things/{blid}/#" namespace. A field tester (chairstacker)
    hit exactly the failure AWS's own documentation warns about:
    "Reserved topics" states topics starting with "$" are reserved,
    "unsupported publish or subscribe operations to reserved topics
    can result in a terminated connection", and the Device Shadow MQTT
    topics page explicitly says "we recommend that you avoid wild card
    subscriptions to shadow topics... avoid subscribing to topic
    filters like $aws/things/thingName/shadow/#". The run hung after
    "Start mission" (needed Ctrl+C), and a SEPARATE, later process
    (roombapy-prime-verify-named-shadows, previously reliable) then
    failed ALL FOUR named-shadow GETs with timeouts -- consistent with
    AWS IoT having terminated the connection or otherwise degraded
    service in response to the unsupported wildcard, not just a local
    client-side hang. watch_shadow_delta above is unaffected by this --
    it subscribes to exactly one specific, AWS-documented shadow topic
    (the same path used as the example in AWS's own IAM policy
    documentation for this exact feature), not a wildcard on the
    reserved namespace."""
    watch_specs: list[tuple[Callable[[], Any], str]] = [
        (robot.watch_mission_timeline, "mission/timeline/report"),
        (robot.watch_rejected_commands, "rejected/report"),
    ]
    if watch_wildcard:
        wildcard_topic = f"{robot._irbt_topic_prefix}/things/{robot.blid}/#"
        watch_specs.append((lambda: robot.watch_raw_topic(wildcard_topic), wildcard_topic))
    if watch_shadow_delta:
        watch_specs.append((robot.watch_state, "$aws/things/{blid}/shadow/update/delta"))
    return watch_specs


async def run(
    username: str, password: str, country_code: str, blid: str,
    duration: float, watch_wildcard: bool, start_mission: bool, try_pose_request: bool,
    post_dock_watch: float, watch_shadow_delta: bool = False,
) -> tuple[Report, dict[str, Any]]:
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
        if getattr(robot, "_irbt_topic_prefix", None) is None:
            print(
                "\nirbt_topic_prefix is missing for this account -- the mission-timeline topic "
                "needs it, same as mission commands. Aborting; see the report above for the "
                "actual discovery-response keys."
            )
            report.add("Watch mission timeline", "SKIPPED", "irbt_topic_prefix missing -- see report above")
            await robot.disconnect()
            return report, raw_capture

        watch_specs = _build_watch_specs(robot, watch_wildcard, watch_shadow_delta)

        print(f"\n== Watching for up to {duration:.0f}s ==")
        for _factory, label in watch_specs:
            print(f"  Subscribing to: {label}")

        if start_mission:
            print(
                "\n--start-mission was given: this run WILL send a real 'start' command "
                "(send_simple_command(), the same already-live-confirmed path "
                "verify_mission_commands.py uses) once subscribed, and 'stop'+'dock' at the end "
                "(so the robot returns home, rather than being left wherever it stopped)."
            )
            if not _confirm("Send 'start' and begin watching now?"):
                for _factory, label in watch_specs:
                    report.add(f"Watch {label}", "SKIPPED", "not confirmed by user")
                await robot.disconnect()
                return report, raw_capture
        else:
            print(
                "Start a cleaning cycle now, any way you like (the robot's own CLEAN button, the "
                "iRobot app, or verify_mission_commands.py running in a separate terminal) -- this "
                "run isn't sending anything itself (pass --start-mission to have it do that for "
                "you instead, in one terminal)."
            )
            if not _confirm("Ready to start watching?"):
                for _factory, label in watch_specs:
                    report.add(f"Watch {label}", "SKIPPED", "not confirmed by user")
                await robot.disconnect()
                return report, raw_capture

        tasks = [
            asyncio.create_task(_watch_one(factory, label, raw_capture, report))
            for factory, label in watch_specs
        ]

        # Subscriptions are established by the first await inside each task
        # (watch_*()'s own subscribe() call) -- give them a brief moment to
        # actually attach before sending "start", so the very first message
        # isn't missed by a subscription that hasn't registered with the
        # broker yet.
        if start_mission:
            await asyncio.sleep(1.0)
            try:
                await robot.send_simple_command("start")
                report.add("Start mission", "OK", "sent via send_simple_command()")
            except Exception as exc:  # noqa: BLE001
                report.add("Start mission", "FAILED", f"{type(exc).__name__}: {exc}")

        if try_pose_request:
            if not watch_wildcard:
                print(
                    "\nWARNING: --try-pose-request without --watch-wildcard means there is "
                    "nowhere this run is listening for a response -- the request's response "
                    "topic is not predictable in advance (see send_umi_get_request()'s own "
                    "docstring). Strongly consider re-running with both flags together."
                )
            print(
                "\n--try-pose-request was given: this sends an EXPERIMENTAL, UNCONFIRMED "
                'request ({"do": "get", "args": ["pose"], "id": 1}) to the same cmd topic '
                "send_simple_command() already uses. This request format was found in the "
                "legacy UMI protocol family, which has at least one HTTPS-only, non-cloud "
                "transport variant -- whether THIS specific attempt (over the AWS IoT cmd "
                "topic) is a cloud-reachable variant or not is genuinely unknown. See "
                "send_umi_get_request()'s own docstring for the full reasoning."
            )
            if _confirm("Send this experimental pose request now?"):
                try:
                    await robot.send_umi_get_request(["pose"])
                    report.add("Experimental pose request", "OK", 'sent {"do": "get", "args": ["pose"], "id": 1}')
                except Exception as exc:  # noqa: BLE001
                    report.add("Experimental pose request", "FAILED", f"{type(exc).__name__}: {exc}")
            else:
                report.add("Experimental pose request", "SKIPPED", "not confirmed by user")

        await asyncio.sleep(duration)

        if start_mission:
            # BUG FOUND (this session, real user friction -- chairstacker):
            # sending only "stop" left the robot stranded wherever it was
            # when the watch window ended -- "I had to physically push the
            # button on the device" to get it back to the dock. Fixed by
            # also sending "dock" afterward -- both commands independently
            # confirmed live-working (see send_simple_command()'s own
            # confidence table), and this exact stop-then-dock ordering
            # matches how verify_mission_commands.py's own test sequence
            # already validated them together ("Stop (after pause test)"
            # followed by "Dock"), rather than trying an untested
            # "dock directly from an actively-cleaning state" shortcut.
            try:
                await robot.send_simple_command("stop")
                report.add("Stop mission", "OK", "sent via send_simple_command()")
            except Exception as exc:  # noqa: BLE001
                report.add("Stop mission", "FAILED", f"{type(exc).__name__}: {exc}")

            try:
                await robot.send_simple_command("dock")
                report.add(
                    "Dock", "OK",
                    "sent via send_simple_command() -- returning the robot to its dock after this test run",
                )
            except Exception as exc:  # noqa: BLE001
                report.add(
                    "Dock", "FAILED",
                    f"{type(exc).__name__}: {exc} -- you may need to send the robot home manually",
                )

            # BUG FOUND (this session, while designing a way to actually
            # capture docking-related events): an earlier version of this
            # function cancelled the watch tasks BEFORE sending stop/dock --
            # meaning any events resulting from docking could never be
            # captured even if they exist, since nothing was listening
            # anymore by the time those commands were sent. The watch tasks
            # (started above, still running) stay alive through this whole
            # stop -> dock -> post-dock window for exactly that reason.
            if post_dock_watch > 0:
                print(
                    f"\n== Still watching for {post_dock_watch:.0f}s after stop/dock, "
                    "to catch any docking-related events =="
                )
                await asyncio.sleep(post_dock_watch)

        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        await robot.disconnect()

    return report, raw_capture


def _add_topic_grouped_views(redacted: dict[str, Any]) -> None:
    """NEW (this session): the terminal output already groups a watch's
    messages by distinct topic (see _watch_one()'s frequency summary)
    -- the saved --dump-config JSON didn't, staying a flat list even
    after the response.topic fix, an inconsistency found while
    answering a question about what else the tooling still needed.
    Every "watch: ..." entry (a list of {"topic", "payload"} dicts)
    gets a sibling "<key> (grouped by topic)" entry, without removing
    the original flat list -- both views available, since the flat one
    preserves arrival order and the grouped one doesn't. Mutates
    `redacted` in place; silently skips any key that isn't shaped like
    a watch entry (defensive, not expected to matter today, but this
    function shouldn't be the thing that breaks if raw_capture's shape
    ever changes elsewhere)."""
    for key in list(redacted.keys()):
        if not key.startswith("watch: "):
            continue
        entries = redacted[key]
        if not (isinstance(entries, list) and entries and isinstance(entries[0], dict)
                and "topic" in entries[0] and "payload" in entries[0]):
            continue
        grouped: dict[str, list[Any]] = {}
        for item in entries:
            grouped.setdefault(item["topic"], []).append(item["payload"])
        redacted[f"{key} (grouped by topic)"] = grouped


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manual, observed capture of whatever arrives on the mission-timeline topic(s) "
            "during a real, actively-running mission. Read-only -- never sends anything to the "
            "robot; see the module docstring for the full evidence trail behind why this topic "
            "is being watched at all."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", required=True, help="The exact target device -- no 'first device found'.")
    parser.add_argument(
        "--duration", type=float, default=90.0,
        help="How many seconds to watch for (default: 90). Start your cleaning cycle as soon as "
        "you confirm you're ready -- the clock starts immediately after that confirmation.",
    )
    parser.add_argument(
        "--watch-wildcard", action="store_true",
        help='Also subscribe to "{irbt_topic_prefix}/things/{blid}/#" at the same time -- '
        "RECOMMENDED: this is currently the only way to potentially catch robot position/pose "
        "data, since its topic is built dynamically (no static path exists to subscribe to "
        "directly, unlike mission/timeline and rejected/report -- see mqtt_client.py's own notes "
        "next to rejected_report_topic() for the full investigation).",
    )
    parser.add_argument(
        "--watch-shadow-delta", action="store_true",
        help="NEW: also runs watch_state() (the shadow's update/delta push channel) for the same "
        "duration as everything else. This method has existed for a while but has never "
        "actually been run LIVE during an active mission -- every prior finding about mission "
        "status not appearing in the shadow was a snapshot DIFF of get_state() (two point-in-time "
        "GETs compared), not a test of this persistent push subscription. Genuinely untested "
        "until now; see watch_state()'s own docstring for the full correction. SAFE: subscribes "
        "to exactly one specific, AWS-documented shadow topic (the same path used as the example "
        "in AWS's own IAM policy docs for this feature) -- not a wildcard on the reserved "
        '"$aws/" namespace (see the removed --watch-aws-tree flag\'s history in '
        "_build_watch_specs() for why that distinction matters).",
    )
    parser.add_argument(
        "--start-mission", action="store_true",
        help="Also send a real 'start' command once subscribed (and 'stop'+'dock' at the end, so "
        "the robot returns home afterward) -- lets "
        "this run in a single terminal instead of needing verify_mission_commands.py (or the "
        "app/robot button) running separately at the same time. Requires "
        "--i-understand-this-will-move-my-robot, same as verify_mission_commands.py.",
    )
    parser.add_argument(
        "--try-pose-request", action="store_true",
        help='EXPERIMENTAL, UNCONFIRMED: also sends {"do": "get", "args": ["pose"], "id": 1} on '
        "the cmd topic, a request format found via native decompilation for the legacy UMI "
        "protocol -- not known to work over this (AWS IoT) topic specifically. Asks for an "
        "explicit interactive confirmation before sending regardless of this flag. Combine with "
        "--watch-wildcard, since the response (if any) has no predictable topic to watch for it "
        "on otherwise. See send_umi_get_request()'s own docstring for the full reasoning.",
    )
    parser.add_argument(
        "--post-dock-watch-seconds", type=float, default=30.0,
        help="Only used with --start-mission: how long to keep watching AFTER sending stop/dock "
        "(default: 30). Set to 0 to disable. Exists specifically to try catching any "
        "docking-related events -- an earlier version of this script stopped watching BEFORE "
        "sending dock, meaning such events could never be captured even if they exist. NOTE: "
        "\"fin\" (mission-concluded) was confirmed to fire within the same second as the stop "
        "command -- it does NOT mean the robot has physically reached its dock yet. If you're "
        "specifically trying to find battery/charging status (still unconfirmed as of this "
        "release), a much longer value here (a few minutes, covering actual travel time back to "
        "base) is more likely to help than the default.",
    )
    parser.add_argument(
        "--i-understand-this-will-move-my-robot",
        action="store_true",
        dest="confirmed_move",
        help="Required if --start-mission is given. Without this flag, --start-mission is refused "
        "and the script aborts immediately, before any login.",
    )
    parser.add_argument("--output", default=None, metavar="PATH")
    parser.add_argument("--dump-config", default=None, metavar="PATH")
    parser.add_argument("--no-issue-link", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    if args.start_mission and not args.confirmed_move:
        print(
            "Aborted: --start-mission was given without "
            "--i-understand-this-will-move-my-robot. This would send a REAL 'start' command to a "
            "REAL device -- see --help."
        )
        sys.exit(1)

    username = args.username or input("Prime account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Password: ")

    print(f"\nTARGET DEVICE: {args.blid}")
    if args.start_mission:
        print("This run WILL send real start/stop commands to this device (--start-mission).")
    else:
        print("This run only listens -- it never sends commands to this device.")

    report, raw_capture = asyncio.run(
        run(
            username, password, args.country_code, args.blid, args.duration,
            args.watch_wildcard, args.start_mission, args.try_pose_request,
            args.post_dock_watch_seconds, args.watch_shadow_delta,
        )
    )
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Summary: {ok} OK, {failed} failed, {skipped} skipped ==")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Report saved to {args.output}")

    if args.dump_config:
        redacted = _redact_raw_capture(raw_capture, [username, password])
        _add_topic_grouped_views(redacted)
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(redacted, f, indent=2, default=str, ensure_ascii=False)
        print(f"Redacted raw responses (incl. every captured mission-timeline message) saved to {args.dump_config}")

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Feedback for the maintainers ==")
        print("If you'd like to share this report:")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


if __name__ == "__main__":
    main()
