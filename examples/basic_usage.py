"""Basic usage: log in, connect, read current state, watch for updates.

Credentials come from environment variables so this is safe to commit
and share — nothing sensitive is hardcoded:

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    export ROOMBAPY_PRIME_COUNTRY=US   # optional, defaults to US
    python examples/basic_usage.py
"""

import asyncio
import os
import sys

import aiohttp

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
        await robot.connect()
        print(f"Connected to robot {robot.blid}")

        state = await robot.get_state()
        print("Current state:", state.payload)

        print("\nWatching for updates (Ctrl+C to stop)...")
        try:
            async for delta in robot.watch_state():
                print("Update:", delta.payload)
        except KeyboardInterrupt:
            pass
        finally:
            await robot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
