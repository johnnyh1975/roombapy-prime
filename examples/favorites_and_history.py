"""Favorites and mission history: two read-only, low-risk look-arounds.

Same credential setup as basic_usage.py:

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/favorites_and_history.py
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime.models import parse_mission_history
from roombapy_prime.prime_factory import PrimeFactory


async def main() -> None:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    country_code = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD first.", file=sys.stderr)
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code)

        print("== Favorites ==")
        favorites = await robot.get_favorites()
        if not favorites:
            print("  (none saved)")
        for fav in favorites:
            steps = len(fav.command_defs)
            print(f"  - {fav.name!r} ({steps} step{'s' if steps != 1 else ''}, id={fav.favorite_id})")

        print("\n== Recent mission history ==")
        raw_history = await robot.get_mission_history(robot.blid, max_reports=5)
        for entry in parse_mission_history(raw_history):
            duration = f"{entry.duration_m} min" if entry.duration_m is not None else "?"
            coverage = f"{entry.square_feet_covered} sq ft" if entry.square_feet_covered is not None else "?"
            print(f"  - {entry.mission_id}: {entry.done_code}, {duration}, {coverage} covered")


if __name__ == "__main__":
    asyncio.run(main())
