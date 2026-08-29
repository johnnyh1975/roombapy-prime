"""Reading a robot's map: versions, region names, and the bundle.

This is the part of the API with the most field work behind it and the
least obvious shape, so it gets its own example.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/maps.py

Reads only. Nothing here changes anything on the robot or in the cloud.
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime import PrimeFactory
from roombapy_prime.models import parse_map_bundle
from roombapy_prime.models.robot_info import parse_active_map_versions


async def main() -> int:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD.")
        return 1
    country = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(
            session, username, password, country
        )
        await robot.connect()
        try:
            # The account lists which map versions are active; the
            # metadata call then gives the full P2MapData for one map.
            versions = parse_active_map_versions(
                await robot.get_active_map_versions()
            )
            if not versions:
                print("This robot has no saved maps.")
                return 0

            for entry in versions:
                map_data = await robot.get_map_metadata(entry.p2map_id)
                print(f"\nMap {map_data.p2map_id}: {map_data.name or '(unnamed)'}")

                # ASK THE MAP WHICH VERSION IS CURRENT, do not read a
                # field directly. Robots disagree about which one they
                # populate: some send `active_p2mapv_id`, some send
                # `user_p2mapv_id` and `p2mapv_id` and no `active_` field
                # at all. A bundle request without a version returns
                # whatever the server defaults to, which may predate an
                # edit made minutes ago.
                version = map_data.current_map_version
                if version is None:
                    print("  No version reported — cannot read regions.")
                    continue
                print(f"  Current version: {version}")

                # Region names come from two places and neither is
                # complete on its own.
                #
                # `rooms_metadata` carries a name for SOME rooms. It is
                # also a snapshot that lags edits in both directions: a
                # zone created minutes ago may be absent, one deleted
                # minutes ago may still be listed.
                for room in map_data.rooms_metadata:
                    if room.name:
                        print(f"    {room.room_id}: {room.name}  [metadata]")

                # The bundle's zone layers carry the rest. Note that
                # `policyZones` has no name field at all — keep-out and
                # no-mop zones are unnamed by design, confirmed from a
                # raw dump. Absence there is not a parsing failure.
                names = await robot.get_map_region_names(
                    map_data.p2map_id, version
                )
                for rid, name in sorted(names.items()):
                    print(f"    {rid}: {name}  [bundle]")

                # The bundle itself, if you want the geometry rather
                # than just the names. Returns a dict, not a URL string.
                link = await robot.get_map_geojson_link(
                    map_data.p2map_id, version
                )
                url = link.get("url") if isinstance(link, dict) else None
                if url:
                    raw = await robot.download_map_bundle(url)
                    parsed = parse_map_bundle(raw)
                    print(f"  Bundle layers: {', '.join(sorted(parsed))}")

        finally:
            await robot.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
