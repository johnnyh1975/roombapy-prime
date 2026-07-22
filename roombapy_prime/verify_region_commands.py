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
     _confirm() helper already used elsewhere in this project's
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
import getpass
import json
import os
import sys
from typing import Any

import aiohttp

from .diagnostics import Report
from .models.mission_control import Region, RegionType
from .prime_factory import PrimeFactory


def _confirm(prompt: str) -> bool:
    """Interactive confirmation -- ONLY "j"/"ja"/"y"/"yes" (case
    doesn't matter) counts as approval, anything else (including just
    pressing Enter) aborts. Same convention as verify_mission_timeline.py
    and verify_mission_commands.py's own _confirm() helpers."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


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


async def _login_and_connect(session: aiohttp.ClientSession, username: str, password: str, country_code: str, blid: str, report: Report):
    print("\n== Login ==")
    robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
    report.add("Login", "OK", f"BLID={robot.blid}")
    await robot.connect()
    report.add("MQTT connection", "OK")
    return robot


async def _confirm_show_send_watch(
    robot, command, report: Report, watch_seconds: int, description: str,
) -> None:
    """Shared final step for every stage: show the exact payload,
    require interactive confirmation, send, optionally watch
    mission/timeline/report afterward. Used identically by stages
    1-4 -- the only thing that differs between stages is HOW `command`
    was constructed before reaching this point."""
    payload = command.to_json()
    print(f"\n{description}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if not _confirm("\nSend this EXACT payload now? This will move the robot."):
        print("Aborted by user -- nothing sent.")
        return

    print("\n== Sending ==")
    await robot.send_routine_command_via_cmd_topic(command)
    report.add("send_routine_command_via_cmd_topic()", "OK", "payload sent, fire-and-forget (no response wait)")

    if watch_seconds > 0:
        print(f"\n== Watching mission/timeline/report for {watch_seconds}s ==")
        print("(Ctrl+C to stop watching early -- the command has already been sent either way)")
        try:
            async with asyncio.timeout(watch_seconds):
                async for event in robot.watch_mission_timeline():
                    print(f"  {event}")
        except TimeoutError:
            pass
        except KeyboardInterrupt:
            pass

    await robot.disconnect()


async def list_favorites(username: str, password: str, country_code: str, blid: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing to the robot.
    Lists every favorite and every command_def within it, flagging
    which ones are eligible for stage 1 (no TID regions) and which
    aren't, so a tester can pick a safe target before touching --send
    at all."""
    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
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
    report = Report()
    async with aiohttp.ClientSession() as session:
        robot = await _login_and_connect(session, username, password, country_code, blid, report)

        print("\n== Fetching favorites ==")
        favorites = await robot.get_favorites()
        favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
        if favorite is None:
            print(f"ERROR: no favorite with favorite_id={favorite_id!r} found on this account.")
            return
        if not favorite.command_defs or command_index >= len(favorite.command_defs):
            print(f"ERROR: favorite {favorite_id!r} has no command_defs[{command_index}].")
            return
        command = favorite.command_defs[command_index]

        if not _is_safe_command_def(command):
            print(
                "ABORTED: this command_def contains a TID (ad-hoc/temporary) region. "
                "This is stage 1 (RID/ZID regions only, completely unchanged) -- see "
                "--send-adhoc for the separate, higher-risk stage 4 path instead."
            )
            return

        await _confirm_show_send_watch(
            robot, command, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] -- EXACTLY as stored, nothing modified:",
        )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")
    print(
        "\nIf the robot is doing something unexpected: send 'stop' now, either from the "
        "real app or via roombapy-prime-verify-mission-commands in a separate terminal."
    )


def _build_modified_command(original, suction_level: int):
    """Stage 2's core logic, pulled out of the async I/O so it's
    directly unit-testable -- see this session's own hard lesson:
    an earlier version of this tried
    dataclasses.replace(original, routine_modified=True) directly on
    the RoutineCommand, which would have raised TypeError at runtime
    the first time this code path actually ran (RoutineCommand has no
    such field at all -- routine_modified lives on CommandParams,
    confirmed directly via dataclasses.fields() on both classes, not
    just reasoned about after the fact). Returns (modified_command,
    original_suction_level_for_display)."""
    import dataclasses

    from .models.mission_control import CommandParams

    original_params = getattr(original, "params", None)
    original_level = getattr(original_params, "suction_level", None) if original_params is not None else None
    if original_params is not None:
        new_params = dataclasses.replace(original_params, suction_level=suction_level, routine_modified=True)
    else:
        new_params = CommandParams(suction_level=suction_level, routine_modified=True)
    modified = dataclasses.replace(original, params=new_params)
    return modified, original_level


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
    an arbitrary guess."""
    report = Report()
    async with aiohttp.ClientSession() as session:
        robot = await _login_and_connect(session, username, password, country_code, blid, report)

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

        modified, original_level = _build_modified_command(original, suction_level)

        await _confirm_show_send_watch(
            robot, modified, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] with suction_level changed "
            f"({original_level!r} -> {suction_level!r}), routine_modified=True:",
        )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")
    print(
        "\nIf the robot is doing something unexpected: send 'stop' now, either from the "
        "real app or via roombapy-prime-verify-mission-commands in a separate terminal."
    )


async def list_rooms(username: str, password: str, country_code: str, blid: str, p2map_id: str) -> None:
    """Stage 3's own reconnaissance -- pure read, sends nothing.
    Lists real room_id/region_type/name values from
    get_map_metadata()'s own rooms_metadata, so a tester can pick a
    REAL room rather than guessing at an id."""
    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
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
    way at all, let alone what it would set this to if so."""
    from .models.mission_control import MissionCommandType, Region, RoutineCommand

    if region_type.lower() not in (str(RegionType.RID).lower(), str(RegionType.ZID).lower()):
        print(f"ERROR: --region-type must be 'rid' or 'zid', got {region_type!r}. Use --send-adhoc for 'tid'.")
        return

    report = Report()
    async with aiohttp.ClientSession() as session:
        robot = await _login_and_connect(session, username, password, country_code, blid, report)

        command = RoutineCommand(
            command_type=MissionCommandType.CLEAN,
            asset_id=blid,
            map_id=p2map_id,
            regions=[Region(region_id=room_id, region_type=RegionType(region_type.lower()))],
        )

        await _confirm_show_send_watch(
            robot, command, report, watch_seconds,
            f"From-scratch command: clean room_id={room_id!r} ({region_type}) on map {p2map_id!r}, "
            "no favorite_id, nothing derived from an existing favorite:",
        )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")
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
    from .models.mission_control import (
        CommandPolygon,
        CommandPolygonMetadata,
        MissionCommandType,
        Region,
        RoutineCommand,
    )

    report = Report()
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

    async with aiohttp.ClientSession() as session:
        robot = await _login_and_connect(session, username, password, country_code, blid, report)

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

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")
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
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", required=True, help="The exact target device -- no 'first device found'.")

    parser.add_argument(
        "--list-favorites", action="store_true",
        help="Stage 0: list favorites and command_defs, flag stage-1/2 eligibility. Sends nothing.",
    )
    parser.add_argument(
        "--send", metavar="FAVORITE_ID", default=None,
        help="Stage 1: resend this favorite's own command_def unchanged.",
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

    username = args.username or input("Prime account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Prime account password: ")

    if args.list_favorites:
        asyncio.run(list_favorites(username, password, args.country_code, args.blid))
        return

    if args.list_rooms:
        if not args.p2map_id:
            print("Aborted: --list-rooms needs --p2map-id.")
            sys.exit(1)
        asyncio.run(list_rooms(username, password, args.country_code, args.blid, args.p2map_id))
        return

    if args.send:
        if not _require_send_gates():
            sys.exit(1)
        asyncio.run(
            send_stage_one(
                username, password, args.country_code, args.blid,
                args.send, args.command_index, args.watch_seconds,
            )
        )
        return

    if args.send_modified:
        if not _require_send_gates():
            sys.exit(1)
        if args.suction_level is None:
            print("Aborted: --send-modified needs --suction-level.")
            sys.exit(1)
        asyncio.run(
            send_stage_two(
                username, password, args.country_code, args.blid,
                args.send_modified, args.command_index, args.suction_level, args.watch_seconds,
            )
        )
        return

    if args.send_region:
        if not _require_send_gates():
            sys.exit(1)
        if not (args.p2map_id and args.room_id and args.region_type):
            print("Aborted: --send-region needs --p2map-id, --room-id, and --region-type.")
            sys.exit(1)
        asyncio.run(
            send_stage_three(
                username, password, args.country_code, args.blid,
                args.p2map_id, args.room_id, args.region_type, args.watch_seconds,
            )
        )
        return

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
        points = _parse_polygon_points(args.polygon_points)
        if points is None:
            print('Aborted: --polygon-points must look like "x1,y1 x2,y2 x3,y3 ...".')
            sys.exit(1)
        asyncio.run(
            send_stage_four(
                username, password, args.country_code, args.blid,
                args.p2map_id, args.furniture_id, points, args.watch_seconds,
            )
        )
        return

    print(
        "Nothing to do -- pass --list-favorites/--list-rooms (safe, send nothing), or one of "
        "--send/--send-modified/--send-region/--send-adhoc."
    )


if __name__ == "__main__":
    main()
