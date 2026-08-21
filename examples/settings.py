"""Writing robot settings, and checking that the write took.

The most-used method in the only real consumer of this library, and the
one with the most ways to go quietly wrong.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    export ROOMBAPY_PRIME_COUNTRY=US   # optional, defaults to US
    python examples/settings.py

READ-ONLY BY DEFAULT. It prints your current settings and stops. Pass
`--write` to change one, and it changes it back afterwards.

TWO THINGS WORTH KNOWING
------------------------

**Dotted keys are real keys, not paths.** `childLock` is a top-level
setting; `carpetBoost.enabled` is a single key whose name contains a
dot. Splitting on the dot and walking a nested structure does not
work — the wire format has no nesting here. Fourteen keys including
dotted ones were confirmed writable in the field.

**The response is an echo, not a confirmation.** `set_setting` returns
the shadow's reply, which is the value the robot *accepted*. Reading
back afterwards is the only way to know it *stored*, and those are two
different things — the same distinction that took three rounds to
establish for virtual walls.
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
    write = "--write" in sys.argv

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
            settings = await robot.get_settings()
            reported = (settings.payload or {}).get("state", {}).get("reported", {})

            print(f"Robot {robot.blid} reports {len(reported)} setting(s):\n")
            for key in sorted(reported):
                print(f"  {key:28s} {reported[key]!r}")

            if not write:
                print("\nRead-only. Pass --write to change one and revert it.")
                return

            # `childLock` because it is harmless, boolean, and present on
            # every robot anyone here has seen. A setting that changes
            # cleaning behaviour would be a poor thing to toggle in an
            # example somebody runs to see what happens.
            key = "childLock"
            if key not in reported:
                print(f"\n{key} not present on this robot -- nothing to demonstrate.")
                return

            original = reported[key]
            target = not original

            print(f"\nSetting {key}: {original!r} -> {target!r}")
            await robot.set_setting(key, target)

            # The read-back, which is the point of this example.
            after = await robot.get_settings()
            stored = (after.payload or {}).get("state", {}).get("reported", {}).get(key)

            if stored == target:
                print(f"  stored: {stored!r} -- write confirmed")
            else:
                print(
                    f"  stored: {stored!r} -- the robot accepted the command "
                    f"and kept its old value. Accepted is not stored."
                )

            print(f"Reverting {key} -> {original!r}")
            await robot.set_setting(key, original)

        finally:
            await robot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
