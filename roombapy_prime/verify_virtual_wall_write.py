"""Staged test package for SetVirtualWallsV1 ("set_virtual_wall") --
virtual walls, keep-out zones, and no-mop zones, never tested live
before this script existed. Read models/map_editing.py's own
policy_zones_to_virtual_walls()/policy_zone_to_virtual_wall()
docstrings first for the full, confirmed categorization rule this
script depends on.

WHY THIS IS SAFE DESPITE BEING A "NEW OBJECT" COMMAND: a real field
report initially suggested SetVirtualWallsV1 might work by
add/delta semantics (only the changed object sent). Direct
confirmation from the real app's own deleteVirtualWall()
implementation settled this: it works by REPLACE semantics -- read
the CURRENT full list, remove/add the target, send the WHOLE list
back. That means the exact same "read current, resend unchanged"
stage-1 philosophy already used by every other staged script in this
project applies here too, and does NOT require understanding
CommandPolygon's own still-unconfirmed coordinate system at all --
existing coordinates are preserved byte-for-byte, never recomputed.

THE STAGED APPROACH:

  Stage 1 (--update-unchanged): downloads the current map bundle,
  reads policyZones.geojson, converts every entry to its correct
  VirtualWallV1 subtype (policy_zones_to_virtual_walls(), the
  confirmed categorization rule), and resends this exact list via
  SetVirtualWallsV1, completely unchanged. Confirms the write path
  accepts a real, complete list without error.

  Stage 2 (NOT built yet, deliberately): adding one new object then
  removing it again (matching deleteVirtualWall()'s own real
  approach: full list minus/plus one entry). Would need a real,
  user-supplied polygon/line geometry to add -- deferred for the same
  reason region-commands' stage 4 defers ad-hoc geometry: the exact
  coordinate system remains genuinely unconfirmed (though, per the
  point above, unnecessary for stage 1 specifically).

TWO SAFETY GATES (same reasoning as verify_schedule_write.py's own
two-gate design):
  1. --i-understand-this-changes-real-map-zones
  2. An interactive y/N confirmation, showing the exact JSON payload
     immediately before it's sent.

WHAT TO DO IF SOMETHING LOOKS WRONG: re-run --list-walls to see
current state. Since stage 1 only ever resends what was just read,
the safest recovery is simply running --update-unchanged again --
each run re-reads the current (by then already-restored, if stage 1
itself is what you're worried about) state fresh.
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


def _confirm(prompt: str) -> bool:
    """Interactive confirmation -- ONLY "j"/"ja"/"y"/"yes" (case
    doesn't matter) counts as approval, anything else (including just
    pressing Enter) aborts. Same convention as this project's other
    diagnostic scripts."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


async def _fetch_current_walls(robot, p2map_id: str, p2mapv_id: str):
    """Shared by both --list-walls and --update-unchanged: download
    the current bundle, read policyZones.geojson, convert to
    VirtualWallV1 -- returns (raw_policy_zone_features, virtual_walls)."""
    from .models import PolicyZoneFeature, parse_map_bundle
    from .models.map_editing import policy_zones_to_virtual_walls

    link = await robot.get_map_geojson_link(p2map_id, p2mapv_id)
    url = link.get("map_url") or next(
        (v for v in link.values() if isinstance(v, str) and v.startswith("http")), None
    )
    if not url:
        raise ValueError(f"get_map_geojson_link() response had no usable URL: {link!r}")

    bundle_bytes = await robot.download_map_bundle(url)
    parsed = parse_map_bundle(bundle_bytes)
    raw_policy_zones = parsed.get("policyZones")
    if raw_policy_zones is None:
        return [], []

    raw_features = raw_policy_zones.get("features") if isinstance(raw_policy_zones, dict) else raw_policy_zones
    features = [PolicyZoneFeature.from_json(f) for f in (raw_features or [])]
    walls = policy_zones_to_virtual_walls(features)
    return features, walls


async def list_walls(username: str, password: str, country_code: str, blid: str, p2map_id: str, p2mapv_id: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing."""
    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        features, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)

    if not features:
        print("No policyZones.geojson data found for this map (or the map bundle had none).")
        return

    print(f"\n{len(features)} raw policyZones feature(s), {len(walls)} converted to VirtualWallV1:\n")
    for feature, wall in zip(features, walls + [None] * (len(features) - len(walls)), strict=True):
        kind = type(wall).__name__ if wall is not None else "(dropped -- Threshold or unrecognized)"
        print(f"  id={feature.feature_id!r} zone_type={feature.properties.zone_type!r} -> {kind}")

    print(
        "\nTo resend this exact combined list unchanged: "
        "roombapy-prime-verify-virtual-wall-write --update-unchanged "
        f"--p2map-id {p2map_id} --p2mapv-id {p2mapv_id} "
        "--i-understand-this-changes-real-map-zones"
    )


async def send_update_unchanged(
    username: str, password: str, country_code: str, blid: str, p2map_id: str, p2mapv_id: str,
) -> None:
    from .models.map_editing import SetVirtualWallsV1

    report = Report()
    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")

        print("\n== Reading current policy zones ==")
        try:
            features, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)
        except Exception as exc:  # noqa: BLE001
            report.add("Reading current policy zones", "FAILED", f"{type(exc).__name__}: {exc}")
            report.redact(username, password)
            ok, failed, skipped = report.summary()
            print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")
            return
        report.add("Reading current policy zones", "OK", f"{len(features)} feature(s), {len(walls)} wall(s)")

        command = SetVirtualWallsV1(walls=walls)
        payload = command.to_v1_command_body()
        print(f"\nResending {len(walls)} wall(s) -- EXACTLY as read, nothing modified:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        if not _confirm("\nSend this EXACT payload now? This changes real map zones."):
            print("Aborted by user -- nothing sent.")
            return

        print("\n== Sending ==")
        try:
            result = await robot.edit_map(p2map_id, command)
            report.add("edit_map() -- SetVirtualWallsV1", "OK", f"response: {result!r}")
        except Exception as exc:  # noqa: BLE001
            report.add("edit_map() -- SetVirtualWallsV1", "FAILED", f"{type(exc).__name__}: {exc}")

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 1 test for SetVirtualWallsV1 (\"set_virtual_wall\"): resend the current, "
            "complete virtual-wall/keep-out-zone/no-mop-zone list completely unchanged. See "
            "this module's own docstring for the full staged-risk explanation."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", default=os.environ.get("ROOMBAPY_PRIME_BLID"), help="The exact target device -- no 'first device found'. Falls back to ROOMBAPY_PRIME_BLID env var.")
    parser.add_argument("--p2map-id", required=True)
    parser.add_argument("--p2mapv-id", required=True, help="From get_active_map_versions()'s own active_p2mapv_id.")
    parser.add_argument(
        "--list-walls", action="store_true",
        help="Stage 0: list current virtual walls/zones for this map. Sends nothing.",
    )
    parser.add_argument(
        "--update-unchanged", action="store_true",
        help="Stage 1: resend the current, complete list unchanged.",
    )
    parser.add_argument("--i-understand-this-changes-real-map-zones", action="store_true")
    args = parser.parse_args()
    if not args.blid:
        print("Aborted: --blid is required (or set the ROOMBAPY_PRIME_BLID env var).")
        sys.exit(1)

    if not (args.list_walls or args.update_unchanged):
        print("Nothing to do -- pass --list-walls (safe, sends nothing) or --update-unchanged.")
        return

    if args.update_unchanged and not args.i_understand_this_changes_real_map_zones:
        print("Aborted: --i-understand-this-changes-real-map-zones is missing.")
        sys.exit(1)

    username = args.username or input("iRobot account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("iRobot account password: ")

    if args.list_walls:
        asyncio.run(list_walls(username, password, args.country_code, args.blid, args.p2map_id, args.p2mapv_id))
        return

    if args.update_unchanged:
        asyncio.run(
            send_update_unchanged(username, password, args.country_code, args.blid, args.p2map_id, args.p2mapv_id)
        )
        return


if __name__ == "__main__":
    main()
