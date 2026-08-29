"""Watching a robot live instead of polling it.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/watching.py

Runs until interrupted. Reads only — watching sends nothing to the
robot beyond the subscription itself.

Start a mission from the app to see anything: a docked robot produces
position updates rarely, and map updates not at all.
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime import PrimeFactory
from roombapy_prime.models import MapUpdateMessage, PositionUpdateMessage


async def watch_positions(robot) -> None:
    """The robot's own position stream.

    WHY WATCH RATHER THAN POLL. Position arrives as the robot moves, so
    polling either misses updates between calls or hammers the
    connection to catch them. `watch_live_map` yields each one as it
    lands.

    The iterator reconnects on its own; the queue drops oldest first if
    a consumer falls behind, so a slow handler loses samples rather than
    stalling the stream.
    """
    async for message in robot.watch_live_map():
        if isinstance(message, PositionUpdateMessage):
            print(f"  pose: {message}")
        elif isinstance(message, MapUpdateMessage):
            print(f"  map updated: {message}")


async def watch_dock(robot) -> None:
    """Dock reports: evacuation, pad washing, pad drying.

    A separate stream from position, and the one that tells you what
    the DOCK is doing rather than the robot.
    """
    async for report in robot.watch_dock_reports():
        print(f"  dock: {report}")


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
        print("Watching. Ctrl-C to stop.\n")

        # Both streams at once. Each has its own subscription, so one
        # falling over does not take the other with it.
        tasks = [
            asyncio.create_task(watch_positions(robot)),
            asyncio.create_task(watch_dock(robot)),
        ]
        try:
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await robot.disconnect()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
