"""Live validation of roombapy-prime against a real Prime/V4 account.

NEW (July 11, eleventh session). By far the biggest, repeatedly-cited
weak point of the whole library is: nothing was ever tested against a
real server. This script is the first concrete step to change that --
it runs the public API against a real account and reports OK/FAILED/
SKIPPED per area.

SAFETY PRINCIPLE, non-negotiable:
- READ-ONLY operations by default (login, REST GETs, fetching shadow
  state, downloading map bundles). None of this can change any state
  on the server or the robot.
- --allow-writes unlocks ONE reversible test: creating a test
  favorite, checking it shows up in get_favorites(), then deleting it
  immediately. This validates the three HTTP methods (create/update/
  delete favorite) that were previously only confirmed via bytecode,
  never live-tested, against a single, clearly-labeled, self-cleaning
  object.
- Mission commands (send_mission_command -- the robot would actually
  start/stop/etc.) and map editing (edit_map -- could disfigure a
  real, possibly laboriously created map) are NEVER run, even with
  --allow-writes. The risk of an unwanted real-world action is too
  high for an automated test script -- these two areas still need
  deliberate, targeted manual tests by a human who is watching.

Usage:
    python -m roombapy_prime.diagnostics --username you@example.com --country-code US
    (Password is prompted interactively, never as a command-line
    argument -- that would end up in shell history.)

    Optional: --blid BLID123 (otherwise the first robot found is used)
    Optional: --allow-writes (see above)
    Optional: --output report.md (additionally save the result report as markdown)
    Optional: --dump-config diagnose.json (see DIAGNOSTIC DUMP below)

Credentials can alternatively be set via environment variables
ROOMBAPY_PRIME_USERNAME / ROOMBAPY_PRIME_PASSWORD / ROOMBAPY_PRIME_COUNTRY
(useful for CI/repeated runs) -- but are never logged or included in
the report.

CHECKS COVERED (NEW, session 24 -- previously had gaps): besides
login/MQTT/the REST reads, now also `get_live_map_stream()` and a
time-bounded (default 3s) `watch_state()` test -- both read-only,
previously not covered by pure oversight. NOT covered, deliberately:
all write operations except the favorite round-trip test, as well as
send_mission_command/edit_map/reset_robot (see safety principle
above) and poll_echo_value (triggers an audible signal on the real
device -- too invasive for an automated script, even though it's
technically reversible).

DIAGNOSTIC DUMP (NEW, session 24): --dump-config PATH saves the
ACTUAL raw responses from every read endpoint as JSON -- similar to a
Home Assistant integration's "Download Diagnostics" feature. Unlike
the normal report (which only shows pass/fail), this contains real
field names AND real values -- that's the whole point of this file:
providing raw data suitable for reverse engineering, not just status.
Redaction still happens in two stages (credentials + obviously
sensitive field names like address/GPS/WiFi), but it is NOT as
thorough as with the normal report -- which is WHY this file is never
automatically part of the issue link, and must be attached
deliberately and individually, after reviewing it yourself. Map
bundle contents are never written out (only the filenames within) --
a floor plan is more personal than most other data captured here.

FEEDBACK FOR THE MAINTAINERS (NEW, twelfth session):
At the end of every run -- in addition to the console output -- a
pre-filled link to open a GitHub issue is printed (title + report as
body, URL-encoded), as well as the same report as plain markdown for
manual copying (e.g. for Discord/email, if GitHub isn't wanted). The
report first goes through a redaction stage: every literal occurrence
of the username or password in any error text is replaced with
"[REDACTED]" -- defense in depth, in case a deeper exception (e.g.
from aiohttp) accidentally embeds credentials in an error message.
The target repo path is configurable via ISSUE_TRACKER_REPO below --
currently set to the real repo
(github.com/johnnyh1975/roombapy-prime, see the constant).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any
from urllib.parse import quote

import aiohttp

from .prime_factory import PrimeFactory

#: Repo for the pre-filled "New issue" link (see module docstring).
#: Updated (session 19) to the real GitHub repo.
ISSUE_TRACKER_REPO = "johnnyh1975/roombapy-prime"


@dataclass
class CheckResult:
    name: str
    status: str  # "OK", "FAILED", "SKIPPED"
    detail: str = ""


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.results.append(CheckResult(name, status, detail))
        marker = {"OK": "✓", "FAILED": "✗", "SKIPPED": "–"}[status]
        line = f"  [{marker}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)

    def summary(self) -> tuple[int, int, int]:
        ok = sum(1 for r in self.results if r.status == "OK")
        failed = sum(1 for r in self.results if r.status == "FAILED")
        skipped = sum(1 for r in self.results if r.status == "SKIPPED")
        return ok, failed, skipped

    def redact(self, *secrets: str) -> None:
        """NEW (session 12). Replaces every literal occurrence of the
        given strings (username, password) in EVERY error text with
        "[REDACTED]" -- defense in depth, before the report is shared.
        Should normally find nothing (credentials are never written
        directly into report entries anywhere), but catches it in case
        a deeper exception (e.g. from aiohttp) accidentally embeds
        credentials in an error message."""
        cleaned = [s for s in secrets if s]
        if not cleaned:
            return
        for result in self.results:
            for secret in cleaned:
                if secret in result.detail:
                    result.detail = result.detail.replace(secret, "[REDACTED]")

    def to_markdown(self) -> str:
        import platform

        from . import __version__ as lib_version

        lines = [
            f"# roombapy-prime Live Validation — {datetime.now(UTC).isoformat()}",
            "",
            f"roombapy-prime {lib_version}, Python {platform.python_version()}, {platform.system()}",
            "",
        ]
        for r in self.results:
            marker = {"OK": "✅", "FAILED": "❌", "SKIPPED": "⏭️"}[r.status]
            entry = f"- {marker} **{r.name}**"
            if r.detail:
                entry += f": {r.detail}"
            lines.append(entry)
        ok, failed, skipped = self.summary()
        lines += ["", f"**Summary:** {ok} OK, {failed} failed, {skipped} skipped."]
        return "\n".join(lines)


async def _try(report: Report, name: str, coro: Any, capture: dict[str, Any] | None = None) -> Any:
    """Runs a single check, catching EVERY exception (not just
    RestError) -- a diagnostics script must never crash itself, no
    matter what the server returns.

    capture (NEW, session 24): if provided, the raw, successful result
    is additionally stored under `name` -- for --dump-config (see
    main()). Kept separate from the report itself so the normal
    pass/fail report (which e.g. flows into the GitHub issue link)
    stays unchanged and compact; the raw data only ends up in the
    optional dump file, never automatically in the issue link."""
    try:
        result = await coro
        report.add(name, "OK")
        if capture is not None:
            capture[name] = result
        return result
    except Exception as exc:  # noqa: BLE001 -- bewusst breit, siehe Docstring
        report.add(name, "FAILED", f"{type(exc).__name__}: {exc}")
        return None


def _skip(report: Report, name: str, reason: str) -> None:
    report.add(name, "SKIPPED", reason)


def _report_device_info(report: Report, state: Any) -> None:
    """NEW (session 21), CORRECTED (sessions 25/27): the first live
    response (chairstacker) showed the actual structure --
    payload["state"]["reported"] contains sku/svcEndpoints/soldAsSku,
    NOT at the top level as originally assumed.

    IMPORTANT CORRECTION (session 27, detailed review): the complete
    real response shows that "reported" contains NO firmware/
    softwareVer field AT ALL -- neither at the top level nor nested.
    Firmware info instead comes from get_serial_number_data() or from
    individual mission history entries (both carry "softwareVer"). The
    "firmware" candidate is still kept here (in case some other
    device/tier does carry it in the shadow), but it shouldn't be
    surprising if it stays empty here -- that's expected, not a sign
    of a bug.

    Candidate field names otherwise remain guesses -- so this will
    ALWAYS additionally report the actual top-level keys, in case the
    nesting changes again."""
    if state is None or not isinstance(getattr(state, "payload", None), dict):
        return
    payload = state.payload
    reported = payload.get("state", {}).get("reported", {}) if isinstance(payload.get("state"), dict) else {}
    candidates = {
        "sku": ["sku", "soldAsSku", "mdl"],
        "firmware": ["softwareVer", "ver", "firmwareVersion"],
        "name": ["name", "robotName"],
        "capabilities": ["cap"],
    }
    found = {}
    for label, keys in candidates.items():
        for key in keys:
            if key in reported:
                found[label] = reported[key]
                break
            if key in payload:
                found[label] = payload[key]
                break
    top_level_keys = sorted(payload.keys())
    reported_keys = sorted(reported.keys()) if reported else []
    detail = f"found: {found}" if found else "none of the suspected candidate fields were found"
    detail += f" -- top-level keys: {top_level_keys}"
    if reported_keys:
        detail += f" -- state.reported keys: {reported_keys}"
    report.add("Device info extracted from get_state()", "OK", detail)


def _report_topic_prefix_status(report: Report, robot: Any) -> None:
    """NEW (session 41). A live test (chairstacker) first showed
    irbt_topic_prefix coming back None -- the guessed discovery-response
    field names ("irbtTopicPrefix"/"iotTopicPrefix") don't match reality
    for this account. Rather than guess again without evidence (this
    project's own standing rule against exactly that), report the
    ACTUAL keys present in the raw discovery deployment object
    (robot.deployment, see auth.py's LoginResult/prime_robot.py's
    PrimeRobot -- previously a local variable, discarded after login(),
    with no way to inspect it when the guess turned out wrong; now
    captured specifically so this diagnostic can exist).

    Uses _shallow_summary() (structure only, never values) -- the
    deployment object may contain endpoint URLs/config that shouldn't be
    fully dumped into a shared report, but the KEY NAMES are exactly
    what's needed to fix the guess."""
    prefix = getattr(robot, "_irbt_topic_prefix", None)
    if prefix is not None:
        report.add("irbt_topic_prefix (from discovery response)", "OK", f"found: {prefix!r}")
        return
    deployment = getattr(robot, "deployment", None) or {}
    if not deployment:
        report.add(
            "irbt_topic_prefix (from discovery response)",
            "FAILED",
            "not found, AND robot.deployment itself is empty -- can't report candidate keys",
        )
        return
    report.add(
        "irbt_topic_prefix (from discovery response)",
        "FAILED",
        "guessed keys \"irbtTopicPrefix\"/\"iotTopicPrefix\" not present -- "
        f"actual deployment object structure: {_shallow_summary(deployment)}",
    )


def _report_tier_inference(report: Report, settings_result: Any) -> None:
    """NEW (session 21), WEAKENED (session 25) -- the same device BLID
    produced DIFFERENT results in two consecutive runs (once success,
    once timeout). That's not a stable tier signal -- either a race
    condition in this library or a genuine, changing device state
    (robot online/offline with respect to AWS IoT). Wording adjusted
    to be more cautious accordingly: "suggests" instead of "is"."""
    if settings_result is not None:
        report.add(
            "Tier guess (from get_settings() result)",
            "OK",
            "rw-settings responded -> suggests SMART tier. NOTE: the same device also showed a "
            "timeout in a different run -- this signal is not reliably stable, see "
            "get_settings()'s docstring.",
        )
    else:
        report.add(
            "Tier guess (from get_settings() result)",
            "OK",
            "rw-settings did NOT respond (timeout) -> could mean EPHEMERAL tier, but could also "
            "be a temporary state (e.g. robot currently not actively connected to AWS IoT) -- the "
            "same device also showed a success in a different run. Not reliable proof of tier on "
            "its own.",
        )


async def _check_candidate_shadows(report: Report, robot: Any, raw_capture: dict[str, Any]) -> None:
    """NEW (this session, prompted by a person's own native-binary symbol
    analysis, not this library's own investigation): the real app
    subscribes to a wildcard covering every NAMED shadow. Five were
    found this way -- classic + "rw-settings" (already checked before
    this function is called), plus "rw-constatus"/"rw-schedule"/
    "rw-software" -- all now confirmed live (chairstacker). None
    contain battery/charging data: "rw-constatus" ({"connected",
    "connectedv2", "echo", "svcEndpoints"}) is MQTT/AWS-IoT connection
    status, not battery -- see RobotStatusV2's own docstring for the
    full correction, and ConnectionStatusShadow/ScheduleShadow/
    SoftwareStatusShadow (models/robot_info.py) for the confirmed
    content of all three.

    NEW CANDIDATES (this session, a separate native-analysis track):
    MQTTTopics.java builds topics for FOUR MORE shadows this project
    never knew existed -- "ro-currentstate", "ro-stats", "ro-services",
    "ro-configinfo" (read-only, unlike the "rw-" ones above). These
    never appeared in the app's own command config for an identifiable
    reason: that config only lists commands, and nothing writes to a
    read-only shadow -- the wildcard-based enumeration that found the
    five "rw-"/classic shadows structurally could never have found
    these. "ro-currentstate" is now the strongest lead this
    investigation has had: the name itself describes exactly the kind
    of data being searched for. NOT YET TESTED against a real device
    as of this writing.

    Purely a read, same risk profile as get_state()/get_settings() --
    see get_named_shadow()'s own docstring for the specific earlier
    mistake ("rw-constatus" was wrongly written off originally because
    the app's command config lists only a write-side command for it --
    that describes commands, not subscriptions) that led to checking
    it at all; the same distinction (config lists commands, not
    subscriptions) is exactly why the four new "ro-" candidates were
    missed for as long as they were. Factored out as its own function
    (rather than an inline loop in run()) specifically so it's
    unit-testable on its own -- run() as a whole has no dedicated test
    of its own, this way the new behavior still does."""
    for candidate_shadow in ("ro-currentstate", "ro-stats", "ro-services", "ro-configinfo"):
        await _try(
            report,
            f'Fetching named shadow "{candidate_shadow}" (get_named_shadow)',
            robot.get_named_shadow(candidate_shadow),
            capture=raw_capture,
        )


async def run(
    username: str,
    password: str,
    country_code: str,
    blid: str | None,
    allow_writes: bool,
    raw_capture: dict[str, Any] | None = None,
) -> Report:
    report = Report()

    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        try:
            robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
            report.add("Login (Discovery + Gigya + iRobot auth chain)", "OK", f"BLID={robot.blid}")
        except Exception as exc:  # noqa: BLE001
            report.add("Login", "FAILED", f"{type(exc).__name__}: {exc}")
            print("\nLogin failed -- all further checks will be skipped.")
            return report

        _report_topic_prefix_status(report, robot)
        if raw_capture is not None:
            # NEW (session 42): _report_topic_prefix_status() above only shows the
            # deployment object's STRUCTURE (key names/types, via _shallow_summary()) --
            # deliberately conservative for the always-printed report. But confirming
            # the real irbt_topic_prefix field name needs an actual VALUE (something
            # shaped like "v0NN-irbthbu") to know which key is the right one, not just
            # its name. --dump-config's redaction (usernames/passwords, not general
            # values) is a fundamentally different, already-accepted trust boundary
            # than the always-printed report, so the raw deployment object is captured
            # here, not there.
            raw_capture["Discovery deployment object (for irbt_topic_prefix)"] = robot.deployment

        print("\n== MQTT / shadow state ==")
        try:
            await robot.connect()
            report.add("MQTT connection (AWS IoT custom authorizer)", "OK")
        except Exception as exc:  # noqa: BLE001
            report.add("MQTT connection", "FAILED", f"{type(exc).__name__}: {exc}")

        state = await _try(report, "Fetching shadow state (get_state)", robot.get_state(), capture=raw_capture)
        _report_device_info(report, state)

        settings_result = await _try(
            report, "Fetching shadow settings (get_settings)", robot.get_settings(), capture=raw_capture
        )
        _report_tier_inference(report, settings_result)

        await _check_candidate_shadows(report, robot, raw_capture)

        await _try(
            report, "Requesting live map stream (get_live_map_stream)", robot.get_live_map_stream(), capture=raw_capture
        )
        await _try_watch_state_briefly(report, robot)

        print("\n== REST reads (favorites/mission history/schedules/...) ==")
        await _try(report, "Fetching favorites (get_favorites)", robot.get_favorites(), capture=raw_capture)
        await _try(
            report,
            "Fetching mission history (get_mission_history)",
            robot.get_mission_history(robot.blid, max_reports=5),
            capture=raw_capture,
        )
        await _try(
            report, "Fetching household list (get_user_households)", robot.get_user_households(), capture=raw_capture
        )
        await _try(
            report, "Fetching consumable parts (get_robot_parts)", robot.get_robot_parts(), capture=raw_capture
        )
        await _try(
            report,
            "Fetching serial number/device data (get_serial_number_data)",
            robot.get_serial_number_data(),
            capture=raw_capture,
        )
        await _try(
            report, "Fetching notifications (get_notifications)", robot.get_notifications(), capture=raw_capture
        )

        map_versions = await _try(
            report,
            "Fetching active map versions (get_active_map_versions)",
            robot.get_active_map_versions(),
            capture=raw_capture,
        )

        p2map_id: str | None = None
        p2map_version_id: str | None = None
        if map_versions:
            try:
                first = map_versions[0]
                p2map_id = (
                    first.get("p2map_id")
                    or first.get("mapId")
                    or first.get("p2mapId")
                    or first.get("id")
                    or first.get("map_id")
                )
                # NEW (session 33): "1" was a placeholder guess value
                # for the map version -- real data (chairstacker) shows
                # that the actual version ID is under "active_p2mapv_id"
                # (e.g. "260518T135521.119", not a simple counter). This
                # likely explains the HTTP 400 error on
                # get_map_geojson_link(): the URL previously always
                # contained a made-up, never-existing version ID.
                p2map_version_id = first.get("active_p2mapv_id")
            except (AttributeError, IndexError, TypeError):
                p2map_id = None
            if p2map_id is None:
                report.add(
                    "Map ID extraction",
                    "FAILED",
                    f"get_active_map_versions() returned data, but no known ID field was found. "
                    f"Response structure: {_shallow_summary(map_versions)}",
                )

        if p2map_id:
            await _try(
                report,
                "Fetching map metadata (get_map_metadata)",
                robot.get_map_metadata(p2map_id),
                capture=raw_capture,
            )
            if p2map_version_id:
                geojson_link = await _try(
                    report,
                    "Fetching presigned map bundle URL (get_map_geojson_link)",
                    robot.get_map_geojson_link(p2map_id, p2map_version_id),
                )
            else:
                geojson_link = None
                _skip(
                    report,
                    "Fetching presigned map bundle URL (get_map_geojson_link)",
                    "no active_p2mapv_id found in get_active_map_versions()'s response",
                )
            if isinstance(geojson_link, dict):
                # CORRECTED (session 48): "map_url" is now confirmed via
                # P2MapURL$$serializer -- tried first, falling back to the old
                # any-http-looking-value heuristic in case a real response ever
                # doesn't match (belt and suspenders, cheap to keep both).
                url = geojson_link.get("map_url") or next(
                    (v for v in geojson_link.values() if isinstance(v, str) and v.startswith("http")), None
                )
                if url:
                    bundle = await _try(report, "Downloading map bundle (download_map_bundle)", _fetch_bundle(robot, url))
                    if bundle is not None:
                        from .models import parse_map_bundle

                        try:
                            parsed = parse_map_bundle(bundle)
                            report.add(
                                "Unpacking map bundle (parse_map_bundle)", "OK", f"{len(parsed)} files found"
                            )
                            # NEW (session 24): deliberately capture ONLY
                            # the filenames, never the map content itself
                            # -- a floor plan is considerably more
                            # personal than most other data captured here.
                            if raw_capture is not None:
                                raw_capture["Map bundle (filenames only)"] = sorted(parsed.keys())
                                # NEW (session 45): ALSO capture a type-only structure
                                # summary of every file via _shallow_summary() -- this is
                                # safe (never reveals actual values, incl. geometry
                                # coordinates, only field names/types/list lengths, same
                                # privacy guarantee as everywhere else _shallow_summary()
                                # is used in this file). UPDATE (session 47): the map-bundle
                                # read models this was written to help confirm (RoomInfo,
                                # BorderInfo, etc.) have since been resolved via bytecode
                                # (see models/map_bundle.py's RoomFeature and neighbors) -- this capture
                                # remains useful as a cross-check against real bundle data,
                                # and for the still-unconfirmed manifest filename/
                                # FeatureCollection-vs-bare-list wrapping question.
                                raw_capture["Map bundle structure (types only, never values)"] = {
                                    filename: _shallow_summary(content) for filename, content in parsed.items()
                                }
                        except Exception as exc:  # noqa: BLE001
                            report.add("Unpacking map bundle", "FAILED", f"{type(exc).__name__}: {exc}")
                else:
                    _skip(
                        report,
                        "Downloading map bundle",
                        "no recognizable URL key in the response (response shape unconfirmed)",
                    )
            households = await _try_silent(robot.get_user_households())
            household_id = _extract_first_id(households, ["household_id", "householdId", "id"])
            if household_id:
                await _try(
                    report, "Fetching schedules (get_schedules)", robot.get_schedules(household_id), capture=raw_capture
                )
                await _try(
                    report,
                    "Fetching DND settings (get_dnd_settings)",
                    robot.get_dnd_settings(household_id),
                    capture=raw_capture,
                )
            elif households:
                report.add(
                    "household_id extraction",
                    "FAILED",
                    f"get_user_households() returned data, but neither 'householdId' nor 'id' "
                    f"was found. Response structure: {_shallow_summary(households)}",
                )
                _skip(report, "Fetching schedules/DND", "household_id extraction failed, see above")
            else:
                _skip(
                    report,
                    "Fetching schedules/DND",
                    "get_user_households() returned no data (empty response or error)",
                )

            await _try(
                report,
                "Fetching cleaning profiles (get_cleaning_profiles)",
                robot.get_cleaning_profiles(robot.blid, p2map_id),
                capture=raw_capture,
            )
            await _try(
                report,
                "Fetching default routines (get_default_routines)",
                robot.get_default_routines(p2map_id),
                capture=raw_capture,
            )
        else:
            _skip(
                report,
                "Map metadata/bundle/cleaning profiles/default routines",
                f"no active map version found -- get_active_map_versions()'s response: "
                f"{_shallow_summary(map_versions)} (if this shows an empty list even though "
                f"the robot has learned a map, that's the actual bug, not the robot's age)",
            )

        if allow_writes:
            print("\n== Reversible write round-trip test (--allow-writes) ==")
            await _round_trip_favorite_test(robot, report)
        else:
            _skip(
                report,
                "Favorite write round-trip test (create/update/delete)",
                "--allow-writes not set",
            )

        _skip(
            report,
            "Mission commands (send_mission_command)",
            "NEVER run automatically -- see module docstring",
        )
        _skip(
            report,
            "Map editing (edit_map)",
            "NEVER run automatically -- see module docstring",
        )

        await robot.disconnect()

    return report


async def _fetch_bundle(robot: Any, url: str) -> bytes:
    return await robot.download_map_bundle(url)


async def _try_watch_state_briefly(report: Report, robot: Any, timeout_seconds: float = 3.0) -> None:
    """NEW (session 24) -- watch_state() was completely untested until
    now, even though it's read-only (only reacts to shadow deltas,
    sends nothing). Runs the generator for at most `timeout_seconds` --
    getting NO delta is normal (the robot needs to actively change for
    that) and counts as OK, not a failure; a crash of the generator
    itself, on the other hand, would be a real finding."""
    try:
        count = 0
        async with asyncio.timeout(timeout_seconds):
            async for _delta in robot.watch_state():
                count += 1
        report.add("Watching continuously (watch_state, brief)", "OK", f"{count} delta(s) in {timeout_seconds}s")
    except TimeoutError:
        report.add(
            "Watching continuously (watch_state, brief)",
            "OK",
            f"no delta in {timeout_seconds}s -- normal if the state doesn't change",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("Watching continuously (watch_state, brief)", "FAILED", f"{type(exc).__name__}: {exc}")


async def _try_silent(coro: Any) -> Any:
    """Like _try(), but without a report entry -- for intermediate
    steps that aren't a check point in their own right (e.g. just
    determining household_id, so that ONE other check can even be
    attempted)."""
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return None


def _shallow_summary(data: Any, _depth: int = 0) -> Any:
    """NEW (session 21) -- summarizes an unknown response structure for
    debug output: STRUCTURE (keys/types/length), NEVER actual values --
    so that even with unexpected shapes, no potentially sensitive
    content (addresses, names, IDs) ends up in a shared report, only
    the shape of the response. Deliberately shallow (max 2 levels) --
    that's enough for field-name debugging, a deeper dump would just
    be noise."""
    if _depth >= 2:
        return "..."
    if isinstance(data, dict):
        return {k: _shallow_summary(v, _depth + 1) for k, v in data.items()}
    if isinstance(data, list):
        if not data:
            return "[] (empty list)"
        return f"list[{len(data)}] first element: {_shallow_summary(data[0], _depth + 1)}"
    return type(data).__name__


def _extract_first_id(data: Any, keys: list[str]) -> str | None:
    """Best-effort: finds the first matching ID in a possibly nested,
    unconfirmed response shape (households/settings listing was never
    checked against a real response, see get_user_households()'s
    docstring)."""
    if isinstance(data, dict):
        for key in keys:
            if key in data and isinstance(data[key], str):
                return data[key]
        for value in data.values():
            found = _extract_first_id(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _extract_first_id(item, keys)
            if found:
                return found
    return None


async def _round_trip_favorite_test(robot: Any, report: Report) -> None:
    """Creates ONE clearly test-labeled favorite, checks it shows up,
    deletes it again immediately. Live-validates the three HTTP
    methods (POST/PUT/DELETE) for favorites that were previously only
    confirmed via bytecode, without leaving any permanent trace."""
    from .models import FavoriteV1

    test_favorite = FavoriteV1(
        name="roombapy-prime-diagnostics-test-favorite (please delete if visible)",
        command_defs=[],
    )

    created = await _try(report, "Creating test favorite (create_favorite)", robot.create_favorite(test_favorite))
    if created is None:
        _skip(report, "Checking/deleting test favorite", "Creation failed, see above")
        return

    created_id = None
    if isinstance(created, dict):
        # CORRECTED (session 48): "favorite_id" is now confirmed via
        # FavoriteIdResponse$$serializer's <clinit> -- not just the first of
        # several guessed fallback candidates anymore, though the fallbacks are
        # kept for defensiveness against an unexpected real response shape.
        created_id = created.get("favorite_id") or created.get("favoriteId") or created.get("id")

    if not created_id:
        _skip(
            report,
            "Finding + deleting test favorite in list",
            "no favorite_id recognizable in the create response (response shape unconfirmed) -- "
            "PLEASE CHECK MANUALLY AND DELETE THE TEST FAVORITE BY HAND",
        )
        return

    favorites = await _try(report, "Fetching favorites list again (test favorite should be there)", robot.get_favorites())
    if favorites is not None:
        found = any(getattr(f, "favorite_id", None) == created_id for f in favorites) if isinstance(favorites, list) else False
        report.add("Test favorite found in list", "OK" if found else "FAILED", f"id={created_id}")

    await _try(report, "Deleting test favorite again (delete_favorite)", robot.delete_favorite(created_id))


def build_issue_url(report: Report, repo: str = ISSUE_TRACKER_REPO) -> str:
    """Builds a pre-filled "New issue" URL for GitHub (title + report
    as body, URL-encoded). Works regardless of whether the repo
    already exists -- the link is just a click, no API call, so no
    way for it to fail."""
    ok, failed, skipped = report.summary()
    title = f"Live validation: {ok} OK, {failed} failed, {skipped} skipped"
    body = report.to_markdown()
    return f"https://github.com/{repo}/issues/new?title={quote(title)}&body={quote(body)}"


_AWS_PRESIGNED_URL_SECRETS_RE = re.compile(
    r"([?&](?:X-Amz-Signature|X-Amz-Security-Token|X-Amz-Credential)=)[^&\s'\"]+",
    re.IGNORECASE,
)


def redact_aws_url_secrets(text: str) -> str:
    """NEW (this session, prompted directly by a real leak): more than
    one tester has pasted raw terminal output containing full presigned
    S3 URLs (from live-map/file-transfer messages) with their
    X-Amz-Signature/X-Amz-Security-Token/X-Amz-Credential query
    parameters completely intact -- these grant real, if short-lived
    (~1h expiry), access to the underlying S3 objects. Neither
    Report.redact() nor _redact_raw_capture()'s existing
    sensitive-key-name masking catches this: these URLs are ordinary
    string VALUES (e.g. under "livemap_url"/"url" keys, not literal
    username/password, and "url" isn't a blanket-redacted key name --
    blanking the whole URL would also lose the base path, which IS
    useful for reverse engineering). This strips just the sensitive
    query-string components, keeping the rest of the URL intact.

    Applied both here (as an additional stage inside
    _redact_raw_capture(), for --dump-config output) AND directly at
    print time in every script that prints a raw payload
    (verify_mission_timeline.py's _watch_one(), verify_named_shadows.py,
    verify_mission_commands.py's _show_state()) -- the leak that
    prompted this happened via someone copy-pasting raw terminal
    output directly, which never went through --dump-config's
    redaction path at all."""
    return _AWS_PRESIGNED_URL_SECRETS_RE.sub(r"\1[REDACTED]", text)


def _redact_raw_capture(data: Any, secrets: list[str], _depth: int = 0) -> Any:
    """NEW (session 24) -- redaction for --dump-config. Unlike
    _shallow_summary() (structure only, never values -- for the report
    that automatically flows into the issue link), this function keeps
    actual values, because a dump file exists for exactly this
    purpose: showing real field names AND real values for reverse-
    engineering purposes. Redaction still happens in two stages,
    though:
    1) Every literal occurrence of username/password (see secrets) is
       replaced -- exactly the same defense in depth as
       Report.redact().
    2) Values under obviously sensitive-looking key names (address,
       GPS coordinates, WiFi credentials) are masked entirely,
       regardless of content -- these fields aren't interesting for
       protocol reverse engineering, but they are for privacy.
    3) NEW (this session): AWS presigned-URL secrets
       (X-Amz-Signature/X-Amz-Security-Token/X-Amz-Credential) are
       stripped from any string value, wherever they appear -- see
       redact_aws_url_secrets()'s own docstring for why this was added.
    Still: this file is NEVER automatically part of the issue link --
    whoever shares it should review it themselves first."""
    sensitive_keys = {
        "password",
        "ssid",
        "wifipassword",
        "wifi_password",
        "address",
        "street",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "gps",
        "accesskeyid",
        "secretkey",
        "sessiontoken",
        "email",
        "username",
        # NEW (session 54, security hardening pass): these credential
        # field names exist elsewhere in this codebase
        # (ConnectionToken.iot_token/iot_signature,
        # RobotLoginEntry.user_cert/password, CloudCredentials
        # .cognito_id) but were never added here -- no CURRENT
        # raw_capture call site actually captures a ConnectionToken/
        # RobotLoginEntry/CloudCredentials object, so this was a latent
        # gap rather than an active leak, but this function's whole
        # purpose is to be a general-purpose safety net for whatever
        # gets captured, not just the specific fields anyone happened
        # to test against. Added for defense in depth.
        "iot_token",
        "iot_signature",
        "user_cert",
        "cognitoid",
        # NEW (this session, prompted directly by a real field name
        # found live): ro-configinfo's own "passwordHash" key would
        # NOT have matched the exact-match "password" entry above
        # ("passwordhash" != "password" after lowercasing) -- a real,
        # currently-existing gap found by verifying this function's
        # own coverage against a genuinely new field name, not a
        # hypothetical. Whether it's a hash rather than plaintext
        # doesn't make it safe to leave unredacted by default.
        "passwordhash",
    }
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if k.lower() in sensitive_keys:
                result[k] = "[REDACTED]"
            else:
                result[k] = _redact_raw_capture(v, secrets, _depth + 1)
        return result
    if isinstance(data, list):
        return [_redact_raw_capture(item, secrets, _depth + 1) for item in data]
    if isinstance(data, str):
        redacted = data
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redact_aws_url_secrets(redacted)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live validation of roombapy-prime against a real Prime/V4 account. "
        "Read-only by default -- see the module docstring for the safety principle."
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", default=None, help="Optional: target this specific robot instead of the first one found")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Allows the reversible favorite round-trip test (create+verify+delete). "
        "Mission commands/map editing are unaffected by this -- they're never run.",
    )
    parser.add_argument("--output", default=None, help="Additionally save the report as a markdown file")
    parser.add_argument(
        "--no-issue-link",
        action="store_true",
        help="Don't print/open a GitHub issue link at the end (if you don't want to share).",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Automatically open the issue link in the default browser at the end, instead of just printing it.",
    )
    parser.add_argument(
        "--dump-config",
        default=None,
        metavar="PATH",
        help="Saves the actual (redacted) raw responses from every read endpoint as JSON under "
        "PATH -- similar to a Home Assistant integration's 'Download Diagnostics' feature. "
        "Meant for reverse-engineering/field-name comparison, not everyday use. Redaction removes "
        "credentials and obviously sensitive fields (address, GPS, WiFi credentials) -- ALL OTHER "
        "values remain visible unchanged. Never automatically included in the issue link -- "
        "please review it yourself before sharing.",
    )
    args = parser.parse_args()

    username = args.username or input("Prime account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Password: ")

    print(f"\nroombapy-prime live validation against account for country '{args.country_code}'...")
    if args.allow_writes:
        print("--allow-writes set: a test favorite will be created and deleted again immediately.")
    else:
        print("Read-only mode (default). Use --allow-writes for the additional favorite round-trip test.")
    if args.dump_config:
        print(f"--dump-config set: redacted raw responses will additionally be saved under {args.dump_config}.")

    raw_capture: dict[str, Any] = {} if args.dump_config else None
    report = asyncio.run(run(username, password, args.country_code, args.blid, args.allow_writes, raw_capture))
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Summary: {ok} OK, {failed} failed, {skipped} skipped ==")

    if failed > 0 and not args.dump_config:
        print(
            "\nTip: after a failure, an additional run with --dump-config diagnose.json often "
            "helps -- it saves the actual raw responses (not just pass/fail), which helps with "
            "debugging. Never shared automatically, reviewing it yourself before attaching it is "
            "recommended (see --help)."
        )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Report saved to {args.output}")

    if args.dump_config and raw_capture is not None:
        import json

        redacted = _redact_raw_capture(raw_capture, [username, password])
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(redacted, f, indent=2, default=str, ensure_ascii=False)
        print(f"Redacted raw responses saved to {args.dump_config}")
        print(
            "  Please review this yourself before sharing -- the redaction catches known cases, "
            "but can't guarantee every possible surprise in unfamiliar response shapes."
        )

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Feedback for the maintainers ==")
        print("If you'd like to share this report (helps the library enormously):")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
