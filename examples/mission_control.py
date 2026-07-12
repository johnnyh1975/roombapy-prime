"""Mission control: sends a REAL command to your robot.

WARNING: unlike the other examples, this one actually controls your
robot. The command payload/transport are individually confirmed from
source and binary analysis, but the two were never confirmed *together*
against a real server — see the README's confidence table. This script
asks for interactive confirmation before sending anything, on purpose.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/mission_control.py clean
    python examples/mission_control.py stop
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime.models import MissionCommandType, RoutineCommand
from roombapy_prime.prime_factory import PrimeFactory


async def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <command>", file=sys.stderr)
        print(f"Available commands: {', '.join(c.name.lower() for c in MissionCommandType)}", file=sys.stderr)
        sys.exit(1)

    try:
        command_type = MissionCommandType[sys.argv[1].upper()]
    except KeyError:
        print(f"Unknown command {sys.argv[1]!r}.", file=sys.stderr)
        print(f"Available commands: {', '.join(c.name.lower() for c in MissionCommandType)}", file=sys.stderr)
        sys.exit(1)

    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    country_code = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD first.", file=sys.stderr)
        sys.exit(1)

    confirm = input(
        f"This will send a real '{command_type.value}' command to your robot. "
        "This path has never been confirmed against a live server (see README). "
        "Continue? [y/N] "
    )
    if confirm.strip().lower() != "y":
        print("Aborted.")
        return

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code)
        await robot.connect()

        response = await robot.send_mission_command(
            RoutineCommand(command_type=command_type, asset_id=robot.blid)
        )
        print("Server response:", response.payload)

        await robot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
