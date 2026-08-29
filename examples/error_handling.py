"""Handling the errors this library raises.

The other examples show the happy path. This one shows what to do when
things fail, because the distinction that matters is not obvious from
the names: one authentication failure will never succeed on retry, and
the rest might.

    export ROOMBAPY_PRIME_USERNAME=you@example.com
    export ROOMBAPY_PRIME_PASSWORD=hunter2
    python examples/error_handling.py
"""

import asyncio
import os
import sys

import aiohttp

from roombapy_prime import (
    AuthConnectionError,
    AuthCredentialsError,
    AuthError,
    AuthRateLimitedError,
    AuthSSLError,
    AuthTimeoutError,
    PrimeFactory,
    RestError,
    ShadowError,
)


async def connect_with_retry(
    session: aiohttp.ClientSession,
    username: str,
    password: str,
    country: str = "US",
    attempts: int = 3,
):
    """Log in, retrying only the failures that can succeed on retry.

    THE ONE DISTINCTION THAT MATTERS. `AuthCredentialsError` means the
    credentials were rejected -- retrying sends the same rejected
    password and will fail identically. Everything else is a transport
    condition that may well work a second later.

    Retrying a credentials failure is not merely useless: repeated
    rejected logins are what produces `AuthRateLimitedError`, so a naive
    retry loop turns a fixable problem into a waiting one.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await PrimeFactory.create_prime_robot(
                session, username, password, country
            )

        except AuthCredentialsError:
            # Terminal. Ask the user for new credentials.
            print("Login rejected: check the username and password.")
            raise

        except AuthRateLimitedError:
            # iRobot limits how many app sessions an account may have.
            # Signing out of the phone app frees one. Waiting also works,
            # but not on the timescale of a retry loop.
            print("Too many active sessions. Sign out of the iRobot app.")
            raise

        except AuthSSLError as err:
            # Not transient. A certificate failure means something is
            # intercepting the connection, or the system trust store is
            # out of date -- retrying hides the cause.
            print(f"TLS verification failed: {err}")
            raise

        except (AuthTimeoutError, AuthConnectionError) as err:
            # Worth retrying: the request never arrived or never came
            # back.
            if attempt == attempts:
                print(f"Giving up after {attempts} attempts: {err}")
                raise
            wait = 2 ** attempt
            print(f"Attempt {attempt} failed ({err}); retrying in {wait}s")
            await asyncio.sleep(wait)

    raise AssertionError("unreachable")


async def main() -> int:
    username = os.environ.get("ROOMBAPY_PRIME_USERNAME")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD")
    if not username or not password:
        print("Set ROOMBAPY_PRIME_USERNAME and ROOMBAPY_PRIME_PASSWORD.")
        return 1

    async with aiohttp.ClientSession() as session:
        country = os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US")

        try:
            robot = await connect_with_retry(
                session, username, password, country
            )
        except AuthError:
            # Every auth failure derives from AuthError, so this catches
            # anything the function re-raised. It must come LAST: listed
            # before the specific cases it would swallow them.
            return 1

        try:
            await robot.connect()
            state = await robot.get_state()
            print(f"Connected. Phase: {state.clean_mission_status.phase}")

        except ShadowError as err:
            # MQTT: the robot may simply be offline. This is the normal
            # condition for a robot whose dock is unplugged, not a bug.
            print(f"Could not reach the robot over MQTT: {err}")
            return 1

        except RestError as err:
            # HTTP: iRobot's API is unreachable or slow. Cloud-only
            # features degrade; anything already cached still works.
            print(f"iRobot's API is unreachable: {err}")
            return 1

        finally:
            await robot.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
