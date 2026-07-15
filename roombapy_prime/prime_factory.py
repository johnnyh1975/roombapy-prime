"""Factory: username/password/blid instead of a local IP (cloud client setup).

STATUS: Draft. Naming convention deliberately mirrors
roombapy.roomba_factory (RoombaFactory.create_roomba(...) ->
PrimeFactory.create_prime_robot(...)) for recognizability -- see
docs/ROOMBAPY_COMPARISON.md section 4. Unlike roombapy's factory,
this one is necessarily async, because establishing the connection
needs a real login flow (roombapy doesn't need this since it works
directly with a local IP + an already-known password).

NOT tested against a real V4 account -- pure wiring of already
individually documented building blocks (auth.py, mqtt_client.py,
rest_client.py, prime_robot.py).

Also has an auto_refresh parameter (see below) for proactive token
refresh -- see prime_robot.py's module docstring for the
credentials-in-memory tradeoff that comes with it.
"""
from __future__ import annotations

import aiohttp

from .auth import login
from .mqtt_client import PrimeMqttClient
from .prime_robot import PrimeRobot
from .rest_client import PrimeRestClient


class PrimeFactory:
    """Analogous to roombapy.RoombaFactory, but async and account-based
    instead of local-IP-based."""

    @staticmethod
    async def create_prime_robot(
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        country_code: str,
        blid: str | None = None,
        *,
        auto_refresh: bool = False,
    ) -> PrimeRobot:
        """Logs in, selects the robot (first one found if blid isn't
        given), wires up the MQTT and REST clients, returns a
        NOT-YET-connected PrimeRobot instance -- the caller still needs
        to await robot.connect() themselves.

        auto_refresh=True: keeps username/password in the closure of a
        relogin callback that's used two ways -- proactively by
        PrimeRobot shortly before the MQTT token expires (see
        prime_robot.py's module docstring for the credentials-in-memory
        tradeoff that comes with this), AND reactively by
        PrimeRestClient on an HTTP 403 on a p2maps call (see
        rest_client.py). Default False -- previous behavior
        (credentials expire after ~1h), no surprise for existing
        callers of this method."""
        login_result = await login(session, username, password, country_code)
        target_blid = blid or login_result.primary_blid()
        token = login_result.token_for_blid(target_blid)

        mqtt_client = PrimeMqttClient(
            token=token,
            endpoint=login_result.mqtt_endpoint,
            blid=target_blid,
        )

        relogin = None
        if auto_refresh:

            async def relogin():
                return await login(session, username, password, country_code)

        rest_client = PrimeRestClient(
            session=session,
            http_base_auth=login_result.http_base_auth,
            credentials=login_result.credentials,
            relogin=relogin,
        )

        return PrimeRobot(
            blid=target_blid,
            mqtt_client=mqtt_client,
            rest_client=rest_client,
            relogin=relogin,
            irbt_topic_prefix=login_result.irbt_topic_prefix,
            deployment=login_result.deployment,
        )
