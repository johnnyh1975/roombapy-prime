"""Systematic, staged test package for region-aware mission commands
(send_routine_command_via_cmd_topic()) -- the single riskiest,
least-confirmed write path this library has. Read
send_routine_command_via_cmd_topic()'s own docstring in prime_robot.py
first; this script exists specifically to execute THE ACTUAL SAFEST
TEST described there, with as many safety gates around it as this
project's own established conventions call for.

WHY THIS IS RISKIER THAN EVERYTHING ELSE THIS PROJECT HAS LIVE-TESTED
SO FAR: send_simple_command() (start/stop/pause/resume/dock/find) is
CONFIRMED working -- a wrong guess there just produces silence, no
lasting effect. A wrong guess on a region-aware command is different:
the device could accept something malformed but plausible-looking and
behave unpredictably (clean the wrong rooms, run an unexpectedly large
area, etc.) -- not zero risk, unlike the topic-discovery problem this
whole hypothesis descends from.

THE STAGED APPROACH, in order of increasing risk -- ALL FOUR STAGES
ARE NOW IMPLEMENTED, but each stage's own safety gates make clear it
should only be attempted after the PREVIOUS stage is confirmed working
against your specific device:

  Stage 1 (--send): resend an EXISTING favorite's OWN command_def,
  COMPLETELY UNCHANGED. Nothing hand-built, nothing modified. If this
  works, it confirms the transport/schema hypothesis with the lowest
  possible risk -- it should behave EXACTLY like running that favorite
  from the real app, since byte-for-byte the same payload is sent.

  Stage 2 (--send-modified): an existing favorite's command_def, with
  ONE benign, easily-reversible params field changed (e.g. suction
  level) -- everything else (regions, region order/IDs) left
  untouched. Tests whether the robot actually APPLIES a modified
  params value, not just whether the transport accepts a payload.
  routine_modified is set True (see CommandParams.routine_modified's
  own docstring on why this is a computed comparison, not arbitrary --
  True is the correct value here specifically because something WAS
  changed relative to the original favorite).

  Stage 3 (--list-rooms / --send-region): hand-constructed RID/ZID
  regions from REAL room data (get_map_metadata()'s own
  rooms_metadata), no favorite_id at all. Tests whether a genuinely
  from-scratch RoutineCommand (not derived from any existing favorite)
  is accepted. Still avoids TID/ad-hoc regions entirely.

  Stage 4 (--send-adhoc): hand-built TID (ad-hoc/temporary zone)
  regions -- the riskiest tier this project knows about. UNLIKE stages
  1-3, this stage CANNOT be made safe by only using already-real,
  already-confirmed values: the polygon's actual coordinate shape and
  a real furniture_id are both required inputs this script does NOT
  attempt to auto-generate or guess -- see --send-adhoc's own
  docstring for why, and its own ADDITIONAL safety gate beyond the two
  shared by every other stage.

THREE SEPARATE SAFETY GATES, deliberately layered rather than relying
on just one, shared by stages 1-3:
  1. --i-understand-this-will-move-my-robot (same flag/wording as
     verify_mission_commands.py and verify_mission_timeline.py's own
     --start-mission mode -- this script moves the robot too)
  2. --i-understand-this-is-experimental-and-unconfirmed (THIS
     script's own, additional flag -- send_simple_command() itself
     doesn't need this one, since IT is confirmed; this script's
     underlying mechanism is not)
  3. An interactive y/N confirmation, showing the EXACT JSON payload
     that will be sent, immediately before sending it -- the same
     confirm() helper already used elsewhere in this project's
     diagnostic scripts.

WHAT SUCCESS LOOKS LIKE: the robot starts cleaning the same area(s) it
would if you ran that exact favorite from the real app. Watching
mission/timeline/report afterward (this script offers to, reusing the
already-confirmed watch_mission_timeline()) should show the same kind
of live mission events already confirmed elsewhere in this project.

WHAT TO DO IF SOMETHING LOOKS WRONG: send "stop" immediately, either
from the real app, or via `roombapy-prime-verify-mission-commands`'s
own already-confirmed send_simple_command("stop") path in a separate
terminal. This script does not need to be running for that -- stopping
the robot never depends on whatever this script itself is doing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from typing import Any


from ._cli import add_account_arguments, confirm, connected_robot, require_blid, resolve_credentials
from roombapy_prime.diagnostics import Report
from roombapy_prime.models.mission_control import Region, RegionType

_LOGGER = logging.getLogger(__name__)

# Timings in the send path, as module constants so tests can shrink them.
# WHY THIS MATTERS: as hardcoded literals these made six tests take 3-4
# seconds EACH -- 21 of the tools suite's 23 seconds were spent sleeping
# through real time. That cost compounds with every future test touching
# this path, which is the kind of drag that quietly stops people from
# writing tests here at all.
#
# The values themselves are deliberate, not arbitrary:
#   SUBSCRIBE_SETTLE_SECONDS -- there is no "subscription confirmed"
#     signal to await precisely, so a short fixed pause is the safest
#     available option before publishing.
#   STATUS_SNAPSHOT_DELAY_SECONDS -- long enough for a robot-side
#     readiness refusal (a local check, near-instant) to land, short
#     enough to read it before the long watch window buries it.
SUBSCRIBE_SETTLE_SECONDS = 1.0
STATUS_SNAPSHOT_DELAY_SECONDS = 3.0




def _region_types(regions: Any) -> list[str]:
    """Extracts each region's type as a plain string, tolerating both
    typed Region objects (region_type: RegionType) and raw dicts
    (["type"], since command_defs read from a real account could
    contain either -- see RoutineCommand's own docstring on why both
    are accepted throughout this library)."""
    if not regions:
        return []
    types: list[str] = []
    for region in regions:
        if isinstance(region, Region):
            types.append(str(region.region_type))
        elif isinstance(region, dict):
            types.append(str(region.get("type", "?")))
        else:
            types.append("?")
    return types


def _is_safe_command_def(command) -> bool:
    """Stage 1's own eligibility check: every region (if any) must be
    RID or ZID -- a real, persistent room/zone from actual map data.
    ANY TID (ad-hoc/temporary zone) present disqualifies this
    command_def from stage 1 entirely -- see RegionType.TID's own
    docstring for why ad-hoc regions carry extra, unconfirmed
    construction requirements this script deliberately avoids."""
    regions = getattr(command, "regions", None)
    for region_type in _region_types(regions):
        if region_type.lower() == str(RegionType.TID).lower():
            return False
    return True


class _EnvelopedCommand:
    """Stage 1c: sends the SAME CommandDef, but WRAPPED in an envelope
    instead of flattened across the MQTT message's top level.

    HYPOTHESIS DISPROVEN (parallel native-analysis track, shortly after
    this was built) -- kept as a documented dead end rather than
    deleted, and deliberately REMOVED from the automatic session
    runner so it can no longer consume robot-moving test runs. It
    remains reachable via --send-enveloped for anyone who wants to
    confirm the negative themselves.

    What settled it: buildJsonCommon() adds initiator and favorite_id
    at the TOP LEVEL -- exactly the flat shape we already send. And the
    "cmd vs cmdJson, exactly one not both" rule turned out to belong to
    RoombaCleanScheduleMultipleMappingDeserializer, i.e. it is the
    SCHEDULE entry envelope (matching cleanSchedule2's own cmdStr
    field), not an envelope for immediate commands. buildString() also
    turned out to simply call buildJson() -- same content, string vs.
    object, no structural difference.

    The original reasoning, left intact below because the evidence
    behind it was sound and only the conclusion was wrong:

    WHY THIS IS WORTH A STAGE OF ITS OWN. Everything we send today puts
    the CommandDef fields directly at the top level
    ({"command": "start", "regions": [...], ...}). Three independent
    signals suggest the real wire form nests it instead:

      1. MissionCommandBuilder has TWO serializers, buildJson() AND
         buildString() -- two output forms for one object only makes
         sense if there are two target formats.
      2. A previously confirmed "cmd vs cmdJson" dual-field rule
         ("must have exactly one, not both") maps exactly onto those
         two: buildString() -> "cmd" (a string), buildJson() ->
         "cmdJson" (an object).
      3. Strongest, and from REAL data rather than decompilation:
         chairstacker's own cleanSchedule2 entry stores its command in
         a field literally named "cmdStr" -- a STRING, not a nested
         object. A stored schedule keeping the command in string form
         is a strong hint that it travels that way too.

    Our simple commands work flat ({command, time, initiator}) -- but
    that may simply be a narrow shape the firmware handles directly,
    while a full CommandDef needs the envelope.

    Implemented as a thin wrapper rather than a new PrimeRobot method
    on purpose: to_json() is the only thing the whole existing send
    path actually calls, and __getattr__ delegation keeps the
    pre-flight checks (which read .regions/.pmap_version_id) working
    unchanged against the real command underneath."""

    def __init__(self, inner: Any, style: str) -> None:
        if style not in ("cmd", "cmdJson"):
            raise ValueError(f"envelope style must be 'cmd' or 'cmdJson', got {style!r}")
        self._inner = inner
        self._style = style

    def to_json(self) -> dict[str, Any]:
        body = self._inner.to_json()
        if self._style == "cmd":
            # buildString()-equivalent: the whole CommandDef as a JSON
            # STRING inside one field.
            return {"cmd": json.dumps(body, ensure_ascii=False)}
        # buildJson()-equivalent: nested object.
        return {"cmdJson": body}

    def __getattr__(self, name: str) -> Any:
        # Everything except to_json() falls through to the real command,
        # so the pre-flight checks see the genuine regions/map version.
        return getattr(self._inner, name)


async def _preflight_roundtrip_fidelity_check(
    robot, command, favorite_id: str, command_index: int, report: Report,
) -> None:
    """THE most direct test of the newest lead (parallel research):
    the app has TWO command formats -- buildJsonFromCommandDef
    ("modern") and buildJsonLegacy, the latter reading several fields
    our payloads have never contained (map_components,
    linked_mission_id, multi_polygons, smart_clean_id). Which one gets
    built is decided by a bool whose origin could not be resolved
    statically.

    We cannot see that decision. But we CAN check something adjacent
    and arguably more useful: does OUR round-trip lose anything? We
    fetch a favorite, parse it into typed models, then re-serialize it
    to send. Any field the stored favorite carries that our models
    don't know about is silently DROPPED in that round-trip -- and we
    would then resend a command subtly less complete than what the app
    sends. That failure mode looks exactly like this project's central
    symptom: structurally valid, no effect, no error.

    Compares raw vs. re-serialized keys at both the command_def level
    and inside each region. Reports FACTS -- added keys (initiator,
    favorite_id) are expected and ignored; only DROPPED keys matter."""
    try:
        raw_favorites = await robot.get_favorites_raw()
    except Exception as exc:  # noqa: BLE001
        report.add("Pre-flight: round-trip fidelity", "SKIPPED", f"raw favorites unavailable: {exc}")
        return

    raw_fav = next(
        (f for f in raw_favorites if f.get("favorite_id") == favorite_id or f.get("id") == favorite_id),
        None,
    )
    if raw_fav is None:
        report.add("Pre-flight: round-trip fidelity", "SKIPPED", "favorite not found in raw response")
        return

    # "commanddefs", all lowercase, is the CONFIRMED wire key (see
    # FavoriteV1's own to_json). The two camel/snake variants below were
    # guesses, and because neither ever matched, this check silently
    # reported "raw command_def not found" on every real run it has ever
    # had -- it has never actually compared anything.
    raw_defs = (
        raw_fav.get("commanddefs")
        or raw_fav.get("command_defs")
        or raw_fav.get("commandDefs")
        or []
    )
    if command_index >= len(raw_defs):
        report.add("Pre-flight: round-trip fidelity", "SKIPPED", "raw command_def not found")
        return
    raw_def = raw_defs[command_index]
    ours = command.to_json()

    dropped_top = sorted(set(raw_def) - set(ours))

    dropped_region: set[str] = set()
    raw_regions = raw_def.get("regions") or []
    our_regions = ours.get("regions") or []
    for raw_region, our_region in zip(raw_regions, our_regions, strict=False):
        if isinstance(raw_region, dict) and isinstance(our_region, dict):
            dropped_region |= set(raw_region) - set(our_region)

    if dropped_top or dropped_region:
        report.add(
            "Pre-flight: round-trip fidelity", "FAILED",
            f"our re-serialized command DROPS fields the stored favorite actually has -- "
            f"command_def level: {dropped_top or 'none'}, region level: {sorted(dropped_region) or 'none'}. "
            "These are fields our models don't know about, so they vanish on the way out. "
            "Strongly worth reporting -- this is exactly how a structurally-valid-but-ineffective "
            "command could arise.",
        )
    else:
        report.add(
            "Pre-flight: round-trip fidelity", "OK",
            "re-serialized command preserves every field the stored favorite carries",
        )


def preflight_target_robot_check(command, blid: str, report: Report) -> None:
    """Checks that the favorite we are about to resend actually belongs
    to the robot we are sending it to.

    FOUND IN THE FIELD (DaRealGuGu, v0.1.11a22) and not something anyone
    thought to check: his login BLID was a 16-character
    "3178480C91223620" while the stored favorite's own robot_id was a
    32-character "0B710054CA277C04B2700374A8349C9A" -- and the
    favorite's p2map_id carried that same 32-character prefix, not the
    BLID's.

    We publish to {prefix}/things/{BLID}/cmd. If those two identifiers
    name different robots -- two devices on one account being the
    obvious way that happens -- then the command goes to a robot that
    has never heard of that map or those regions, and "no reaction" is
    the only possible outcome. No protocol mystery required.

    Reports rather than blocks: the two identifiers may simply live in
    different namespaces on some device generations (jayjay13011's
    account has them identical, so at least sometimes they are the same
    thing). Stating the mismatch plainly lets a human judge it; refusing
    to send would be presumptuous on evidence this thin."""
    robot_id = getattr(command, "asset_id", None)
    if not robot_id:
        report.add("Pre-flight: target robot", "SKIPPED", "command carries no robot_id")
        return
    if robot_id == blid:
        report.add(
            "Pre-flight: target robot", "OK",
            f"favorite's robot_id matches the BLID being published to ({blid})",
        )
        return
    report.add(
        "Pre-flight: target robot", "FAILED",
        f"the favorite says robot_id={robot_id!r}, but this command is being published to "
        f"BLID={blid!r} -- these are NOT the same identifier. If your account has more than "
        "one robot, the command may be going to a different one than the favorite was made "
        "for, which would explain no reaction entirely. Worth reporting either way: it is "
        "not yet known whether these two are always meant to match.",
    )


async def run_session_preflight_checks(
    robot, command, favorite_id: str, command_index: int, report: Report,
) -> None:
    """The two pre-flight checks whose inputs do NOT change between
    stages of one session: the favorite's map version, and whether our
    own round-trip drops fields the stored favorite carries.

    SESSION-LEVEL ON PURPOSE. These used to run before every single
    send, which meant three identical get_active_map_versions() calls
    per session for data that cannot have changed in between -- on a
    connection where a tester has already hit server-side throttling.
    The per-send checks (pad vs. mode, mission status) stay per-send,
    because those genuinely can change: a pad can be fitted between
    stages, and the mission status is the whole point of a before/after
    comparison.

    Call once, after picking the favorite and before the first send."""
    preflight_target_robot_check(command, getattr(robot, "blid", "") or "", report)
    await _preflight_map_version_check(robot, command, report)
    await _preflight_roundtrip_fidelity_check(
        robot, command, favorite_id, command_index, report
    )


async def _preflight_map_version_check(robot, command, report: Report) -> None:
    """HYPOTHESIS A from the parallel APK research, made checkable
    without moving the robot at all: RobotReadinessState 22 is
    MAP_VERSION_MISMATCH. A stored favorite carries the map version
    that was current WHEN IT WAS SAVED (user_p2mapv_id), but the robot
    re-maps and re-versions over time. If the favorite now points at a
    superseded version, applyConditionalChecks() would refuse the
    command -- silently, exactly matching this project's symptom (no
    effect, no error anywhere we look).

    Compares the outgoing command's user_p2mapv_id against
    active_p2mapv_id from get_active_map_versions() (a confirmed
    P2MapData field). Reports FACTS only -- a mismatch is a strong
    lead, not proof, and this deliberately does NOT auto-correct the
    value: silently sending something different from what the user
    asked to send would undermine the whole point of a staged,
    show-the-exact-payload test script."""
    stored_version = getattr(command, "pmap_version_id", None)
    if not stored_version:
        report.add("Pre-flight: map version", "SKIPPED", "command carries no user_p2mapv_id")
        return
    try:
        versions = await robot.get_active_map_versions()
    except Exception as exc:  # noqa: BLE001
        report.add("Pre-flight: map version", "SKIPPED", f"could not fetch active versions: {exc}")
        return

    active = {
        getattr(v, "active_p2mapv_id", None)
        for v in (versions or [])
        if getattr(v, "active_p2mapv_id", None)
    }
    if not active:
        report.add("Pre-flight: map version", "SKIPPED", "no active_p2mapv_id reported")
    elif stored_version in active:
        report.add(
            "Pre-flight: map version", "OK",
            f"favorite's user_p2mapv_id {stored_version!r} matches a currently active version",
        )
    else:
        report.add(
            "Pre-flight: map version", "FAILED",
            f"favorite carries user_p2mapv_id {stored_version!r}, but the currently active "
            f"version(s) are {sorted(active)!r} -- a MAP_VERSION_MISMATCH "
            "(RobotReadinessState 22) is a strong candidate for why this command has no effect. "
            "Re-saving the favorite in the real app would refresh it.",
        )


def preflight_pad_vs_mode_check(command, reported: dict | None, report: Report) -> None:
    """HYPOTHESIS B from the parallel APK research: RobotReadinessState
    75 (NO_VAC_WITH_PAD) and 76 (NO_MOP_WITHOUT_PAD) suggest the robot
    refuses a command whose operating mode doesn't match the physically
    attached pad. jayjay13011's favorites all requested operatingMode
    32 (VAC_MOP_COMBO_ONLY) -- a mopping mode -- so running that with
    no pad fitted would be refused, silently, matching this project's
    symptom exactly.

    DELIBERATELY REPORTS, DOES NOT JUDGE, except in one unambiguous
    case. The same research established that this check runs
    ROBOT-side, not app-side (the app only ever PARSES an incoming
    readiness value -- see RoombaMissionStatusDeserializer's
    "UNEXPECTED READINESS STATE: (%d)"), so we cannot reproduce the
    rule, only observe its inputs. And the exact value set of
    ro-currentstate.detectedPad is NOT confirmed for Prime: real
    Classic data shows simpler values ("reusable", "wet") than the
    REST-side PadCategory vocabulary, so a strict comparison would
    risk confident false alarms. Showing the operator both inputs
    side by side is honest and useful; pretending to know the rule
    would not be."""
    from roombapy_prime.models import OperatingModeBitmask

    modes: set[int] = set()
    for region in (getattr(command, "regions", None) or []):
        # Regions arriving from a stored favorite are raw dicts, not
        # typed objects -- getattr() on a dict silently returns the
        # default, which is why this reported "no operatingMode in
        # regions" for a payload that visibly contained one on every
        # region (DaRealGuGu, v0.1.11a22).
        params = region.get("params") if isinstance(region, dict) else getattr(region, "params", None)
        mode = (
            params.get("operatingMode") if isinstance(params, dict)
            else getattr(params, "operating_mode", None)
        )
        if mode:
            modes.add(int(mode))

    if not modes:
        report.add("Pre-flight: pad vs. operating mode", "SKIPPED", "no operatingMode in regions")
        return

    if reported is None:
        report.add("Pre-flight: pad vs. operating mode", "SKIPPED", "ro-currentstate not readable")
        return
    detected_pad = reported.get("detectedPad")

    decoded = {m: str(OperatingModeBitmask(m)) for m in sorted(modes)}
    mop_involving = {
        OperatingModeBitmask.MOP_ONLY, OperatingModeBitmask.VAC_MOP_COMBO_ONLY,
        OperatingModeBitmask.SCRUBBING, OperatingModeBitmask.MOPPING,
        OperatingModeBitmask.VAC_THEN_MOP,
    }
    wants_mopping = any(OperatingModeBitmask(m) & mode for m in modes for mode in mop_involving)

    pad_clearly_absent = str(detected_pad or "").lower() in ("nopad", "none", "invalid", "")

    if wants_mopping and pad_clearly_absent:
        report.add(
            "Pre-flight: pad vs. operating mode", "FAILED",
            f"regions request a mopping mode ({decoded}) but detectedPad={detected_pad!r} "
            "indicates no pad fitted -- NO_MOP_WITHOUT_PAD (RobotReadinessState 76) is a strong "
            "candidate for a silent robot-side refusal. Fitting a pad and retrying would test it.",
        )
    else:
        report.add(
            "Pre-flight: pad vs. operating mode", "OK",
            f"requested mode(s) {decoded}, detectedPad={detected_pad!r} -- reported for context; "
            "the actual compatibility rule lives robot-side and cannot be checked here.",
        )


async def fetch_current_state(robot) -> dict | None:
    """Fetches ro-currentstate's reported block once, for whichever
    checks need it.

    EXTRACTED (this session) because the pad/mode pre-flight and the
    before-send status snapshot each fetched this SAME shadow, back to
    back, microseconds apart. That is not just wasteful -- it is a real
    hazard here: a field tester already hit server-side throttling
    where only 3 of 8 shadow requests came through, and diagnostics
    that crowd out the very traffic they are measuring would be the
    worst possible failure mode for this script. One fetch, several
    readers.

    Returns the raw reported dict rather than a parsed model, because
    its consumers want different slices of it (one wants detectedPad,
    another cleanMissionStatus, a third a field we have never seen in
    a capture and therefore cannot model)."""
    try:
        response = await robot.get_named_shadow("ro-currentstate")
        return (response.payload or {}).get("state", {}).get("reported", {})
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("roombapy-prime: could not fetch ro-currentstate: %s", exc)
        return None


def mission_status_from(reported: dict | None) -> dict | None:
    """NEW (this session, directly acting on the parallel APK-research
    chat's strongest finding): the app's own
    CloudCapableMissionUIService::applyConditionalChecks() runs a
    READINESS check over a CommandDef's regions before the command
    takes effect, and a refusal surfaces as a ResolvedMissionStatus
    value (7/8/12/13 = the various *_START_REFUSE states) with
    reasons in a vector<RobotReadinessState> -- i.e. in the MISSION
    STATUS, not on rejected/report.

    That would explain this project's central open mystery exactly:
    a region command that produces neither an effect NOR any error
    anywhere we've been looking. We already model the two wire fields
    that would carry it (CleanMissionStatus.not_ready and
    .cond_not_ready) -- we simply never read them during a test.

    Returns the handful of cleanMissionStatus fields worth comparing
    before/after a send, or None if the shadow can't be fetched (never
    fatal -- this is diagnostics on top of the actual test, and must
    not be able to break it)."""
    from roombapy_prime.models import CurrentStateShadow

    if reported is None:
        return None
    try:
        status = CurrentStateShadow.from_json(reported).clean_mission_status
        if status is None:
            return None
        return {
            # NEW (parallel research): regions_left would directly show
            # whether a REGION-based mission actually started -- the
            # single most on-point field for this project's core open
            # question. Read straight from the raw payload, since it is
            # not modelled on CleanMissionStatus yet (no capture has
            # contained it, so there is nothing to model it from).
            "regions_left": (reported.get("cleanMissionStatus") or {}).get("regions_left"),
            "phase": status.phase,
            "cycle": status.cycle,
            "error": status.error,
            "not_ready": status.not_ready,
            "cond_not_ready": status.cond_not_ready,
            "mission_id": status.mission_id,
            "initiator": status.initiator,
        }
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("roombapy-prime: could not parse mission status: %s", exc)
        return None


def _report_mission_status(report: Report, before: dict | None, after: dict | None) -> None:
    """Prints/records the before/after mission status around a send.
    Deliberately reports FACTS, not a verdict -- see
    mission_status_from()'s docstring for what a refusal would
    look like, but whether a given not_ready value IS a refusal is
    still a judgment for whoever reads it, not something this asserts."""
    if before is None and after is None:
        report.add("Mission status", "SKIPPED", "ro-currentstate not readable this run")
        return

    print("\n== Mission status around the send ==")
    for label, snap in (("before", before), ("after", after)):
        print(f"  {label:>6}: {snap}")

    after = after or {}
    not_ready = after.get("not_ready")
    cond_not_ready = after.get("cond_not_ready") or []

    if not_ready or cond_not_ready:
        from roombapy_prime.models import RobotReadinessState

        # NEW (this session): name the codes instead of printing raw
        # ints. RobotReadinessState is deliberately partial (only the
        # values actually confirmed by the research are listed), so an
        # unrecognized value comes back as UNKNOWN_<n> rather than a
        # guessed label -- see that enum's own docstring.
        named = RobotReadinessState.name_for(not_ready)
        named_conds = [
            RobotReadinessState.name_for(c) if isinstance(c, int) else c
            for c in cond_not_ready
        ]
        report.add(
            "Mission status after send", "FAILED",
            f"not_ready={not_ready!r} ({named}), cond_not_ready={named_conds!r} -- this is EXACTLY "
            "the shape a readiness-based start refusal would take (see the parallel APK research: "
            "applyConditionalChecks/ResolvedMissionStatus 7/8/12/13). Strongly worth reporting.",
        )
    elif before != after:
        report.add(
            "Mission status after send", "OK",
            f"changed: {before!r} -> {after!r} -- the command reached something that reacted",
        )
    else:
        report.add(
            "Mission status after send", "OK",
            "unchanged, and no readiness refusal reported -- consistent with the command being "
            "accepted-and-ignored, or never reaching the mission layer at all",
        )


async def _confirm_show_send_watch(
    robot, command, report: Report, watch_seconds: int, description: str,
    disconnect_after: bool = True,
) -> tuple[list, list]:
    """Shared final step for every stage: show the exact payload,
    require interactive confirmation, subscribe to mission/timeline/
    report AND rejected/report, THEN send, then keep watching. Used
    identically by stages 1-4 -- the only thing that differs between
    stages is HOW `command` was constructed before reaching this
    point.

    RETURNS (timeline_events, rejected_events) (this session) --
    previously returned just a plain list of timeline events. Every
    existing caller either ignores the return value (the four
    standalone stage functions) or has been updated to unpack the
    tuple (verify_region_commands_session.py).

    NOW ALSO WATCHES watch_rejected_commands() (this session) --
    genuinely never done before in this script, despite the method
    existing and already being proven functional elsewhere
    (verify_mission_timeline.py's own combined watch). Every region-
    command test so far has only watched mission/timeline/report,
    which would show nothing at all if the server silently rejects a
    malformed/incomplete command rather than the robot simply
    ignoring an accepted one -- two different findings this project's
    prior "nothing happened" results have never actually
    distinguished between.

    REAL RACE CONDITION FOUND AND FIXED (this session): this used to
    SEND the command FIRST, then start watching -- but _watch_topic()
    (prime_robot.py) subscribes fresh on every call, not from a
    persistent subscription held since connect(). A response arriving
    faster than the time it takes this function to start its two
    watch loops afterward would have been silently missed entirely --
    plausible for a REJECTION specifically, which could come back in
    milliseconds (a schema/validation check), far faster than a
    physical robot could ever react. Every prior region-command test
    subscribed only AFTER already sending. Now subscribes first (as
    background tasks), waits a short settle period for the
    subscriptions to actually establish with the broker, THEN sends,
    THEN lets the same tasks keep running for watch_seconds."""
    payload = command.to_json()
    # REAL DISPLAY BUG FOUND AND FIXED (this session): what we printed
    # was command.to_json() -- but publish_cmd_payload() then adds a
    # "time" field (setdefault, mqtt_client.py) just before publishing.
    # The displayed payload was therefore NOT the payload that actually
    # went out. That is exactly the kind of gap that produces confident
    # wrong conclusions from otherwise careful analysis: a parallel
    # research pass compared a field tester's printed payload against
    # the app's own builder, found no "time", and reasonably concluded
    # it was missing -- when it was there on the wire all along, just
    # never shown. Display now mirrors what publish actually sends.
    display_payload = {**payload}
    display_payload.setdefault("time", int(time.time()))
    print(f"\n{description}")
    print(json.dumps(display_payload, indent=2, ensure_ascii=False))
    print(
        "  (\"time\" is stamped by publish_cmd_payload() at send; the exact value will be "
        "the moment of sending, a second or two after this preview)"
    )

    if not confirm("\nSend this EXACT payload now? This will move the robot."):
        print("Aborted by user -- nothing sent.")
        return [], []

    events: list = []
    rejected: list = []

    async def _watch_timeline() -> None:
        async for event in robot.watch_mission_timeline():
            print(f"  [timeline] {event}")
            events.append(event)

    async def _watch_rejected() -> None:
        async for response in robot.watch_rejected_commands():
            print(f"  ** REJECTED ** {response}")
            rejected.append(response)

    timeline_task: asyncio.Task | None = None
    rejected_task: asyncio.Task | None = None
    if watch_seconds > 0:
        print(
            "\n== Subscribing to mission/timeline/report and rejected/report BEFORE "
            "sending (a fast response, especially a rejection, could otherwise arrive "
            "before we're listening) =="
        )
        timeline_task = asyncio.create_task(_watch_timeline())
        rejected_task = asyncio.create_task(_watch_rejected())
        # Give the subscribe() calls a moment to actually reach the
        # broker before sending -- there's no "subscription confirmed"
        # signal to await precisely here, so a short, fixed settle
        # period is the safest available option.
        await asyncio.sleep(SUBSCRIBE_SETTLE_SECONDS)

    # ONE fetch feeding both pre-send readers -- see fetch_current_state()'s
    # own docstring for why crowding the shadow endpoint is a real hazard
    # here and not just wasteful.
    reported_before = await fetch_current_state(robot)
    preflight_pad_vs_mode_check(command, reported_before, report)
    status_before = mission_status_from(reported_before)

    print("\n== Sending ==")
    broker_confirmed = await robot.send_routine_command_via_cmd_topic(command)
    if broker_confirmed:
        report.add(
            "send_routine_command_via_cmd_topic()", "OK",
            "broker confirmed receipt (PUBACK) -- see mqtt_client.py's publish_cmd_payload() "
            "docstring for why this matters: it rules out a silent broker-level drop, leaving "
            "only 'robot received it but ignored it' as the remaining explanation if nothing "
            "else happens below",
        )
    else:
        report.add(
            "send_routine_command_via_cmd_topic()", "FAILED",
            "broker did NOT confirm receipt (no PUBACK within the timeout). CHECK THE OUTPUT "
            "ABOVE FIRST: if the connection dropped or reconnected during this run, that alone "
            "explains a missing PUBACK and this says nothing about the payload. Only on an "
            "otherwise clean run does this point at a policy/ACL-level block. (A client-side "
            "crash in our own SUBACK handling produced exactly this false signal for one "
            "tester in v0.1.11a22 -- fixed since, but worth checking rather than assuming.)",
        )

    # Snapshot the mission status shortly after sending -- deliberately
    # BEFORE the long watch window, since a readiness refusal is
    # expected to be near-instant (a local check, not a mission), and
    # could well have cleared again by the time a 60s window ends.
    await asyncio.sleep(STATUS_SNAPSHOT_DELAY_SECONDS)
    status_after = mission_status_from(await fetch_current_state(robot))
    _report_mission_status(report, status_before, status_after)

    if watch_seconds > 0:
        print(f"\n== Watching for {watch_seconds}s (already subscribed since before sending) ==")
        print("(Ctrl+C to stop watching early -- the command has already been sent either way)")
        try:
            async with asyncio.timeout(watch_seconds):
                await asyncio.gather(timeline_task, rejected_task)
        except TimeoutError:
            pass
        except KeyboardInterrupt:
            pass
        except Exception:  # noqa: BLE001 -- watch_rejected_commands() is
            # EXPLORATORY (see its own docstring, prime_robot.py) -- a
            # failure watching it (e.g. ValueError if irbt_topic_prefix
            # is unexpectedly missing) must not take down the
            # already-working mission-timeline watch alongside it.
            _LOGGER.exception("roomba_prime: watch_rejected_commands() failed during this test")
        finally:
            for task in (timeline_task, rejected_task):
                if task is not None and not task.done():
                    task.cancel()
        print(_summarize_events(events))
        if rejected:
            print(
                f"\n== {len(rejected)} REJECTION(S) received -- see above for the raw "
                "response(s). This is a genuinely new finding if it happens -- no prior "
                "region-command test has ever watched this channel. =="
            )
        else:
            # CORRECTED (this session, per the parallel APK-research
            # chat's own finding): rejected/report is published BY THE
            # ROBOT when it receives and rejects a command. If the
            # broker's IoT policy silently dropped the publish before
            # it ever reached the robot, the robot never saw it and
            # therefore couldn't reject it either -- "no rejection"
            # was previously worded as if it meant "silently ignored
            # by the robot", but it's equally consistent with "never
            # delivered at all". The PUBACK result above is what
            # actually distinguishes these two.
            if broker_confirmed:
                print(
                    "\nNo rejection received on rejected/report -- and the broker DID confirm "
                    "delivery (PUBACK), so this is NOT explained by a silent policy-level drop. "
                    "Most likely explanation now: the robot received the command but didn't "
                    "act on it (or acted in a way that doesn't reach rejected/report)."
                )
            else:
                print(
                    "\nNo rejection received on rejected/report -- but the broker did NOT "
                    "confirm delivery (PUBACK) either. This is consistent with the publish "
                    "never reaching the robot at all (a policy/ACL-level block) -- 'no "
                    "rejection' here does NOT mean 'silently ignored by the robot'."
                )

    if disconnect_after:
        await robot.disconnect()
    return events, rejected


def _summarize_events(events: list) -> str:
    """Pulls out the specific fields that actually matter for judging
    whether a region-targeted command worked, from the raw
    MissionTimelineEvent list _confirm_show_send_watch() captured --
    rather than leaving a human to parse repr() output live in a
    terminal. Deliberately reports FACTS only (what fields were
    present and what they said), not a verdict -- "did this work" is
    still a judgment call for whoever watched the robot, this just
    makes the judgment easier to make correctly.

    NEW (this session), built specifically because "zero events in
    the watch window" (chairstacker/jayjay13011's real stage 1/1b
    results) and "events arrived but don't mention the requested
    region" are two different findings that raw printing didn't
    distinguish clearly enough."""
    if not events:
        return (
            "\n== Summary: NO events observed during the watch window ==\n"
            "This matches what stage 1 showed for both chairstacker and jayjay13011 -- "
            "consistent with \"nothing happened\", not proof of it (a real event could "
            "still arrive after the watch window closed)."
        )

    lines = [f"\n== Summary: {len(events)} event(s) observed =="]
    for event in events:
        event_type = getattr(event, "event_type", None)
        parts = [f"  [{event_type}]"]
        command_ev = getattr(event, "command", None)
        if command_ev is not None:
            parts.append(
                f"command={getattr(command_ev, 'command', None)!r} "
                f"initiator={getattr(command_ev, 'initiator', None)!r}"
            )
        room_ev = getattr(event, "room", None)
        if room_ev is not None:
            parts.append(
                f"region_id={getattr(room_ev, 'region_id', None)!r} "
                f"area={getattr(room_ev, 'area', None)!r} "
                f"total_area={getattr(room_ev, 'total_area', None)!r}"
            )
        zone_ev = getattr(event, "zone", None)
        if zone_ev is not None:
            parts.append(
                f"zone_id={getattr(zone_ev, 'zone_id', None)!r} "
                f"area={getattr(zone_ev, 'area', None)!r} "
                f"total_area={getattr(zone_ev, 'total_area', None)!r}"
            )
        error_ev = getattr(event, "error", None)
        if error_ev is not None:
            parts.append(f"** ERROR value={getattr(error_ev, 'value', None)!r} **")
        lines.append(" ".join(parts))
    return "\n".join(lines)


async def list_favorites(username: str, password: str, country_code: str, blid: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing to the robot.
    Lists every favorite and every command_def within it, flagging
    which ones are eligible for stage 1 (no TID regions) and which
    aren't, so a tester can pick a safe target before touching --send
    at all."""
    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):
        favorites = await robot.get_favorites()

    if not favorites:
        print("No favorites found on this account for this robot.")
        return

    print(f"\n{len(favorites)} favorite(s) found:\n")
    for favorite in favorites:
        print(f"favorite_id={favorite.favorite_id!r}  name={favorite.name!r}")
        if not favorite.command_defs:
            print("  (no command_defs)")
            continue
        for i, command in enumerate(favorite.command_defs):
            region_types = _region_types(getattr(command, "regions", None))
            eligible = _is_safe_command_def(command)
            tag = "STAGE-1 ELIGIBLE" if eligible else "CONTAINS TID -- NOT eligible for stage 1/2"
            print(f"  [{i}] command_type={getattr(command, 'command_type', '?')!r} regions={region_types or '(none)'} -- {tag}")
    print(
        "\nTo test one: roombapy-prime-verify-region-commands --send FAVORITE_ID "
        "--command-index N --i-understand-this-will-move-my-robot "
        "--i-understand-this-is-experimental-and-unconfirmed"
    )


async def send_stage_one(
    username: str,
    password: str,
    country_code: str,
    blid: str,
    favorite_id: str,
    command_index: int,
    watch_seconds: int,
) -> None:
    """Stage 1: resend an existing favorite's own command_def exactly
    as stored -- see this module's own docstring for the full
    staged-risk reasoning.

    REAL GAP FOUND AND FIXED (this session, re-analyzing this
    project's own prior research after two negative field results):
    the command_def as stored on a favorite apparently never carries
    its OWN favorite_id (that lives on the parent favorite object, not
    copied down) -- but send_routine_command_via_cmd_topic()'s own
    docstring already confirmed, via the real app's own
    RoutineCommandBuilder, that setFromFavorite() always sends
    favorite_id together with the resolved command_defs. Resending
    just the command_def, without adding favorite_id back, was never
    actually byte-for-byte what the real app sends when replaying a
    favorite -- see _add_favorite_id_if_missing()'s own docstring for
    the full finding. This completes stage 1 to match that confirmed
    real behavior; it isn't a new modification of the kind stage 1's
    own "completely unchanged" promise is about (suction level,
    regions, etc. remain untouched)."""
    async with connected_robot(
        username, password, country_code, blid, connect_mqtt=True
    ) as (robot, report):

        print("\n== Fetching favorites ==")
        favorites = await robot.get_favorites()
        favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
        if favorite is None:
            print(f"ERROR: no favorite with favorite_id={favorite_id!r} found on this account.")
            return
        if not favorite.command_defs or command_index >= len(favorite.command_defs):
            print(f"ERROR: favorite {favorite_id!r} has no command_defs[{command_index}].")
            return
        original = favorite.command_defs[command_index]

        if not _is_safe_command_def(original):
            print(
                "ABORTED: this command_def contains a TID (ad-hoc/temporary) region. "
                "This is stage 1 (RID/ZID regions only, completely unchanged) -- see "
                "--send-adhoc for the separate, higher-risk stage 4 path instead."
            )
            return

        command = build_stage_one_command(original, favorite_id)

        await _confirm_show_send_watch(
            robot, command, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] -- as stored, favorite_id added to match "
            "the real app's own confirmed behavior, nothing else changed:",
        )



def build_stage_one_command(original, favorite_id: str):
    """Stage 1's outgoing command: the favorite's own command_def, with
    favorite_id restored.

    THE THREE build_stage_*_command() FUNCTIONS EXIST TO BE SHARED.
    Previously the standalone stage functions and the session runner
    each composed these additions by hand, and the two copies drifted
    every single time something changed: adding favorite_id, adding
    initiator to stages 2/3, and removing the envelope experiment each
    had to be done twice, and each was at some point done only once.
    Keeping the composition in one pure function -- no I/O, no report,
    just command in, command out -- means a change lands in both
    callers or neither."""
    return _add_favorite_id_if_missing(original, favorite_id) or original


def build_stage_one_b_command(original, favorite_id: str):
    """Stage 1b: stage 1 plus an initiator, if the favorite has none.

    Returns None when the favorite ALREADY carries an initiator -- in
    that case stage 1b would be byte-identical to stage 1, and callers
    should say so rather than sending a duplicate."""
    with_initiator = _add_initiator_if_missing(original)
    if with_initiator is None:
        return None
    return _add_favorite_id_if_missing(with_initiator, favorite_id) or with_initiator


def build_stage_two_command(original, favorite_id: str, suction_level: int):
    """Stage 2: stage 1b plus one changed suction level. Returns
    (command, original_level) so callers can report what changed."""
    modified, original_level = _build_modified_command(original, suction_level)
    command = _add_initiator_if_missing(modified) or modified
    command = _add_favorite_id_if_missing(command, favorite_id) or command
    return command, original_level


def _add_favorite_id_if_missing(original, favorite_id: str) -> object | None:
    """NEW (this session, real gap found while re-analyzing this
    project's own prior research): confirmed directly through the
    real app's own RoutineCommandBuilder (see
    send_routine_command_via_cmd_topic()'s own docstring,
    prime_robot.py) -- setFromFavorite(favoriteId, commandDefs) stores
    BOTH the favorite_id AND the favorite's resolved command_defs, and
    build() sends them together. RoutineCommand.to_json() has
    supported emitting "favorite_id" since it was written (see its own
    to_json() -- `if self.favorite_id is not None: body["favorite_id"]
    = self.favorite_id`) -- but NOTHING in this script's stages 1/1b/2
    ever actually SET it on the command being sent, despite fetching
    the favorite (and therefore knowing its real favorite_id) in every
    one of them. Every real payload shown by any field tester so far
    (chairstacker, jayjay13011) is missing this field entirely.

    For stage 1 specifically, this isn't "changing" the command
    relative to stage 1's own "completely unchanged" promise -- the
    favorite's OWN command_defs entry, as stored, apparently never
    carries its parent favorite's id (that lives one level up, on the
    favorite object itself, not copied into each command_def) -- so
    resending the command_def alone was never actually byte-for-byte
    what the real app sends when replaying that favorite. Adding this
    completes stage 1 to match the app's own confirmed behavior,
    rather than deviating from it.

    Same "only fill in if missing" contract as
    _add_initiator_if_missing(): returns None if already set (nothing
    to add), otherwise the command with favorite_id added, everything
    else unchanged."""
    import dataclasses

    if original.favorite_id is not None:
        return None
    return dataclasses.replace(original, favorite_id=favorite_id)


def _add_initiator_if_missing(original) -> object | None:
    """Stage 1b's core logic, pulled out of the async I/O so it's
    directly unit-testable -- same lesson as this project's other
    staged scripts' own _build_modified_command()-style helpers: an
    executing test catches real construction bugs a syntax check
    cannot. Returns None if initiator was ALREADY set (nothing to add
    -- caller should treat this as "use --send instead"), otherwise
    the command with initiator="rmtApp" added, everything else
    unchanged.

    CORRECTED (this session, real capture from chairstacker's
    raw_shadows.json): this used to default to "localApp" -- borrowed
    from send_simple_command()'s own default, which is itself
    documented (mqtt_client.py's publish_cmd()) as CLASSIC's literal
    observed value for a local-MQTT connection, never independently
    confirmed as a value real Prime traffic uses. chairstacker's own
    rw-software shadow shows a real, live PRIME lastCommand.initiator
    of "rmtApp" (for an app-triggered stoppaddry command) -- the first
    actual evidence of what a Prime device itself reports for this
    field, and a stronger candidate than a value borrowed from a
    different product line's own local-transport convention."""
    import dataclasses

    if original.initiator is not None:
        return None
    return dataclasses.replace(original, initiator="rmtApp")


async def send_stage_one_with_initiator(
    username: str,
    password: str,
    country_code: str,
    blid: str,
    favorite_id: str,
    command_index: int,
    watch_seconds: int,
) -> None:
    """Stage 1b -- CONFIRMED FINDING (chairstacker, real device test):
    stage 1's own real-world first attempt produced no observable
    effect, and the actual payload sent had NO "initiator" field at
    all -- the stored favorite's own command_def had initiator=None,
    and RoutineCommand.to_json() omits the field entirely when unset.
    This matters because the ORIGINAL hypothesis behind this whole
    transport was that "command" AND "initiator" are shared keys
    between the confirmed-working simple-command payload
    ({"command", "time", "initiator": "localApp"}) and RoutineCommand's
    own schema -- stage 1's own real test accidentally exercised a
    version of the hypothesis missing that second shared field, not
    the full hypothesis as originally reasoned.

    This stage tests the natural next, still-minimal step: identical
    to stage 1 in every other way (same favorite, same command_def,
    completely unchanged otherwise), with ONLY initiator explicitly
    set to "rmtApp" -- purely additive (supplies a value where none
    existed, does not override anything that was actually set). See
    _add_initiator_if_missing()'s own docstring for why "rmtApp", not
    the earlier "localApp"."""
    async with connected_robot(
        username, password, country_code, blid, connect_mqtt=True
    ) as (robot, report):

        print("\n== Fetching favorites ==")
        favorites = await robot.get_favorites()
        favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
        if favorite is None:
            print(f"ERROR: no favorite with favorite_id={favorite_id!r} found on this account.")
            return
        if not favorite.command_defs or command_index >= len(favorite.command_defs):
            print(f"ERROR: favorite {favorite_id!r} has no command_defs[{command_index}].")
            return
        original = favorite.command_defs[command_index]

        if not _is_safe_command_def(original):
            print(
                "ABORTED: this command_def contains a TID (ad-hoc/temporary) region. "
                "This is stage 1b (RID/ZID regions only) -- see --send-adhoc for the "
                "separate, higher-risk stage 4 path instead."
            )
            return

        await run_session_preflight_checks(
            robot, original, favorite_id, command_index, report
        )

        command = build_stage_one_b_command(original, favorite_id)
        if command is None:
            print(
                f"This favorite's command_def already has initiator={original.initiator!r} set "
                "-- stage 1b has nothing to add here (it was designed for the initiator=None "
                "case). Use --send instead; this would be identical to that."
            )
            return

        await _confirm_show_send_watch(
            robot, command, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] with initiator added (was unset -> \"rmtApp\") "
            "and favorite_id added to match the real app's own confirmed behavior, "
            "nothing else changed:",
        )

    print(
        "\nIf the robot is doing something unexpected: send 'stop' now, either from the "
        "real app or via roombapy-prime-verify-mission-commands in a separate terminal."
    )


def _build_modified_command(original, suction_level: int):
    """Stage 2's core logic, pulled out of the async I/O so it's
    directly unit-testable.

    REAL CRASH FOUND AND FIXED (jayjay, real device test): favorites
    are ALWAYS constructed with their command_defs[].params kept as a
    RAW DICT, never upgraded to a CommandParams instance --
    rest_client.py's own _favorite_from_json() does `params=c.get(
    "params")` directly, by design (RoutineCommand.params is typed as
    `CommandParams | dict[str, Any] | None` specifically to allow
    this). This function previously assumed a CommandParams instance
    unconditionally and called dataclasses.replace() directly on it --
    which raises TypeError immediately for EVERY real favorite, not
    an edge case tied to any particular field. Now branches on the
    actual runtime type instead of assuming one.

    An earlier version of this same function ALSO once tried
    dataclasses.replace(original, routine_modified=True) directly on
    the RoutineCommand itself, which would have raised TypeError the
    first time that code path ran (RoutineCommand has no such field at
    all -- routine_modified lives on CommandParams, confirmed directly
    via dataclasses.fields() on both classes, not just reasoned about
    after the fact). Returns (modified_command,
    original_suction_level_for_display)."""
    import dataclasses

    from roombapy_prime.models.mission_control import CommandParams

    original_params = getattr(original, "params", None)
    if isinstance(original_params, dict):
        original_level = original_params.get("suctionLevel")
        new_params: CommandParams | dict = {
            **original_params, "suctionLevel": suction_level, "routineModified": True,
        }
    elif original_params is not None:
        original_level = getattr(original_params, "suction_level", None)
        new_params = dataclasses.replace(original_params, suction_level=suction_level, routine_modified=True)
    else:
        original_level = None
        new_params = CommandParams(suction_level=suction_level, routine_modified=True)
    modified = dataclasses.replace(original, params=new_params)
    return modified, original_level


async def send_stage_one_c(
    username: str, password: str, country_code: str, blid: str,
    favorite_id: str, command_index: int, envelope_style: str, watch_seconds: int,
) -> None:
    """Stage 1c: identical to stage 1b in every way (same favorite,
    same initiator, same favorite_id), except the CommandDef is WRAPPED
    in a cmd/cmdJson envelope rather than flattened at the top level --
    see _EnvelopedCommand's own docstring for the three signals that
    make this worth testing separately."""
    async with connected_robot(
        username, password, country_code, blid, connect_mqtt=True
    ) as (robot, report):

        print("\n== Fetching favorites ==")
        favorites = await robot.get_favorites()
        favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
        if favorite is None:
            print(f"ERROR: no favorite with favorite_id={favorite_id!r} found on this account.")
            return
        if not favorite.command_defs or command_index >= len(favorite.command_defs):
            print(f"ERROR: favorite {favorite_id!r} has no command_defs[{command_index}].")
            return
        original = favorite.command_defs[command_index]

        if not _is_safe_command_def(original):
            print(
                "ABORTED: this command_def contains a TID (ad-hoc/temporary) region. "
                "Stage 1c only re-wraps RID/ZID-only command_defs."
            )
            return

        command = _add_initiator_if_missing(original) or original
        command = _add_favorite_id_if_missing(command, favorite_id) or command
        enveloped = _EnvelopedCommand(command, envelope_style)

        await _confirm_show_send_watch(
            robot, enveloped, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] with initiator + favorite_id, WRAPPED in a "
            f"{envelope_style!r} envelope instead of flattened at the top level:",
        )



async def send_stage_two(
    username: str,
    password: str,
    country_code: str,
    blid: str,
    favorite_id: str,
    command_index: int,
    suction_level: int,
    watch_seconds: int,
) -> None:
    """Stage 2: an existing favorite's command_def, with ONE benign,
    easily-reversible field changed (suction_level) -- regions
    themselves untouched. routine_modified is set True: per
    CommandParams.routine_modified's own docstring, the real app
    computes this by comparing region count/order/IDs and each
    region's user-modifiable params against the original favorite --
    since something WAS genuinely changed here (relative to the
    favorite this came from), True is the correct value to send, not
    an arbitrary guess.

    REAL GAP FOUND AND FIXED (this session, jayjay13011's own field
    report): this used to never add "initiator", regardless of
    whether the favorite had one -- meaning stage 2 always tested the
    SAME "no initiator" shape as stage 1, never actually exercising
    the initiator+command hypothesis stage 1b was specifically built
    to test. Now reuses _add_initiator_if_missing() (unchanged,
    already-tested) exactly like stage 1b does, so a positive/negative
    result here is no longer confounded by a field stage 1b's own
    result suggests might matter.

    A SECOND REAL GAP, found the same session while re-analyzing prior
    research: favorite_id was never added here either -- see stage 1's
    own docstring and _add_favorite_id_if_missing()'s own docstring
    for the full finding (the real app's own RoutineCommandBuilder
    always sends favorite_id together with a favorite's resolved
    command_defs). Composed on top of the initiator addition."""
    async with connected_robot(
        username, password, country_code, blid, connect_mqtt=True
    ) as (robot, report):

        print("\n== Fetching favorites ==")
        favorites = await robot.get_favorites()
        favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
        if favorite is None:
            print(f"ERROR: no favorite with favorite_id={favorite_id!r} found on this account.")
            return
        if not favorite.command_defs or command_index >= len(favorite.command_defs):
            print(f"ERROR: favorite {favorite_id!r} has no command_defs[{command_index}].")
            return
        original = favorite.command_defs[command_index]

        if not _is_safe_command_def(original):
            print(
                "ABORTED: this command_def contains a TID (ad-hoc/temporary) region. "
                "Stage 2 only modifies params on RID/ZID-only command_defs -- see "
                "--send-adhoc for the separate, higher-risk stage 4 path instead."
            )
            return

        final_command, original_level = build_stage_two_command(
            original, favorite_id, suction_level
        )
        initiator_note = " and initiator included"

        await _confirm_show_send_watch(
            robot, final_command, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] with suction_level changed "
            f"({original_level!r} -> {suction_level!r}), routine_modified=True{initiator_note}, "
            "favorite_id added to match the real app's own confirmed behavior:",
        )

    print(
        "\nIf the robot is doing something unexpected: send 'stop' now, either from the "
        "real app or via roombapy-prime-verify-mission-commands in a separate terminal."
    )


async def list_rooms(username: str, password: str, country_code: str, blid: str, p2map_id: str) -> None:
    """Stage 3's own reconnaissance -- pure read, sends nothing.
    Lists real room_id/region_type/name values from
    get_map_metadata()'s own rooms_metadata, so a tester can pick a
    REAL room rather than guessing at an id."""
    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):
        map_data = await robot.get_map_metadata(p2map_id)

    if not map_data.rooms_metadata:
        print(f"No rooms_metadata found for p2map_id={p2map_id!r}.")
        return

    print(f"\n{len(map_data.rooms_metadata)} room(s) found on map {p2map_id!r}:\n")
    for room in map_data.rooms_metadata:
        print(f"  room_id={room.room_id!r}  region_type={room.region_type!r}  name={room.name!r}")
    print(
        "\nTo test one: roombapy-prime-verify-region-commands --send-region "
        "--p2map-id P2MAP_ID --room-id ROOM_ID --region-type rid_or_zid "
        "--i-understand-this-will-move-my-robot "
        "--i-understand-this-is-experimental-and-unconfirmed"
    )


async def send_stage_three(
    username: str,
    password: str,
    country_code: str,
    blid: str,
    p2map_id: str,
    room_id: str,
    region_type: str,
    watch_seconds: int,
) -> None:
    """Stage 3: a genuinely from-scratch RoutineCommand, no
    favorite_id at all -- one hand-constructed RID/ZID region
    referencing a REAL room_id (from list_rooms()/get_map_metadata(),
    not invented). Still avoids TID/ad-hoc regions entirely -- see
    --send-adhoc for that separate, higher-risk stage 4 path.

    routine_modified is left unset here: there is no "original
    favorite" for a from-scratch command to be modified relative to,
    so the modified-vs-unmodified comparison this field represents
    doesn't apply the same way it does for stages 1-2 -- unconfirmed
    whether the real app ever constructs a from-scratch command this
    way at all, let alone what it would set this to if so.

    REAL GAP FOUND AND FIXED (this session, jayjay13011's own field
    report): this never set "initiator" either, for the same reason
    stage 2 didn't -- nobody had connected stage 1b's own finding back
    to stages 2/3 until a real field test showed all three payloads
    side by side. Now adds it via _add_initiator_if_missing(), same as
    stages 1b/2."""
    from roombapy_prime.models.mission_control import MissionCommandType, Region, RoutineCommand

    if region_type.lower() not in (str(RegionType.RID).lower(), str(RegionType.ZID).lower()):
        print(f"ERROR: --region-type must be 'rid' or 'zid', got {region_type!r}. Use --send-adhoc for 'tid'.")
        return

    async with connected_robot(
        username, password, country_code, blid, connect_mqtt=True
    ) as (robot, report):

        command = RoutineCommand(
            command_type=MissionCommandType.CLEAN,
            asset_id=blid,
            map_id=p2map_id,
            regions=[Region(region_id=room_id, region_type=RegionType(region_type.lower()))],
        )
        with_initiator = _add_initiator_if_missing(command)
        final_command = with_initiator if with_initiator is not None else command

        await _confirm_show_send_watch(
            robot, final_command, report, watch_seconds,
            f"From-scratch command: clean room_id={room_id!r} ({region_type}) on map {p2map_id!r}, "
            "no favorite_id, nothing derived from an existing favorite, initiator=\"rmtApp\" added:",
        )

    print(
        "\nIf the robot is doing something unexpected: send 'stop' now, either from the "
        "real app or via roombapy-prime-verify-mission-commands in a separate terminal."
    )


async def send_stage_four(
    username: str,
    password: str,
    country_code: str,
    blid: str,
    p2map_id: str,
    furniture_id: int,
    polygon_points: list[tuple[float, float]],
    watch_seconds: int,
) -> None:
    """Stage 4: a hand-built TID (ad-hoc/temporary zone) region --
    THE RISKIEST TIER THIS PROJECT KNOWS ABOUT. UNLIKE stages 1-3,
    this cannot be made safe by only using already-real,
    already-confirmed values -- two genuinely unconfirmed pieces are
    required as EXPLICIT inputs, deliberately not auto-generated or
    guessed at by this script:

      - furniture_id: confirmed (addAdhocRegion()) to reference a real
        furniture item on the account's own map, but this script has
        no way to look up which furniture_ids actually exist on your
        map -- you must supply one you know is real (e.g. noted from
        the app's own furniture-placement UI), not an arbitrary
        integer.
      - polygon_points: the polygon's coordinate list/format itself is
        only an ASSUMPTION (list[Position], by analogy to every other
        polygon-like structure in this library) -- the real coordinate
        system, unit, and valid range for this specific field were
        never independently confirmed (generics type erasure in the
        bytecode reading -- see CommandPolygon's own docstring).

    The region's own id AND its paired CommandPolygon's id are set
    identically here (confirmed requirement -- see RegionType.TID's
    own docstring) to a value in the confirmed reserved range 160-199,
    picked automatically (161, avoiding the more commonly-seen 160)
    -- NOT something to change lightly, since a real device manages
    this range via its own adHocCounter, not arbitrary caller choice.
    """
    from roombapy_prime.models.mission_control import (
        CommandPolygon,
        CommandPolygonMetadata,
        MissionCommandType,
        Region,
        RoutineCommand,
    )

    print(
        "\n*** STAGE 4: the highest-risk tier this project knows about. ***\n"
        "furniture_id and polygon_points are YOUR responsibility to supply as real, "
        "verified values -- this script does not check or guess at their validity "
        "beyond basic shape."
    )

    adhoc_id = "161"
    polygon = CommandPolygon(
        polygon_id=adhoc_id,
        poly=list(polygon_points),  # Position is just tuple[float, float] -- no constructor needed
        metadata=CommandPolygonMetadata(furniture_id=furniture_id),
    )
    region = Region(region_id=adhoc_id, region_type=RegionType.TID)

    async with connected_robot(
        username, password, country_code, blid, connect_mqtt=True
    ) as (robot, report):

        command = RoutineCommand(
            command_type=MissionCommandType.CLEAN,
            asset_id=blid,
            map_id=p2map_id,
            regions=[region],
            id_multipolys=[polygon],
        )

        await _confirm_show_send_watch(
            robot, command, report, watch_seconds,
            f"Ad-hoc (TID) region id={adhoc_id!r}, furniture_id={furniture_id!r}, "
            f"{len(polygon_points)} polygon point(s), on map {p2map_id!r}:",
        )

    print(
        "\nIf the robot is doing something unexpected: send 'stop' now, either from the "
        "real app or via roombapy-prime-verify-mission-commands in a separate terminal."
    )


def _parse_polygon_points(raw: str) -> list[tuple[float, float]] | None:
    """Parses "x1,y1 x2,y2 x3,y3 ..." into a list of (x, y) tuples.
    Returns None (not an exception) on malformed input, so callers can
    print a clean, user-facing error rather than a traceback."""
    try:
        return [tuple(float(v) for v in pair.split(",")) for pair in raw.split()]
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Staged test package for region-aware mission commands (stages 1-4, increasing "
            "risk). See this module's own docstring for the full explanation before using "
            "any of --send/--send-modified/--send-region/--send-adhoc."
        )
    )
    add_account_arguments(parser)

    parser.add_argument(
        "--list-favorites", action="store_true",
        help="Stage 0: list favorites and command_defs, flag stage-1/2 eligibility. Sends nothing.",
    )
    parser.add_argument(
        "--send", metavar="FAVORITE_ID", default=None,
        help="Stage 1: resend this favorite's own command_def unchanged.",
    )
    parser.add_argument(
        "--send-with-initiator", metavar="FAVORITE_ID", default=None,
        help="Stage 1b: identical to --send, but adds initiator=\"rmtApp\" if the stored "
        "command_def has none set. Purely additive -- see send_stage_one_with_initiator()'s "
        "own docstring for why this is worth testing specifically.",
    )
    parser.add_argument(
        "--send-enveloped", metavar="FAVORITE_ID", default=None,
        help="Stage 1c: identical to stage 1b, but wraps the CommandDef in a cmd/cmdJson "
        "envelope instead of flattening it at the top level. See _EnvelopedCommand's own "
        "docstring for why this is worth testing separately.",
    )
    parser.add_argument(
        "--envelope-style", choices=("cmd", "cmdJson"), default="cmd",
        help="Which envelope stage 1c uses: 'cmd' (the CommandDef as a JSON string, mirroring "
        "buildString()) or 'cmdJson' (nested object, mirroring buildJson()). Default: cmd.",
    )
    parser.add_argument(
        "--send-modified", metavar="FAVORITE_ID", default=None,
        help="Stage 2: resend this favorite's command_def with --suction-level changed.",
    )
    parser.add_argument(
        "--suction-level", type=int, default=None,
        help="New suction_level value for --send-modified (required with it).",
    )
    parser.add_argument(
        "--command-index", type=int, default=0,
        help="Which command_defs[N] within --send/--send-modified's favorite to use (default: 0).",
    )

    parser.add_argument(
        "--list-rooms", action="store_true",
        help="Stage 3 reconnaissance: list real room_id/region_type/name for --p2map-id. Sends nothing.",
    )
    parser.add_argument(
        "--send-region", action="store_true",
        help="Stage 3: send a from-scratch command for --room-id/--region-type on --p2map-id.",
    )
    parser.add_argument("--p2map-id", default=None, help="Required for --list-rooms/--send-region/--send-adhoc.")
    parser.add_argument("--room-id", default=None, help="A REAL room_id from --list-rooms, required for --send-region.")
    parser.add_argument("--region-type", default=None, help="'rid' or 'zid', required for --send-region.")

    parser.add_argument(
        "--send-adhoc", action="store_true",
        help="Stage 4 (HIGHEST RISK): send a hand-built TID/ad-hoc region. Requires --p2map-id, "
        "--furniture-id, --polygon-points, and ALL THREE safety flags including "
        "--i-acknowledge-this-is-the-highest-risk-tier.",
    )
    parser.add_argument(
        "--furniture-id", type=int, default=None,
        help="A REAL furniture_id you have separately verified exists on your map. Required for --send-adhoc.",
    )
    parser.add_argument(
        "--polygon-points", default=None,
        help='Polygon coordinates as "x1,y1 x2,y2 x3,y3 ...". Required for --send-adhoc. '
        "The coordinate system/unit/range is an assumption, not confirmed -- see "
        "send_stage_four()'s own docstring.",
    )
    parser.add_argument(
        "--i-acknowledge-this-is-the-highest-risk-tier", action="store_true",
        help="Stage 4's own, THIRD safety flag, on top of the two shared by every other stage.",
    )

    parser.add_argument(
        "--watch-seconds", type=int, default=60,
        help="How long to watch mission/timeline/report after sending (default: 60, 0 to skip).",
    )
    parser.add_argument("--i-understand-this-will-move-my-robot", action="store_true")
    parser.add_argument("--i-understand-this-is-experimental-and-unconfirmed", action="store_true")
    args = parser.parse_args()
    require_blid(args)

    def _require_send_gates() -> bool:
        if not args.i_understand_this_will_move_my_robot:
            print(
                "Aborted: --i-understand-this-will-move-my-robot is missing. This script sends "
                "a real mission command that will move the robot."
            )
            return False
        if not args.i_understand_this_is_experimental_and_unconfirmed:
            print(
                "Aborted: --i-understand-this-is-experimental-and-unconfirmed is missing. "
                "Unlike send_simple_command(), this transport/schema is NOT yet confirmed "
                "working -- read this module's own docstring before proceeding."
            )
            return False
        return True

    # Validate everything BEFORE ever prompting for credentials -- a
    # bare or malformed invocation should abort immediately with a
    # clear message, the same way this project's older diagnostic
    # scripts already do, not ask for a Prime account login first and
    # only THEN explain what went wrong.
    if not (
        args.list_favorites or args.list_rooms or args.send or args.send_with_initiator
        or args.send_enveloped
        or args.send_modified or args.send_region or args.send_adhoc
    ):
        print(
            "Nothing to do -- pass --list-favorites/--list-rooms (safe, send nothing), or one of "
            "--send/--send-with-initiator/--send-modified/--send-region/--send-adhoc."
        )
        return

    if args.list_rooms and not args.p2map_id:
        print("Aborted: --list-rooms needs --p2map-id.")
        sys.exit(1)

    if args.send and not _require_send_gates():
        sys.exit(1)

    if args.send_enveloped and not _require_send_gates():
        return

    if args.send_with_initiator and not _require_send_gates():
        sys.exit(1)

    if args.send_modified:
        if not _require_send_gates():
            sys.exit(1)
        if args.suction_level is None:
            print("Aborted: --send-modified needs --suction-level.")
            sys.exit(1)

    if args.send_region:
        if not _require_send_gates():
            sys.exit(1)
        if not (args.p2map_id and args.room_id and args.region_type):
            print("Aborted: --send-region needs --p2map-id, --room-id, and --region-type.")
            sys.exit(1)

    parsed_polygon_points = None
    if args.send_adhoc:
        if not _require_send_gates():
            sys.exit(1)
        if not args.i_acknowledge_this_is_the_highest_risk_tier:
            print(
                "Aborted: --i-acknowledge-this-is-the-highest-risk-tier is missing. Stage 4 "
                "needs a THIRD, separate acknowledgment on top of the two shared by every "
                "other stage -- read send_stage_four()'s own docstring first."
            )
            sys.exit(1)
        if not (args.p2map_id and args.furniture_id is not None and args.polygon_points):
            print("Aborted: --send-adhoc needs --p2map-id, --furniture-id, and --polygon-points.")
            sys.exit(1)
        parsed_polygon_points = _parse_polygon_points(args.polygon_points)
        if parsed_polygon_points is None:
            print('Aborted: --polygon-points must look like "x1,y1 x2,y2 x3,y3 ...".')
            sys.exit(1)

    username, password = resolve_credentials(args)

    if args.list_favorites:
        asyncio.run(list_favorites(username, password, args.country_code, args.blid))
        return

    if args.list_rooms:
        asyncio.run(list_rooms(username, password, args.country_code, args.blid, args.p2map_id))
        return

    if args.send:
        asyncio.run(
            send_stage_one(
                username, password, args.country_code, args.blid,
                args.send, args.command_index, args.watch_seconds,
            )
        )
        return

    if args.send_with_initiator:
        asyncio.run(
            send_stage_one_with_initiator(
                username, password, args.country_code, args.blid,
                args.send_with_initiator, args.command_index, args.watch_seconds,
            )
        )
        return

    if args.send_enveloped:
        asyncio.run(
            send_stage_one_c(
                username, password, args.country_code, args.blid,
                args.send_enveloped, args.command_index, args.envelope_style, args.watch_seconds,
            )
        )
        return

    if args.send_modified:
        asyncio.run(
            send_stage_two(
                username, password, args.country_code, args.blid,
                args.send_modified, args.command_index, args.suction_level, args.watch_seconds,
            )
        )
        return

    if args.send_region:
        asyncio.run(
            send_stage_three(
                username, password, args.country_code, args.blid,
                args.p2map_id, args.room_id, args.region_type, args.watch_seconds,
            )
        )
        return

    if args.send_adhoc:
        asyncio.run(
            send_stage_four(
                username, password, args.country_code, args.blid,
                args.p2map_id, args.furniture_id, parsed_polygon_points, args.watch_seconds,
            )
        )
        return


if __name__ == "__main__":
    main()
