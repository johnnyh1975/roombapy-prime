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
import json
import sys


from ._cli import add_account_arguments, confirm, connected_robot, field, require_blid, resolve_credentials, run_script




async def _fetch_current_walls(robot, p2map_id: str, p2mapv_id: str):
    """Shared by both --list-walls and --update-unchanged: download
    the current bundle, read policyZones.geojson, convert to
    VirtualWallV1 -- returns (raw_policy_zone_features, virtual_walls)."""
    from roombapy_prime.models import PolicyZoneFeature, parse_map_bundle
    from roombapy_prime.models.map_editing import policy_zones_to_virtual_walls

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


async def list_maps(username: str, password: str, country_code: str, blid: str) -> None:
    """NEW (this session): stage 0 needs BOTH --p2map-id and
    --p2mapv-id, but nothing in this project actually printed the
    latter -- and taking it from a stored favorite would be actively
    wrong, since a favorite carries the version that was current when
    it was SAVED (user_p2mapv_id), which may since have been
    superseded. That is exactly the MAP_VERSION_MISMATCH case the
    region-command work is investigating. This prints the CURRENTLY
    ACTIVE pair straight from get_active_map_versions(), making this
    script self-sufficient instead of sending testers hunting."""
    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):
        maps = await robot.get_active_map_versions()

    if not maps:
        print("\nNo maps found for this robot.")
        report.add("List maps", "OK", "no maps on this account")
    else:
        print(f"\n{len(maps)} map(s) found:\n")
        for m in maps:
            name = field(m, "name") or "(unnamed)"
            print(f"  name={name!r}")
            print(f"    --p2map-id  {field(m, 'p2map_id')}")
            print(f"    --p2mapv-id {field(m, 'active_p2mapv_id')}")
        report.add("List maps", "OK", f"{len(maps)} map(s)")
        print(
            "\nCopy the two IDs of the map you want into:\n"
            "  roombapy-prime-verify-virtual-wall-write --list-walls "
            "--p2map-id <...> --p2mapv-id <...>"
        )



async def warn_if_map_version_is_stale(robot, p2map_id: str, p2mapv_id: str, report) -> bool:
    """Warns when the caller passed a map version the robot has moved on
    from. Returns True if the version is current (or unknowable).

    FOUND IN THE FIELD (DaRealGuGu). He restarted his robot between
    tests, which re-versioned the map, then ran with the older version
    id -- and got "No policyZones.geojson data found". That result is
    ambiguous in the worst way: it reads as "you have no keep-out
    zones" when it might equally mean "we looked in a version that no
    longer exists".

    Neither he nor we could tell which, and the script said nothing
    about the difference. Since map re-versioning on restart is now
    confirmed behaviour rather than a theory, an unnoticed stale id is
    a realistic way to produce a confidently wrong empty result."""
    try:
        maps = await robot.get_active_map_versions()
    except Exception as exc:  # noqa: BLE001
        report.add("Map version freshness", "SKIPPED", f"{type(exc).__name__}: {exc}")
        return True

    for entry in maps or []:
        if field(entry, "p2map_id") != p2map_id:
            continue
        active = field(entry, "active_p2mapv_id")
        if not active:
            report.add("Map version freshness", "SKIPPED", "robot reported no active version")
            return True
        if active == p2mapv_id:
            report.add("Map version freshness", "OK", f"--p2mapv-id matches the active {active!r}")
            return True
        report.add(
            "Map version freshness", "FAILED",
            f"you passed --p2mapv-id {p2mapv_id!r} but the robot's active version is "
            f"{active!r}. Map versions change when the robot re-maps or is restarted, so an "
            "empty result here would be ambiguous: it could mean you have no zones, or that "
            f"we looked in a version that no longer exists. Re-run with --p2mapv-id {active}",
        )
        return False

    report.add("Map version freshness", "SKIPPED", f"map {p2map_id!r} not in the active list")
    return True


async def list_walls(username: str, password: str, country_code: str, blid: str, p2map_id: str, p2mapv_id: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing."""
    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):
        fresh = await warn_if_map_version_is_stale(robot, p2map_id, p2mapv_id, report)
        features, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)

    if not features:
        print("No policyZones.geojson data found for this map (or the map bundle had none).")
        if not fresh:
            print(
                "\nTREAT THIS RESULT AS INCONCLUSIVE: the map version you passed is not the\n"
                "robot's current one (see the report above). An empty result may simply mean\n"
                "we looked in a version that no longer exists. Re-run with the active version."
            )
        else:
            print(
                "\nThe map version you passed IS the robot's current one, so this is a real\n"
                "result: this map genuinely has no keep-out zones or virtual walls. That is\n"
                "still worth reporting."
            )
        return

    print(f"\n{len(features)} raw policyZones feature(s), {len(walls)} converted to VirtualWallV1:\n")
    for feature, wall in zip(features, walls + [None] * (len(features) - len(walls)), strict=True):
        # KNOWN omissions read differently from unknown ones, and the
        # old message conflated them (arielgr: a Threshold zone printed
        # as "dropped -- Threshold or unrecognized", which reads like a
        # gap in this library and is not).
        #
        # Thresholds are doorway markers. They live in policyZones
        # alongside keep-out zones, but they are NOT virtual walls: the
        # app edits them through set_thresholds, a separate command with
        # its own status field. Dropping them here is correct --
        # resending them as virtual walls would be wrong.
        if wall is not None:
            kind = type(wall).__name__
        elif (feature.properties.zone_type or "") == "Threshold":
            kind = "(skipped -- a doorway threshold, edited via set_thresholds, not a wall)"
        else:
            kind = f"(DROPPED -- zone_type {feature.properties.zone_type!r} is not recognised; please report)"
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
    from roombapy_prime.models.map_editing import SetVirtualWallsV1

    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):

        print("\n== Reading current policy zones ==")
        try:
            features, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)
        except Exception as exc:  # noqa: BLE001
            report.add("Reading current policy zones", "FAILED", f"{type(exc).__name__}: {exc}")
            return
        report.add("Reading current policy zones", "OK", f"{len(features)} feature(s), {len(walls)} wall(s)")

        command = SetVirtualWallsV1(walls=walls)
        payload = command.to_v1_command_body()
        print(f"\nResending {len(walls)} wall(s) -- EXACTLY as read, nothing modified:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        if not confirm("\nSend this EXACT payload now? This changes real map zones."):
            print("Aborted by user -- nothing sent.")
            return

        # TRIES SEVERAL ENVELOPES IN ONE RUN (this session).
        #
        # Two field runs resent two untouched zones and got HTTP 500
        # both times -- once with a payload carrying a genuine extra
        # point, and again after that was corrected. So the extra point
        # was a real deviation from the documented format but not the
        # cause, and testing one guess per round is too slow when each
        # round costs a tester their evening.
        #
        # `response_type` is the least-verified part of this request:
        # "link" asks the server for a presigned DOWNLOAD url, which is
        # confirmed for FETCHING a map and may be meaningless on an
        # EDIT. This module's own docstring has said as much all along.
        #
        # The variants stop at the first success, so a working one ends
        # the run rather than sending the same edit repeatedly.
        variants: list[tuple[str, str | None]] = [
            ("response_type omitted entirely", None),
            ('response_type="link" (the previous default)', "link"),
            ('response_type="binary"', "binary"),
        ]

        local_bug = False
        print(f"\n== Sending -- trying {len(variants)} request shapes, stopping at the first success ==")
        for label, response_type in variants:
            print(f"\n-- {label} --")
            try:
                result = await robot.edit_map(p2map_id, command, response_type=response_type)
            except TypeError as exc:
                # A TypeError here never reached the network -- it is a
                # bug in THIS code, not an answer from the server.
                #
                # HAPPENED FOR REAL (a28): response_type was added to
                # the REST client and not to the robot wrapper, so all
                # three variants died before a single request went out
                # -- and the summary below still announced that
                # response_type was ruled out. It had not been tested
                # at all.
                #
                # Reporting a local failure as a server result is the
                # same mistake as the PUBACK false signal earlier in
                # this project's history, and it wastes a tester's
                # entire evening.
                report.add(
                    f"edit_map() -- {label}", "FAILED",
                    f"LOCAL BUG, request never sent -- {type(exc).__name__}: {exc}",
                )
                print(f"   NOT SENT -- bug in this script: {exc}")
                local_bug = True
                continue
            except Exception as exc:  # noqa: BLE001
                report.add(
                    f"edit_map() -- {label}", "FAILED", f"{type(exc).__name__}: {exc}",
                )
                print(f"   failed: {type(exc).__name__}: {exc}")
                continue

            report.add(f"edit_map() -- {label}", "OK", f"response: {result!r}")
            print(f"   ACCEPTED: {result!r}")
            print(
                "\nThis shape worked. Please check the iRobot app: the zones should look\n"
                "exactly as they did before, since this resent them unchanged."
            )
            return

        if local_bug:
            print(
                "\nNOTHING WAS ACTUALLY SENT. The failures above are bugs in this script,\n"
                "not answers from the server -- so this run rules out nothing at all.\n"
                "Please report it; the fix belongs on our side."
            )
        else:
            print(
                "\nAll shapes were sent and all were rejected. That rules out response_type\n"
                "as the cause, which is worth knowing -- it was the least-verified part of\n"
                "the request."
            )



def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 1 test for SetVirtualWallsV1 (\"set_virtual_wall\"): resend the current, "
            "complete virtual-wall/keep-out-zone/no-mop-zone list completely unchanged. See "
            "this module's own docstring for the full staged-risk explanation."
        )
    )
    add_account_arguments(parser)
    # NOT required=True (this session): --list-maps exists precisely to
    # OBTAIN these two, so demanding them up front made it unusable.
    # Checked below, only for the stages that actually need them.
    parser.add_argument("--p2map-id", default=None)
    parser.add_argument("--p2mapv-id", default=None, help="From --list-maps, or get_active_map_versions()'s own active_p2mapv_id.")
    parser.add_argument(
        "--list-maps", action="store_true",
        help="Stage 0a: print each map's p2map_id and its CURRENTLY ACTIVE version id, ready "
        "to paste into --list-walls. Sends nothing, and needs no IDs itself.",
    )
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
    require_blid(args)

    if not (args.list_maps or args.list_walls or args.update_unchanged):
        print(
            "Nothing to do -- start with --list-maps (safe, sends nothing, and gives you the "
            "two IDs the other stages need)."
        )
        return

    if (args.list_walls or args.update_unchanged) and not (args.p2map_id and args.p2mapv_id):
        print(
            "Aborted: --p2map-id and --p2mapv-id are both required for this stage. "
            "Run --list-maps first to get them."
        )
        sys.exit(1)

    if args.update_unchanged and not args.i_understand_this_changes_real_map_zones:
        print("Aborted: --i-understand-this-changes-real-map-zones is missing.")
        sys.exit(1)

    username, password = resolve_credentials(args)

    if args.list_maps:
        sys.exit(run_script(list_maps(username, password, args.country_code, args.blid)))
        return

    if args.list_walls:
        sys.exit(run_script(list_walls(username, password, args.country_code, args.blid, args.p2map_id, args.p2mapv_id)))
        return

    if args.update_unchanged:
        sys.exit(run_script(
            send_update_unchanged(username, password, args.country_code, args.blid, args.p2map_id, args.p2mapv_id)
        ))
        return


if __name__ == "__main__":
    main()
