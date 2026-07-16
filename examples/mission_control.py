"""Mission control: sends a REAL command to your robot.

WARNING: unlike the other examples, this one actually controls your
robot. Basic commands (start/stop/pause/resume/dock) via
send_simple_command() are LIVE-CONFIRMED working — watched and
confirmed by a real user on a real device (see the README's confidence
table) — but this still moves your actual robot. This script asks for
interactive confirmation before sending anything, on purpose.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/mission_control.py start
    python examples/mission_control.py stop

Available commands: start, stop, pause, resume, dock (the confirmed
verb set — see prime_robot.py's send_simple_command() docstring for
why this is a plain string, not an enum, and for the richer,
region-aware — but unconfirmed — send_mission_command() alternative).
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime.prime_factory import PrimeFactory

AVAILABLE_COMMANDS = ("start", "stop", "pause", "resume", "dock")


async def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in AVAILABLE_COMMANDS:
        print(f"Usage: {sys.argv[0]} <command>", file=sys.stderr)
        print(f"Available commands: {', '.join(AVAILABLE_COMMANDS)}", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    country_code = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD first.", file=sys.stderr)
        sys.exit(1)

    confirm = input(f"This will send a real '{command}' command to your robot. Continue? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Aborted.")
        return

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code)
        await robot.connect()

        await robot.send_simple_command(command)
        print(f"'{command}' published — this path doesn't wait for a server acknowledgment "
              "(see send_simple_command()'s docstring for why), so watch/listen to the robot "
              "itself to confirm it actually reacted.")

        await robot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
