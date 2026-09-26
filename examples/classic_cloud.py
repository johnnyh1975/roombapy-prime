"""Classic robots in the cloud: one login, maps, mission history, parts.

Classic robots (900 series, i/s/j series) are controlled locally with
roombapy; this library carries their cloud side. One login serves every
robot on the account, and each robot gets the client for its generation.

Reads only -- nothing here changes anything on the account or a robot.

Credentials come from environment variables:

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    export ROOMBAPY_PRIME_COUNTRY=DE   # optional, defaults to US
    python examples/classic_cloud.py
"""

import asyncio
import datetime
import os
import sys

import aiohttp

from roombapy_prime import CloudAccount, CloudError
from roombapy_prime.account import CLASSIC


async def main() -> None:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    country_code = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD first.", file=sys.stderr)
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        # One login for the whole account. Every failure from iRobot's
        # cloud -- wrong password, rate limit, network, timeout -- is a
        # CloudError; the subclasses tell them apart when that matters.
        try:
            account = await CloudAccount.login(session, username, password, country_code)
        except CloudError as exc:
            print(f"Login failed: {exc}", file=sys.stderr)
            sys.exit(1)

        # One client serves every Classic robot on the account.
        classic = account.classic_rest()

        for blid, entry in account.robots.items():
            generation = account.generation(blid)
            print(f"\n{entry.name or blid}  sku={entry.sku}  generation={generation or 'unknown'}")
            if generation != CLASSIC:
                # Prime robots: account.prime_robot(blid). Unknown SKUs are
                # not guessed -- see CloudAccount.rest().
                continue

            try:
                maps = await classic.get_pmaps(blid)
                history = await classic.get_mission_history(blid, count=5)
                parts = await classic.get_robot_parts(blid)
            except CloudError as exc:
                print(f"  cloud request failed: {exc}")
                continue

            print(f"  {len(maps)} saved map(s)")
            for pmap in maps:
                print(f"    {pmap.get('pmap_id')}  active version {pmap.get('active_pmapv_id')}")

            records = history if isinstance(history, list) else []
            print(f"  last {len(records)} mission(s)")
            for record in records:
                started = record.get("startTime")
                when = (
                    datetime.datetime.fromtimestamp(started, datetime.UTC).isoformat(timespec="minutes")
                    if isinstance(started, int)
                    else "?"
                )
                print(f"    {when}  result={record.get('done')}  area={record.get('sqft')} sq ft")

            print("  part counters (percent used)")
            for part in parts.parts:
                print(f"    part {part.part_id}: {part.counter} %  ({part.minutes_remaining} min left)")


if __name__ == "__main__":
    asyncio.run(main())
