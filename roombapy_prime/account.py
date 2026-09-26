"""One iRobot account: one login, shared by every robot on it.

WHY IT EXISTS (0.4.0). Login, credentials and the session belong to the
ACCOUNT; only the robots differ. Until now this library offered one
entry point per Prime robot (PrimeFactory), and a Classic caller would
have needed a second one next to it -- two ways into the same account,
each with its own login. iRobot limits how many sessions an account may
hold, and the error it answers with (AuthRateLimitedError, "close the
iRobot app and try again") is exactly what two logins where one would do
brings closer.

CloudAccount is the one way in:

    account = await CloudAccount.login(session, username, password, "DE")
    account.robots                  # every robot on the account
    account.generation(blid)        # "prime", "classic", or None (unknown)
    account.rest(blid)              # ClassicRestClient or PrimeRestClient
    await account.prime_robot(blid) # PrimeRobot with MQTT, Prime only

WHAT IS DELIBERATELY NOT HERE: one robot class for both generations.
Such a class would carry methods that fail at runtime for one of them --
get_pmaps() on a Prime robot, MQTT commands on a Classic one. The clients
stay separate; the account only hands out the right one.

UNKNOWN IS AN ANSWER. The SKU tables are incomplete for models nobody has
tested. rest(blid) refuses a robot of unknown generation rather than
guess; a caller who knows better uses classic_rest() or prime_rest().

ONE RELOGIN FOR ALL CLIENTS. Every REST client this account hands out
relogs through the account on HTTP 403. Clients that hit 403 together
wait for a single login, and a client still holding credentials the
account has already replaced picks up the new ones instead of logging in
again.

NOT YET SHARED: prime_robot() goes through PrimeFactory, which keeps its
own relogin for the MQTT token. Moving that onto the account is a
separate step, left for when the Prime path is changed on purpose.
"""
from __future__ import annotations

import asyncio
from typing import TypeVar

import aiohttp

from .auth import (
    _APP_ID,
    DEFAULT_REQUEST_TIMEOUT,
    CloudCredentials,
    LoginResult,
    RobotLoginEntry,
    is_classic_sku,
    is_prime_sku,
    login,
)
from .prime_factory import PrimeFactory
from .prime_robot import PrimeRobot
from .rest_client import ClassicRestClient, CloudRestClient, PrimeRestClient

_ClientT = TypeVar("_ClientT", bound=CloudRestClient)

#: Generation names, as generation() returns them.
PRIME = "prime"
CLASSIC = "classic"

#: The app id login() uses unless told otherwise.
DEFAULT_APP_ID = _APP_ID


class CloudAccount:
    """An iRobot account after login. Build one with CloudAccount.login()."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        country_code: str,
        login_result: LoginResult,
        *,
        app_id: str = DEFAULT_APP_ID,
        request_timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._country_code = country_code
        self._login_result = login_result
        self._relogin_lock = asyncio.Lock()
        #: The id this account logged in with. Classic's mission history
        #: sends it again, so the Classic client gets it too.
        self.app_id = app_id
        #: Seconds per HTTP request, for every login this account makes
        #: and every client it hands out (None: no limit).
        self.request_timeout = request_timeout

    @classmethod
    async def login(
        cls,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        country_code: str,
        *,
        app_id: str = DEFAULT_APP_ID,
        request_timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    ) -> CloudAccount:
        """Logs in once and returns the account. Raises the auth errors
        login() raises (AuthCredentialsError, AuthRateLimitedError, ...)."""
        result = await login(
            session, username, password, country_code,
            app_id=app_id, request_timeout=request_timeout,
        )
        return cls(
            session, username, password, country_code, result,
            app_id=app_id, request_timeout=request_timeout,
        )

    @property
    def login_result(self) -> LoginResult:
        """The current login -- replaced whenever the account relogs."""
        return self._login_result

    @property
    def robots(self) -> dict[str, RobotLoginEntry]:
        """Every robot on the account, keyed by BLID."""
        return self._login_result.robots

    def generation(self, blid: str) -> str | None:
        """PRIME, CLASSIC, or None when the SKU is in neither table.
        Raises KeyError for a BLID that is not on this account."""
        entry = self.robots.get(blid)
        if entry is None:
            raise KeyError(f"{blid} is not a robot on this account")
        if is_prime_sku(entry.sku):
            return PRIME
        if is_classic_sku(entry.sku):
            return CLASSIC
        return None

    def classic_rest(self) -> ClassicRestClient:
        """A Classic REST client on this account's session and login."""
        return self._attach(ClassicRestClient(
            self._session,
            self._login_result.http_base_auth,
            self._login_result.credentials,
            app_id=self.app_id,
            request_timeout=self.request_timeout,
        ))

    def prime_rest(self) -> PrimeRestClient:
        """A Prime REST client on this account's session and login."""
        return self._attach(PrimeRestClient(
            self._session,
            self._login_result.http_base_auth,
            self._login_result.credentials,
            request_timeout=self.request_timeout,
        ))

    def rest(self, blid: str) -> ClassicRestClient | PrimeRestClient:
        """The REST client for this robot's generation. Raises
        ValueError when the generation is unknown -- see the module
        docstring for why that is not guessed."""
        generation = self.generation(blid)
        if generation == PRIME:
            return self.prime_rest()
        if generation == CLASSIC:
            return self.classic_rest()
        raise ValueError(
            f"sku={self.robots[blid].sku!r} of {blid} is in neither SKU table; "
            "use classic_rest() or prime_rest() if you know which it is"
        )

    async def prime_robot(self, blid: str, *, auto_refresh: bool = False) -> PrimeRobot:
        """A PrimeRobot for this BLID, built from this account's login --
        no second login. Refuses a robot known to be Classic."""
        if self.generation(blid) == CLASSIC:
            raise ValueError(f"{blid} is a Classic robot; PrimeRobot is for Prime robots")
        return await PrimeFactory.create_prime_robot(
            session=self._session,
            username=self._username,
            password=self._password,
            country_code=self._country_code,
            blid=blid,
            auto_refresh=auto_refresh,
            login_result=self._login_result,
        )

    async def relogin(self) -> LoginResult:
        """Logs in again now, with the same app id, and returns the new
        login. Clients relog on their own on HTTP 403; this is for a
        caller that knows the login has gone stale."""
        async with self._relogin_lock:
            self._login_result = await self._login()
            return self._login_result

    def _attach(self, client: _ClientT) -> _ClientT:
        """Routes the client's 403 relogin through the account."""
        async def relogin() -> LoginResult:
            return await self._refresh(client._credentials)

        client._relogin = relogin
        return client

    async def _refresh(self, stale: CloudCredentials) -> LoginResult:
        """One login for however many clients noticed at once. A client
        whose credentials the account has already replaced gets the
        current login without a new one."""
        async with self._relogin_lock:
            if self._login_result.credentials is not stale:
                return self._login_result
            self._login_result = await self._login()
            return self._login_result

    async def _login(self) -> LoginResult:
        return await login(
            self._session, self._username, self._password, self._country_code,
            app_id=self.app_id, request_timeout=self.request_timeout,
        )
