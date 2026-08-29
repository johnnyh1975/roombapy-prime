"""Reading and setting Do Not Disturb hours.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/do_not_disturb.py

Reads by default. Writing is behind a flag:

    python examples/do_not_disturb.py --set-quiet-hours
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime import PrimeFactory
from roombapy_prime.models.schedules_dnd import DNDDailySchedule


async def main() -> int:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD.")
        return 1
    country = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")
    do_write = "--set-quiet-hours" in sys.argv

    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(
            session, username, password, country
        )
        await robot.connect()
        try:
            # DND is a HOUSEHOLD setting, not a robot one. It applies to
            # every robot on the account, which is why this needs the
            # household id rather than the blid.
            household_id = await robot.get_household_id()
            if household_id is None:
                print("No household id — cannot read DND settings.")
                return 1

            current = await robot.get_dnd_settings(household_id)
            print(f"Current DND settings: {current}")

            if not do_write:
                print("\nPass --set-quiet-hours to set 22:00–07:00 daily.")
                return 0

            # TWO MUTUALLY EXCLUSIVE SHAPES, and only one may be sent:
            #
            #   {"dailyStart": int, "dailyEnd": int}   quiet every day
            #   {"endsAt": long}                       quiet until a moment
            #
            # The app's own type system makes sending both impossible.
            # The one live attempt that did returned HTTP 400.
            #
            # `from_clock` exists so callers do not repeat the
            # minutes-since-midnight conversion and get it subtly wrong.
            schedule = DNDDailySchedule.from_clock(
                start_hour=22, start_minute=0,
                end_hour=7, end_minute=0,
            )
            print(f"\nSetting quiet hours: {schedule.to_json()}")

            result = await robot.set_dnd_settings(
                household_id, schedule.to_json()
            )
            print(f"Response: {result}")

        finally:
            await robot.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
