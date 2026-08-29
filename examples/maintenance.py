"""Reading and resetting the robot's consumable part counters.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/maintenance.py

Reads by default. The reset is behind a flag, because it writes to
iRobot's cloud and cannot be undone:

    python examples/maintenance.py --reset-filter
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime import PrimeFactory


async def main() -> int:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD.")
        return 1
    country = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")
    do_reset = "--reset-filter" in sys.argv

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(
            session, username, password, country
        )
        await robot.connect()
        try:
            info = await robot.get_robot_parts()
            print(f"{info.num_parts} part(s):\n")

            filter_id = None
            for part in info.parts:
                # `count_type` is NOT always minutes. Field captures show
                # `evacs`, `combo_missions` and `pad_washes_used` on one
                # robot alongside `minutes` — so a value cannot be
                # converted to hours without checking this first.
                print(
                    f"  {part.part_id:>6}  {part.count_used:>6} used, "
                    f"{part.count_remaining:>6} left  ({part.count_type})"
                )
                if part.count_type == "minutes" and filter_id is None:
                    filter_id = part.part_id

            # A counter that never moves is not necessarily a bug here.
            # One field robot reports 0 evacuations across 379 missions
            # on a dock that can evacuate: iRobot is not feeding that
            # counter. Nothing this library can fix, and worth knowing
            # before replacing a part on the strength of the number.

            if not do_reset:
                print("\nPass --reset-filter to reset the first minute-based part.")
                return 0

            if filter_id is None:
                print("\nNo minute-based part found to reset.")
                return 1

            # WRITES TO IROBOT'S CLOUD. The app will show the new value
            # too, and there is no undo — the previous counter is gone.
            print(f"\nResetting part {filter_id}...")
            result = await robot.reset_robot_parts(part_ids=[filter_id])
            print(f"Response: {result}")

        finally:
            await robot.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
