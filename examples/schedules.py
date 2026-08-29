"""Reading and changing cleaning schedules.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/schedules.py              # lists them
    python examples/schedules.py --disable ID # turns one off

READ-ONLY BY DEFAULT.

THE WEEKDAY BASIS IS THE TRAP
-----------------------------

**The robot counts from Sunday. Python counts from Monday.**

    Python  datetime.weekday()   Mon=0 Tue=1 ... Sun=6
    Wire    days                 sun=0 mon=1 ... sat=6

Handing `datetime.weekday()` straight to the wire format shifts every
day by one, and the result looks plausible: a Monday schedule lands on
Sunday and still runs, so nothing errors. This project got it wrong
three separate times before a guard test pinned both ends.

Convert with `(weekday + 1) % 7`, or read the day off an existing
schedule rather than computing it.

**A schedule is a whole object.** `update_schedules` takes the full
list for a container and replaces it — a partial list deletes what it
omits. Read first, change one field, send everything back. The same
full-replace shape as virtual walls and clean zones.
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime import PrimeFactory

_WIRE_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


async def main() -> None:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    country_code = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

    disable_id = None
    if "--disable" in sys.argv:
        disable_id = sys.argv[sys.argv.index("--disable") + 1]

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
            # `get_user_households` returns the raw response.
            households = await robot.get_user_households()
            entries = households.get("household") or households.get("households") or []
            if not entries:
                print("No households in the response.")
                return

            household_id = entries[0].get("household_id")
            response = await robot.get_schedules(household_id)
            containers = response.household_schedules

            if not containers:
                print("No schedules.")
                return

            for container in containers:
                print(f"\nContainer {container.household_schedule_id}:")

                for schedule in container.schedules or []:
                    options = schedule.options
                    # The days live on `start`, a ScheduleTime of
                    # (day, hour, min) -- not on the options directly.
                    start = options.start
                    days = ", ".join(
                        _WIRE_DAYS[d] if isinstance(d, int) and 0 <= d < 7 else str(d)
                        for d in (getattr(start, "day", None) or [])
                    )
                    state = "on " if options.enabled else "off"
                    print(
                        f"  [{state}] {schedule.schedule_id}  "
                        f"{options.name or '(unnamed)'}  days={days or '-'}"
                    )

            if not disable_id:
                print("\nRead-only. Pass --disable ID to turn one off.")
                return

            # FULL REPLACE. Every schedule in the container goes back,
            # with one field changed on one of them. Sending only the
            # changed schedule deletes the rest.
            for container in containers:
                schedules = list(container.schedules or [])
                if not any(s.schedule_id == disable_id for s in schedules):
                    continue

                for schedule in schedules:
                    if schedule.schedule_id == disable_id:
                        schedule.options.enabled = False
                        print(f"\nDisabling {disable_id}")

                await robot.update_schedules(
                    household_id,
                    container.household_schedule_id,
                    schedules,
                )
                print(f"Sent {len(schedules)} schedule(s) back -- all of them.")
                return

            print(f"\nNo schedule with id {disable_id}.")

        finally:
            await robot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
