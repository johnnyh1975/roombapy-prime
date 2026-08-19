"""Name a clean zone, or list the ones that have no name.

WHY THIS EXISTS
---------------

@chairstacker's regions, read with `--list-rooms`:

    room_id='10'   RID   name='Dining Room'
    room_id='100'  ZID   name=None
    ... thirteen more, all None

He can rename **rooms** in the iRobot app and the names reach Home
Assistant within minutes. He cannot rename **zones** -- the input does
not take.

APK 3.0.0 explains why: they are two different mechanisms behind one
app surface.

    rooms  ->  setRoomMetadata / setRenameRoom
    zones  ->  updateCleanZones

And `updateCleanZones` has no rename of its own. It reads
`zone, id, name, geometry` per item, looks up an `existingId`, keeps
what is in `retainIds` and deletes the rest -- so **renaming a zone is
writing it again with its existing id and a new name.**

WHAT THIS SENDS
---------------

`SetPermanentAreasV1`, the active V1 edit path, whose wire shape is a
three-element array per area:

    [id, name, [x1, y1, x2, y2, ...]]

The id and geometry come from the map bundle unchanged; only the name
differs. That is the whole operation.

CONFIRMED TO WORK, from APK 3.0.0
---------------------------------

Both directions use the same field offset in the app's internal map
model:

    reading  (timeline)  _resolveAreaName            r5->field_53
    writing  (rename)    _convertToP2MapCleanZoneInfo r0->field_53

There are not two separate name stores. A name set through
`SetPermanentAreasV1` lands in exactly the field the app writes when
renaming and reads when labelling a timeline entry:

    CleanZoneFeature.properties.name   (bundle / server)
              |
    MapAreaData.field_53               (app-internal)
              |
    timeline display

So this is not a write into a field only the map editor sees. It is
the same field.

SAFETY
------

A full-replace command. Every zone on the map is sent back, and
anything omitted is deleted -- which is why this reads the current set
first and refuses to proceed if it cannot.

`--dry-run` is the default. Nothing is sent without
`--i-understand-this-rewrites-my-zone-list`.

THE REAL RISK IS THE FULL REPLACE, not whether the name takes.
`updateCleanZones` derives `deleteIds` from `retainIds`: name one zone
and omit the other seven, and the other seven are gone. Their geometry
is not trivially recoverable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any


def _fmt_zone(zone: Any) -> str:
    zid = getattr(zone, "zone_id", None) or getattr(zone, "area_id", None)
    name = getattr(zone, "name", None)
    return f"  id={zid!s:<6} name={name!r}"


async def _run(args: argparse.Namespace) -> int:
    from roombapy_prime import PrimeFactory

    factory = PrimeFactory()
    robot = await factory.create_robot(
        username=args.username, password=args.password, blid=args.blid
    )

    try:
        await robot.connect()

        p2map_id = args.map_id or await _active_map_id(robot)
        if not p2map_id:
            print("No active map id -- pass --map-id explicitly.")
            return 2

        zones = await _read_zones(robot, p2map_id)
        if zones is None:
            print(
                "Could not read the current zone list. Refusing to send: "
                "this command replaces the whole list, so a partial "
                "write would delete the zones it does not carry."
            )
            return 2

        print(f"Map {p2map_id}: {len(zones)} zone(s)")
        for zone in zones:
            print(_fmt_zone(zone))

        if not args.zone_id:
            print("\nNothing to do -- pass --zone-id and --name to rename one.")
            return 0

        target = next(
            (
                z for z in zones
                if str(getattr(z, "zone_id", None)
                       or getattr(z, "area_id", "")) == str(args.zone_id)
            ),
            None,
        )
        if target is None:
            print(f"\nNo zone with id {args.zone_id} on this map.")
            return 2

        print(
            f"\nWould rename {args.zone_id} from "
            f"{getattr(target, 'name', None)!r} to {args.name!r}, "
            f"resending all {len(zones)} zone(s) unchanged."
        )

        if not args.i_understand_this_rewrites_my_zone_list:
            print(
                "\nDry run. Nothing sent. Add "
                "--i-understand-this-rewrites-my-zone-list to proceed."
            )
            return 0

        result = await _send_rename(robot, p2map_id, zones, target, args.name)
        print(f"\nSent. Response: {json.dumps(result)[:400]}")
        print(
            "\nRe-read the zone list to see whether the name stuck -- "
            "the server accepting a command and storing it are two "
            "different things, and this path has never been confirmed "
            "on real hardware."
        )
        return 0
    finally:
        await robot.disconnect()


async def _active_map_id(robot: Any) -> str | None:
    from roombapy_prime.models.map_bundle import parse_active_map_versions

    raw = await robot.get_active_map_versions()
    versions = parse_active_map_versions(raw)
    for version in versions or []:
        # Direct attribute access, not getattr(): a parsed model has
        # the field or the parser is wrong, and getattr with a default
        # would hide a renamed one. See the guard in tools/tests.
        if version.p2map_id:
            return str(version.p2map_id)
    return None


async def _read_zones(robot: Any, p2map_id: str) -> list[Any] | None:
    """The current clean zones, from the map bundle."""
    try:
        link = await robot.get_map_geojson_link(p2map_id)
        blob = await robot.download_map_bundle(link)
        bundle = robot.parse_map_bundle(blob)
    except Exception as exc:  # noqa: BLE001
        print(f"Map bundle read failed: {exc}")
        return None

    zones = getattr(bundle, "clean_zones", None)
    return list(zones) if zones is not None else None


async def _send_rename(
    robot: Any, p2map_id: str, zones: list[Any], target: Any, new_name: str
) -> Any:
    from roombapy_prime.models.map_editing import (
        PermanentAreaV1,
        SetPermanentAreasV1,
    )

    areas = [
        PermanentAreaV1(
            area_id=str(
                getattr(z, "zone_id", None) or getattr(z, "area_id", "")
            ),
            name=new_name if z is target else (getattr(z, "name", "") or ""),
            geometry=z.geometry,
        )
        for z in zones
    ]
    return await robot.edit_map(p2map_id, SetPermanentAreasV1(areas=areas))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Name a clean zone. Renaming is a full-list rewrite -- see "
            "the module docstring."
        )
    )
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--blid", required=True)
    parser.add_argument("--map-id", default=None)
    parser.add_argument("--zone-id", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--i-understand-this-rewrites-my-zone-list",
        action="store_true",
        help=(
            "Send the command. Without it, nothing is sent and the "
            "current zones are printed."
        ),
    )
    args = parser.parse_args()

    if args.zone_id and not args.name:
        parser.error("--zone-id needs --name")

    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
