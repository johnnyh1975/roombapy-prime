"""Cleaning specific rooms or zones.

This is the reason to use this library rather than `roombapy`: a Prime
robot can be told to clean region 15 and nothing else.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/clean_regions.py                 # lists regions
    python examples/clean_regions.py --clean 15 16   # actually cleans

READ-ONLY BY DEFAULT. Without `--clean` it prints the regions on your
active map and stops.

FOUR THINGS THAT HAVE GONE WRONG HERE
-------------------------------------

**`command_type` is START, not CLEAN.** A `CLEAN` command with
`map_id=None` returns a PUBACK and cleans the whole house. The broker
accepts it; the robot does something else entirely.

**`map_id` is required.** Same failure as above. A region id without
the map it belongs to is not a location.

**Region ids are per map.** `15` on one floor plan and `15` on another
are different rooms. A household with several maps needs the id
qualified, and guessing which map means cleaning the wrong floor.

**Rooms are `RID`, zones are `ZID`.** Both are regions and a command
takes either, but the type has to match what the map says — which is
why this example reads them rather than letting you type one.
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime.models import (
    MissionCommandType,
    Region,
    RegionType,
    RoutineCommand,
)
from roombapy_prime.models.robot_info import parse_active_map_versions
from roombapy_prime.prime_factory import PrimeFactory


async def main() -> None:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    country_code = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

    wanted: list[str] = []
    if "--clean" in sys.argv:
        wanted = sys.argv[sys.argv.index("--clean") + 1:]

    if not username or not password:
        print(
            "Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD first.",
            file=sys.stderr,
        )
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(
            session, username, password, country_code
        )
        await robot.connect()

        try:
            # `get_active_map_versions` returns raw dicts. The parser
            # turns them into `P2MapVersion` -- reaching for `.p2map_id`
            # on the raw form fails at runtime, not at import.
            versions = parse_active_map_versions(
                await robot.get_active_map_versions()
            )
            if not versions:
                print("No active map versions -- has the robot mapped your home?")
                return

            # The first active map. A multi-map household has more, and
            # picking one for the user is exactly the guess that cleans
            # the wrong floor -- so this prints which one it chose.
            version = versions[0]
            p2map_id = version.p2map_id
            print(f"Map: {p2map_id}")

            metadata = await robot.get_map_metadata(p2map_id)
            regions = metadata.rooms_metadata or []

            if not regions:
                print("No regions on this map.")
                return

            print(f"\n{len(regions)} region(s):\n")
            for region in regions:
                kind = "room" if region.region_type == RegionType.RID else "zone"
                print(
                    f"  {region.room_id:>5}  {kind:5s}  {region.name or '(unnamed)'}"
                )

            if not wanted:
                print("\nRead-only. Pass --clean ID [ID ...] to send a command.")
                return

            known = {str(r.room_id): r for r in regions}
            missing = [rid for rid in wanted if rid not in known]
            if missing:
                # Refused rather than sent partially. Cleaning some of
                # what was asked for looks like success from the outside.
                print(f"\nNot on this map: {missing}. Nothing sent.")
                return

            command = RoutineCommand(
                command_type=MissionCommandType.START,
                asset_id=robot.blid,
                map_id=p2map_id,
                regions=[
                    Region(
                        region_id=str(known[rid].room_id),
                        region_type=known[rid].region_type,
                    )
                    for rid in wanted
                ],
            )

            names = ", ".join(known[rid].name or rid for rid in wanted)
            print(f"\nSending: clean {names}")
            response = await robot.send_mission_command(command)
            print(f"Response: {response.payload}")

            print(
                "\nA response means the broker accepted it. Watch the robot, "
                "or watch_mission_timeline(), to see what it actually does."
            )

        finally:
            await robot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
