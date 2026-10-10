"""p2maps REST client: map metadata, edit commands, live stream init.

STATUS: Field-exercised. The endpoint shapes came from Java source
analysis (see docs/archive/FINDINGS_2026-07-11.md); they have since been
run against real accounts by a dozen testers.

CONFIRMED ON HARDWARE: map metadata and bundle reads, map editing
(rename, split, merge -- the last two on a Combo 105), the firmware
catalogue, favourites and schedules. The read surface is the
best-attested part of this library.

STILL UNRUN, and marked as such at each call site rather than here:
p2map upload, and the services write path. A blanket claim at the top of
the file that nothing has been executed was true when written and is
badly misleading now -- individual doubts belong next to the individual
calls, where a reader is deciding whether to trust one.

Also: AWS SigV4 signing (see aws_sigv4.py) and `http_base_auth` instead
of `http_base` -- both carried over from ha_roomba_plus's already-
production cloud_api.py (a third, independent confirmation source
alongside live tests and APK analysis). Previously, `auth_headers` here
was a vague, never-populated passthrough dict -- that was simply
modeled wrong, not just incomplete.

Open questions, deliberately not guessed at:
  - Whether p2maps needs SigV4 signing at all (vs. e.g. one of the
    login tokens as a Bearer header) is an analogy assumption from
    other /v1/ endpoints in the same cloud API family, not a fact
    confirmed for p2maps itself.
  - SigV4 for POST-with-body is MY extension of the original (which
    only signs GET) -- see aws_sigv4.py's docstring.
  - 403 -> reauth retry is carried over 1:1 from cloud_api.py's
    _aws_get() (confirmed there for Classic REST endpoints), applied
    here to p2maps for the first time.
"""
from __future__ import annotations

import asyncio
import datetime
import email.utils
import json
import logging
import math
import urllib.parse
from collections.abc import Awaitable, Callable, Mapping, Sequence
from json.decoder import JSONDecodeError
from typing import Any, NoReturn, cast

import aiohttp

from .auth import DEFAULT_REQUEST_TIMEOUT, CloudCredentials, LoginResult, _ssl_diagnosis
from .aws_sigv4 import AwsSigV4Signer
from .errors import CloudError, CloudErrorReason, reason_for_status
from .models.enums_common import _enum_or_none
from .models import (
    DNDStatusResponse,
    FavoriteV1,
    HouseholdSchedule,
    LiveMapStreamInit,
    MapEditCommand,
    MapEditCommandV1,
    MissionCommandType,
    P2MapData,
    PartsCatalog,
    RobotPartsInfo,
    RobotSerialInfo,
    RoutineCommand,
    RoutinesDefaultsResponse,
    ScheduleOptions,
    SchedulesResponse,
)

_LOGGER = logging.getLogger(__name__)

Relogin = Callable[[], Awaitable[LoginResult]]

#: The content host, `ContentStackHost` in the app: unauthenticated, and
#: the same `content-{env}` pattern get_firmware_raw() documents. No
#: discovery field carries it, so production is written down here.
_CONTENT_HOST = "https://content-prod.iot.irobotapi.com"


def _path_segment(value: str) -> str:
    """NEW (session 54, security hardening pass). Every identifier this
    library embeds into a URL path (BLIDs, p2map IDs, favorite IDs,
    household IDs, etc.) was previously interpolated directly via an
    f-string, with no escaping at all. In this library's own normal
    usage these values typically come from a trusted source (this
    library's own login/API responses, or values a developer passes
    directly) -- but this library provides no protection at all if a
    consuming application (e.g. a Home Assistant integration built on
    top of this, which is an explicit goal for this project) ever lets
    a corrupted config value or, in principle, untrusted input reach
    one of these parameters. A value like `"../whatever"` or one
    containing a literal `/` could redirect the request to an
    unintended path on the same host.

    `urllib.parse.quote(value, safe="")` is a no-op for any
    legitimate identifier this API actually uses (BLIDs, UUIDs, and
    similar are alphanumeric/hyphen strings, unaffected by
    URL-encoding) -- this is purely additive safety, not a behavior
    change for any well-formed input. Applied at every URL
    construction site in this file that embeds a caller-supplied
    identifier."""
    return urllib.parse.quote(str(value), safe="")


class RestError(CloudError):
    """Raised for any non-2xx response or unparseable body, with the
    raw response text attached where available.

    A PLAIN RestError is an answer in a shape the call did not expect,
    so that is its reason -- unless it carries an HTTP error status, in
    which case the status says why (reason_for_status), exactly as for
    the RestHTTPError subclasses. A subclass with a reason of its own
    keeps it."""

    reason: CloudErrorReason = CloudErrorReason.RESPONSE_MALFORMED

    def __init__(
        self,
        message: str,
        status: int | None = None,
        raw_response: str | None = None,
        *,
        reason: CloudErrorReason | None = None,
    ) -> None:
        if (
            reason is None
            and status is not None
            and status >= 400
            and type(self).reason is CloudErrorReason.RESPONSE_MALFORMED
        ):
            reason = reason_for_status(status)
        super().__init__(message, reason=reason)
        self.status = status
        self.raw_response = raw_response


class RestSSLError(RestError):
    """TLS/certificate verification failure -- see
    _raise_clear_ssl_error(). `reason` says which of three causes."""

    reason: CloudErrorReason = CloudErrorReason.SSL_UNVERIFIED


class RestConnectionError(RestError):
    """Could not establish a connection at all (DNS failure, connection
    refused, network unreachable) -- see _raise_clear_connection_error().
    Deliberately does NOT claim to know whether this is iRobot's fault
    or the caller's own network.

    `reason` is CONNECTION_BROKEN when a connection was made and broke
    off mid-answer, CONNECTION_FAILED when none was made."""

    reason: CloudErrorReason = CloudErrorReason.CONNECTION_FAILED


class RestTimeoutError(RestError):
    """Request was sent but no response came back in time -- see
    _raise_clear_timeout_error()."""

    reason: CloudErrorReason = CloudErrorReason.TIMEOUT


class RestHTTPError(RestError):
    """The server answered with an HTTP error status (4xx or 5xx);
    `status` is always set.

    NEW IN 0.4.0, and only a finer name for what was raised before:
    until then every such answer was a plain RestError with the status
    in an attribute, so a caller could not tell "do not send this again"
    from "try again later" without reading numbers. The three subclasses
    below say it. Every `except RestError` still catches all of them."""

    status: int

    def __init__(self, message: str, status: int, raw_response: str | None = None) -> None:
        super().__init__(message, status=status, raw_response=raw_response)


class RestClientError(RestHTTPError):
    """4xx other than 429: the request itself was refused -- not found,
    not allowed, malformed. Sending it again unchanged will not help.
    A 403 reaches here only after the one relogin has not cured it."""

    reason: CloudErrorReason = CloudErrorReason.REQUEST_REFUSED


class RestRateLimitedError(RestHTTPError):
    """429: too many requests. Try again later -- `retry_after` is the
    wait in seconds the server asked for, or None when it named none."""

    reason: CloudErrorReason = CloudErrorReason.RATE_LIMITED

    def __init__(
        self,
        message: str,
        status: int = 429,
        raw_response: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status, raw_response)
        self.retry_after = retry_after


class RestServerError(RestHTTPError):
    """5xx: the problem is on iRobot's side. Usually temporary."""

    reason: CloudErrorReason = CloudErrorReason.SERVER_ERROR


def _retry_after_seconds(value: str | None) -> float | None:
    """A Retry-After header as seconds from now: either a number of
    seconds or an HTTP date (RFC 9110). None when absent or unreadable,
    and never negative."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.UTC)
    return max(0.0, (when - datetime.datetime.now(datetime.UTC)).total_seconds())


def _http_error(
    status: int,
    message: str,
    raw_response: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> RestHTTPError:
    """The RestHTTPError subclass for an error status."""
    if status == 429:
        retry_after = _retry_after_seconds((headers or {}).get("Retry-After"))
        return RestRateLimitedError(message, status, raw_response, retry_after=retry_after)
    if 400 <= status < 500:
        return RestClientError(message, status, raw_response)
    return RestServerError(message, status, raw_response)


def _raise_clear_ssl_error(exc: aiohttp.ClientSSLError) -> NoReturn:
    """Re-raise an aiohttp SSL/certificate failure as a clear
    RestSSLError instead of letting the raw aiohttp exception bubble
    up as an opaque "unknown error occurred".

    NEW (V4/Prime prep, following the same fix in auth.py). This
    class's own _request() is the single chokepoint nearly every
    endpoint method in this file goes through, so wrapping it there
    covers p2maps, favorites, schedules, DND, mission history, and map
    editing all at once; download_map_bundle() is the one call that
    deliberately bypasses _request() (a different, unsigned host, see
    its own docstring) and is wrapped separately below for the same
    reason.

    SINCE 0.4.0 THE SAME DIAGNOSIS AS LOGIN (auth._ssl_diagnosis). Until
    then this said "almost always a temporary problem on iRobot's
    servers ... not something wrong with your setup" for every
    certificate failure -- wrong for a machine without a usable trust
    store, where waiting fixes nothing. Login had stopped saying it
    after a field report; REST had not."""
    reason, message = _ssl_diagnosis(exc)
    raise RestSSLError(message, reason=reason) from exc


def _raise_clear_connection_error(exc: aiohttp.ClientConnectorError) -> NoReturn:
    """Re-raise a connection failure (DNS, connection refused, network
    unreachable) as a clear RestConnectionError. See auth.py's
    equivalent for why this deliberately doesn't claim confident fault
    attribution the way _raise_clear_ssl_error() does."""
    raise RestConnectionError(
        "Could not connect to iRobot's cloud servers. This could be a "
        "temporary problem with iRobot's servers, or with your own "
        "internet connection -- check that other internet-dependent "
        "services are working, and try again in a few minutes."
    ) from exc


def _raise_clear_timeout_error(exc: BaseException) -> NoReturn:
    """Re-raise a request timeout as a clear RestTimeoutError. Accepts
    BaseException -- see auth.py's equivalent for why."""
    raise RestTimeoutError(
        "iRobot's cloud servers took too long to respond. This is "
        "usually temporary -- please try again in a few minutes."
    ) from exc


def _raise_transport_error(exc: BaseException) -> NoReturn:
    """Maps ANY failure of a REST request to its RestError -- the REST
    twin of auth._raise_transport_error(), and there for the same
    measured reason: a dropped connection (ServerDisconnectedError) and
    a timeout (bare TimeoutError) used to escape as raw aiohttp/asyncio,
    past every `except RestError` and `except CloudError`.

    Order matters: ClientSSLError is a ClientConnectorError, and
    aiohttp.ServerTimeoutError is a TimeoutError."""
    if isinstance(exc, aiohttp.ClientSSLError):
        _raise_clear_ssl_error(exc)
    if isinstance(exc, TimeoutError):
        _raise_clear_timeout_error(exc)
    if isinstance(exc, aiohttp.ClientConnectorError):
        _raise_clear_connection_error(exc)
    if isinstance(exc, aiohttp.ClientResponseError):
        # The server answered; the answer was the problem.
        message = f"iRobot's cloud answered unexpectedly: {exc.status} {exc.message}"
        if exc.status >= 400:
            raise _http_error(exc.status, message, headers=exc.headers) from exc
        raise RestError(message, status=exc.status) from exc
    raise RestConnectionError(
        "The connection to iRobot's cloud broke off mid-request "
        f"({type(exc).__name__}). This is usually temporary -- please try "
        "again in a few minutes.",
        reason=CloudErrorReason.CONNECTION_BROKEN,
    ) from exc


def _either(data: dict[str, Any], *names: str) -> Any:
    """The first of several spellings that is present.

    THE MODEL'S OWN `to_json` DISAGREES WITH THE FAVOURITE PARSER. It
    writes `default`, `deleted`, `hidden` and `commanddefs`; the parser
    read `favorite_id`, `display_order` and `modification_secs`. Both
    were written from the app's source, and the parser's own docstring
    admits nobody had seen a real response.

    A favourite whose id does not parse is dropped by the caller -- so a
    mismatch here does not produce an error, it produces an account with
    no favourites. @chairstacker has seven and saw no buttons.

    Accepting both spellings costs one lookup and cannot be wrong in the
    way a choice between them can.
    """
    for name in names:
        if name in data:
            return data[name]
    # CASE-INSENSITIVE SECOND PASS, because the first one missed the
    # spelling that matters most.
    #
    # This helper was written to accept `favoriteid` beside
    # `favorite_id`. The vendor's own favourite model carries
    # `favoriteId` -- capital I -- alongside the snake form, and an
    # exact-match loop over lowercase candidates never sees it.
    #
    # A favourite whose id does not parse is dropped by the caller
    # without an error, so one capital letter is the difference between
    # seven favourites and an account that looks empty.
    lowered = {k.lower(): v for k, v in data.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


class CloudRestClient:
    """The signed REST surface of iRobot's cloud, shared by both robot
    generations -- the base of PrimeRestClient and ClassicRestClient.

    NEW IN 0.4.0, and only a new name for what was already here: the
    SigV4 signing, the request/response handling and the one-shot
    relogin on HTTP 403 moved out of PrimeRestClient unchanged, together
    with the three calls both generations make identically --
    get_robot_parts(), get_favorites_raw() and get_automations_raw().

    Takes an existing aiohttp.ClientSession (same one used for
    auth.login(), so cookies/connection pooling are shared) rather than
    owning its own.

    credentials: AWS Cognito credentials (see auth.CloudCredentials) --
    every request is SigV4-signed with these.

    relogin: optional async callback that's called exactly once on an
    HTTP 403, to fetch new credentials and retry the call. None
    (default) -- no automatic retry, a 403 is passed through as a
    RestError.

    request_timeout: seconds one request may take, sending to reading
    the whole answer (auth.DEFAULT_REQUEST_TIMEOUT, 30 s, unless given;
    None for no limit). A 403 relogin runs outside it, and the retried
    request gets a fresh one."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        http_base_auth: str,
        credentials: CloudCredentials,
        relogin: Relogin | None = None,
        *,
        request_timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self._http_base_auth = http_base_auth.rstrip("/")
        self._credentials = credentials
        self._relogin = relogin
        self._request_timeout = request_timeout

    async def get_robot_parts(self, blid: str) -> RobotPartsInfo:
        """GET /v1/robots/{blid}/parts -- NEW (session 15). CONFIRMED
        from the actual APK configuration file
        (res/raw/base_roomba_config.json, commandId "GetRobotParts":
        httpMethod=GET, urlPath="/v1/robots/%s/parts",
        networkList=["awsApiGateway"]) -- a primary source, not
        bytecode interpretation.

        Response shape confirmed (session 27, real live response from
        chairstacker): robot_id, num_parts, parts (list with part_id,
        counter, minutes_remaining, count_type e.g.
        "combo_missions"/"pad_washes_used"/"minutes"/"evacs",
        count_remaining, count_used, counter_category, reset_by).

        CORRECTED (session 53): actually parsed into RobotPartsInfo
        now, rather than returning raw JSON with a docstring pointing
        at a parser that was never called -- a genuine architectural
        gap found during a broader review, not new field-level
        information."""
        return RobotPartsInfo.from_json(await self.get_robot_parts_raw(blid))

    async def get_robot_parts_raw(self, blid: str) -> Any:
        """GET /v1/robots/{blid}/parts, unparsed -- the object the server
        sent, every key of it. NEW (0.4.0) for callers that keep the
        answer as it came, as ha_roomba_plus has for Classic robots:
        RobotPartsInfo carries the fields known today and drops any
        other, and converting back would lose them."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/parts"
        return await self._request("GET", url)

    async def get_parts_catalog(
        self, sku: str, language: str = "en-US", country: str = "US"
    ) -> PartsCatalog:
        """The parts catalogue for a model: names, care guides, shop
        links, joined to get_robot_parts()'s counters on `part_id`.
        NEW in 0.6.0; see get_parts_catalog_raw() for the request."""
        return PartsCatalog.from_json(
            await self.get_parts_catalog_raw(sku, language, country)
        )

    async def get_parts_catalog_raw(
        self, sku: str, language: str = "en-US", country: str = "US"
    ) -> Any:
        """GET content-prod.iot.irobotapi.com/v2/{language}/{country}/{sku}/parts,
        unparsed. NEW in 0.6.0.

        UNAUTHENTICATED, AND THEREFORE NOT SIGNED. The app's
        `AssetPartsRequest` goes to `ContentStackHost`, which implements
        `UnauthenticatedAPIHost`, with a Content-Type header and nothing
        else. The catalogue has answered that request live for six
        models; a SigV4 signature for another host would at best be
        ignored, so none is sent -- the same reasoning as
        download_map_bundle().

        ON BOTH CLIENTS. The Classic app's native code carries the same
        `%s/%s/%s/parts` template, and the six models that answered
        include Classic ones.

        `language` and `country` are the app's form, `de-DE` and `DE`.
        `sku` is the robot's own (`RobotSerialInfo.sku`): part ids are
        numbered per model, so a catalogue fetched for another model
        joins to the wrong parts. The catalogue does not change between
        refreshes; fetch it once per robot."""
        url = (
            f"{_CONTENT_HOST}/v2/{_path_segment(language)}/{_path_segment(country)}"
            f"/{_path_segment(sku)}/parts"
        )
        try:
            async with asyncio.timeout(self._request_timeout):
                async with self._session.get(
                    url, headers={"Content-Type": "application/json"}
                ) as resp:
                    return await self._parse_response(resp)
        except (aiohttp.ClientError, TimeoutError) as exc:
            _raise_transport_error(exc)

    async def get_favorites_raw(
        self, app_edition: str | None = "1"
    ) -> list[dict[str, Any]]:
        """Same endpoint as get_favorites(), but returns the UNPARSED
        response. Added (this session) for a round-trip fidelity check:
        if a stored favorite carries fields our own models don't know
        about, parsing and re-serializing it would silently DROP them,
        and we would resend a command that is subtly less complete than
        what the app itself sends -- a failure mode that looks exactly
        like this project's central symptom (structurally valid,
        no effect, no error). Diagnostic use only; nothing in the
        library's normal path should need this."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        query = {"app_edition": app_edition} if app_edition else {}
        data = await self._request("GET", url, query=query)
        # THE DIAGNOSTIC HAD THE BUG IT EXISTS TO DIAGNOSE.
        #
        # This returned `data if isinstance(data, list) else []`, so a
        # wrapped `{"favorites": [...]}` response captured as an empty
        # list -- the same shape that made get_favorites() report an
        # empty account, in the one place built to reveal it.
        #
        # A diagnostics download taken to answer "does the server return
        # anything?" therefore answered "no" whether or not it did.
        return self._unwrap_favorites_payload(data)

    @staticmethod
    def _unwrap_favorites_payload(data: Any) -> list[dict[str, Any]]:
        """The favourites list out of whatever wraps it.

        THE OUTER KEYS ARE THE FINDING when there is no list, so an
        object with no `favorites` key is handed back whole rather than
        discarded -- that is precisely the case a download is taken to
        investigate."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            wrapped = data.get("favorites")
            if isinstance(wrapped, list):
                return wrapped
            return [data]
        return []

    async def get_automations_raw(self) -> Any:
        """GET /v1/user/automations -- third-party triggers and
        geofencing. NOT the schedule endpoint.

        WHAT THIS SUBSYSTEM IS (APK, 2 August 2026). RoutineConstants
        holds 66 entries, and they settle the purpose: hard-coded
        service ids for August Home, Ecobee, Leviton, MyQ and Wyze;
        geofencing keys (kLatitude, kLongitude, kRadius,
        kEnterRegionLocationTriggerId); and behaviour options
        (kContinueCleaning, kPauseAndNotify, kEndJob). So: "when I leave
        the house, run favourite X", or "when Ecobee goes to Away".

        `favorite_id` and `robot_commands` in the payload show it drives
        the same command structures as favourites do.

        A DIFFERENT DATA MODEL FROM SCHEDULES, not an alternative to
        them -- automation_id vs schedule_id, time_window{hour,minute}
        vs start/end, plus service_id/trigger_id, which schedules have
        no counterpart for. Schedule management stays on
        /v1/households/{id}/settings/schedule, the only path active in
        the app's Kotlin.

        THE ENDPOINT MAY NOT BE LIVE, and that is the open question this
        method exists to settle. In liblegacyCore.so the string has
        exactly ONE reference -- a static initialiser, recognisable by
        the __cxa_atexit pattern, sitting between two IFTTT URLs. No
        reader. Same signature as `sec_message` and `koz-v1.0.0`, both
        of which turned out to be dead; actively used URLs show two or
        three real consumers.

        The app reaches the data through
        AutomationDataUseCaseImpl::fetchAllAutomations() and on across
        the Djinni boundary, so its real path is not resolvable
        statically. A second Home Assistant integration
        (a-mavrides/roomba_v4) calls this URL -- but reading its code,
        that is NOT evidence the endpoint answers. It swallows any
        exception into a debug log, defaults the result to {}, writes it
        to a debug file, and no entity ever reads it. A 404 would look
        identical to an empty response there, and nobody would notice.

        So there is currently NO evidence for or against, only an
        untested call in someone else's integration and a dead constant
        here.

        One read attempt on an account with automations configured
        settles it either way, and costs nothing if the answer is no.
        """
        url = f"{self._http_base_auth}/v1/user/automations"
        return await self._request("GET", url)

    def _signer(self) -> AwsSigV4Signer:
        return AwsSigV4Signer(
            self._credentials.access_key_id,
            self._credentials.secret_key,
            self._credentials.session_token,
        )

    async def _request(
        self,
        method: str,
        url: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        _retry: bool = True,
        compact_body: bool = False,
    ) -> Any:
        parsed = urllib.parse.urlparse(url)
        # compact_body: the Classic calls send JSON without spaces, as
        # the integration did. Every Prime call keeps json.dumps()'s
        # default -- those bytes are what the Prime field tests sent.
        separators = (",", ":") if compact_body else None
        body_str = json.dumps(body, separators=separators) if body is not None else ""
        headers = self._signer().signed_headers(
            method=method,
            service="execute-api",
            region=self._credentials.region,
            host=parsed.netloc,
            path=parsed.path,
            query_params=query or {},
            body=body_str,
        )

        request_kwargs: dict[str, Any] = {"params": query, "headers": headers}
        if body is not None:
            # NOTE: must send the EXACT same bytes we hashed for the
            # signature -- aiohttp's json= would re-serialize
            # independently (possibly different key order/whitespace)
            # and invalidate the signature. data= sends our own string
            # verbatim.
            request_kwargs["data"] = body_str.encode()

        method_fn = getattr(self._session, method.lower())
        try:
            async with asyncio.timeout(self._request_timeout):
                async with method_fn(url, **request_kwargs) as resp:
                    if not (resp.status == 403 and _retry and self._relogin is not None):
                        return await self._parse_response(resp)
        except (aiohttp.ClientError, TimeoutError) as exc:
            _raise_transport_error(exc)

        # Only a 403 with a relogin gets here -- AFTER the timeout block,
        # so a slow login does not eat the request's time, and the retry
        # below gets a timeout of its own.
        _LOGGER.debug("roombapy-prime REST: 403 -- reauthenticating")
        assert self._relogin is not None
        login_result = await self._relogin()
        self._credentials = login_result.credentials
        return await self._request(
            method, url, query, body, _retry=False, compact_body=compact_body
        )

    async def _request_object(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """_request() for endpoints documented to answer with an object.

        THE TYPE IS A PROMISE, NOT A CHECK. The answer is returned
        unchanged, whatever it is, exactly as these methods always did;
        this only states in one place what their `-> dict[str, Any]`
        annotations claim, so `mypy --strict` can hold the rest of the
        library to it. A method that has to rely on the shape checks it
        itself (see get_pmap_umf()). The arguments pass through exactly as
        given, so every call reaches _request() in the form it always had."""
        return cast("dict[str, Any]", await self._request(method, url, **kwargs))

    async def _parse_response(self, resp: aiohttp.ClientResponse) -> Any:
        text = await resp.text()
        if resp.status >= 400:
            raise _http_error(
                resp.status, f"HTTP {resp.status} from {resp.url}", text,
                # getattr: a real response always has headers; the check
                # keeps minimal test doubles working.
                getattr(resp, "headers", None),
            )
        if not text:
            return {}
        try:
            return json.loads(text)
        except JSONDecodeError as exc:
            raise RestError(
                f"Non-JSON response from {resp.url}: {text[:300]}", status=resp.status, raw_response=text
            ) from exc


class PrimeRestClient(CloudRestClient):
    """The Prime/V4 robots' REST surface: p2maps, favorites with their
    command definitions, mission history as the Prime app asks for it,
    households, schedules, DND, time estimates and the rest.

    Built on CloudRestClient since 0.4.0; constructor, methods and
    import path are unchanged. Everything here is either Prime-only or
    unmeasured on Classic robots -- see ClassicRestClient for what
    Classic robots are confirmed to answer."""

    async def get_map_metadata(self, p2map_id: str) -> P2MapData:
        """GET /v1/p2maps/{p2mapId}. CORRECTED (session 51): response
        shape is now confirmed via P2MapData$$serializer -- p2map_id,
        active_p2mapv_id, create_time, last_p2mapv_ts, state, visible,
        name, user_orientation_rad. Previously returned raw JSON
        ("response shape not modeled yet") -- now parsed into
        P2MapData directly."""
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}"
        data = await self._request("GET", url)
        return P2MapData.from_json(data)

    async def get_active_map_versions(self, blid: str) -> list[dict[str, Any]]:
        """GET /v1/p2maps?robotId={blid}&visible=true -- NEW (July 11),
        confirmed from P2MapAPIFetching$fetchActiveVersions$2 (this
        inner coroutine class decompiled cleanly, unlike the three
        fetchPersistentMap/fetchLatestPersistentMap/fetchMissionMap
        equivalents, see PRIME_APP_GAP_ANALYSIS point C2).

        CORRECTED (session 25): the original assumption "at least
        'mapId' and 'mapVersionId'" was WRONG -- a real live response
        (chairstacker) shows the actual fields: `p2map_id`,
        `entity_type`, `create_time`, `robot_id`, `sku`,
        `active_p2mapv_id` (that's the map version ID),
        `last_p2mapv_ts`, `state`, `visible`, `name`, `rooms_metadata`.
        Still passed through here as raw JSON -- for a typed result
        use models/robot_info.py::parse_active_map_versions() (NEW, session 26,
        includes room metadata with reusable CommandParams presets per
        operating mode)."""
        url = f"{self._http_base_auth}/v1/p2maps"
        data = await self._request("GET", url, query={"robotId": blid, "visible": "true"})
        return data if isinstance(data, list) else []

    async def get_map_version(self, map_id: str, map_version: str) -> dict[str, Any]:
        """The map version document, which carries region names inline.

        `GET /v1/p2maps/{id}/versions/{vid}` -- no suffix. Distinct from
        `/geojson` (a presigned link to the bundle) and `/raw`.

        THIS IS WHERE REGION NAMES LIVE, and it took four rounds with
        @chairstacker to find because we were looking at two other
        places:

            rooms_metadata          rooms only, zones always None
            bundle cleanZones       empty on his robot
            geojson_details.regions **names for rooms AND zones**

        Shape, from samm-git/irobot-explore's independent
        reconstruction of the same protocol:

            {"geojson_details": {"regions": [
              {"id": "10", "name": "Living room", "region_type": "livingRoom"},
              {"id": "11", "name": "Kitchen", "region_type": "kitchen"}
            ]}}

        Note `region_type` here is a ROOM CATEGORY (`kitchen`,
        `livingRoom`), not the `rid`/`zid` distinction that
        `Region.region_type` carries. Two different things under one
        name.

        CONFIRMED WORKING ON A REAL ROBOT (August 2026). The author of
        samm-git/irobot-explore ran their `rooms` command against
        firmware `p25-705+9.3.6+I3.8.149` and got room names back --
        rid 10 through 16, "Living room", "Kitchen", "Entryway" and so
        on. So the endpoint is live, whatever the app does or does not
        call.

        Their read is that this lives in ARM64 native blocks in the app,
        which would explain the searches below coming back empty: a
        DEX-and-Dart search does not reach into compiled ARM64.

        WHAT THAT DOES NOT SETTLE: their output is ROOMS. Whether the
        same document carries ZONES is still open -- and on the one
        robot where zone names were actually chased down, they came
        from the bundle's `cleanZones` layer instead. Two sources, and
        no reason yet to think either is the only one.

        `geojson_details` EXISTS IN NO APP VERSION WE HAVE CHECKED,
        which now reads as a limit of the search rather than of the
        protocol.
        Zero hits across 3.0.0 (2 DEX + Dart snapshot) and 2.2.4 (8 DEX
        + 21 native libraries), including camelCase and snake_case
        spellings. So the shape below is samm-git's alone, and may be a
        1.6.0-only response or a reading of one.

        THE SUFFIX-LESS PATH DOES EXIST, in 2.2.4, under the OLD
        naming:

            /v1/{assetId}/pmaps/{mapId}/versions/{versionId}

        Note `pmaps`, not `p2maps`, and an `assetId` prefix. That whole
        generation is gone from 3.0.0 -- zero `pmaps` paths, zero
        `active_details`. Only `p2maps` survived, so the URL this
        method builds is a 3.0.0-shaped guess at a 2.2.4-shaped
        endpoint.

        AND 2.2.4 LOOKED UP REGION NAMES ONE AT A TIME:

            P2MapInfoProvider.fetchRegionName(
                assetId, mapId, mapVersion, regionId, regionType)

            rid  = room     zid = zone     furniture_id = furniture

        It calls `fetchMapMetadata` -- the same source we already read.
        Which suggests region names were never in a separate document
        at all: the app resolved them per region from metadata it had.

        APP 3.0.0 DOES NOT CALL THIS. Checked against both DEX files:
        no suffix-less `/versions/{vid}` request anywhere. The endpoint
        comes from samm-git/irobot-explore's reconstruction of app
        **1.6.0**.

        Kept anyway, because "not in the app" is not "does not exist" --
        a server routinely serves what a newer client stopped calling,
        and this project has been wrong in that direction before
        (`libapp.so`, unreadable twice then readable). A caller who
        tries it either gets regions or gets a 404, and both are
        answers.

        `parse_map_version_regions()` tolerates a missing key rather
        than assuming one.

        AND THE FIELD NAME COLLIDES. `region_type` in the 1.6.0 shape
        is documented as a room CATEGORY (`kitchen`, `livingRoom`). In
        3.0.0 `region_type` is the `rid`/`zid`/`tid` distinction, and
        the categories live in `p2_map_room_info.value` as
        `IrobotP2MapRoomTypeValue`. Two different things under one name
        across two app versions -- read whichever you get carefully.
        """
        url = (
            f"{self._http_base_auth}/v1/p2maps/{_path_segment(map_id)}"
            f"/versions/{_path_segment(map_version)}"
        )
        return dict(await self._request("GET", url))

    async def get_map_geojson_link(self, map_id: str, map_version: str) -> dict[str, Any]:
        """NEW (July 11, third session -- after renewed, targeted
        searching). Finally resolves how fetchPersistentMap/
        fetchLatestPersistentMap/fetchMissionMap get their tar.gz map
        bundle (see PRIME_APP_GAP_ANALYSIS point C2, previously marked
        as "not economically resolvable further" -- that was given up
        too early, a broader source-code search for "/versions/" found
        P2MapGeoJSONRequest.java directly):

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#rest_clientget_map_geojson_link
    """
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(map_id)}/versions/{_path_segment(map_version)}/geojson"
        return await self._request_object("GET", url, query={"response_type": "link"})

    async def get_map_raw_link(
        self, map_id: str, map_version: str, response_type: str | None = "link"
    ) -> dict[str, Any]:
        """The same map version in the vendor's RAW format rather than
        GeoJSON.

        `P2MapRawDataRequest` (app 3.0.0) is `get_map_geojson_link()`'s
        sibling: identical URL shape with `/raw` where the other has
        `/geojson`, and the same `response_type` query parameter and
        `DesiredResponseType` enum behind it.

        FOUND BY DIFFING THE APP'S SERVICE CHANNELS against this client,
        not by reading. `_MapFetcherServiceChannel` lists
        `fetchMapRawData` beside `fetchMapGeoJson`, and only one of the
        two had ever been implemented -- the GeoJSON one, because that
        is the one a bundle search turned up.

        WHAT IS IN IT IS NOT KNOWN. Nobody here has fetched a raw map,
        and the format is the vendor's own rather than GeoJSON, so the
        contents are unmodelled and the response is passed through. It
        is offered because the endpoint exists and the caller who needs
        it should not have to rediscover the URL.

        WHY IT MIGHT MATTER: the GeoJSON bundle is a rendering of the
        map, and this project has repeatedly found fields present in the
        SDK's own map model that the bundle does not carry -- the zone
        detection flags, `p2_map_room_info.value`. If any of those are
        reachable at all, this is the endpoint that would carry them."""
        url = (
            f"{self._http_base_auth}/v1/p2maps/{_path_segment(map_id)}"
            f"/versions/{_path_segment(map_version)}/raw"
        )
        query = {"response_type": response_type} if response_type else {}
        return await self._request_object("GET", url, query=query)

    async def download_map_bundle(self, url: str) -> bytes:
        """NEW (July 11, fifth session). Downloads the raw tar.gz map
        bundle from a PRESIGNED URL (see get_map_geojson_link()).

        DELIBERATELY WITHOUT SigV4 signing -- confirmed from P2MapAPI.
        MapUnpacker.fetchMapBundleContentHolder(P2MapIdentifier, URL):
        the app opens the presigned URL directly
        (`mapURL.openConnection()`), with no auth header of its own.
        Presigned URLs (S3-style) typically carry their
        authentication in their own query parameters -- additional
        signing wouldn't just be unnecessary, it would overwrite/
        corrupt the signature the server expects.

        Returns the raw bytes (tar.gz archive) -- for unpacking and
        parsing see models/map_bundle.py::parse_map_bundle(). Separate from
        _request(), since this URL doesn't live under
        self._http_base_auth (typically an S3 bucket or similar CDN
        host) and therefore shouldn't go through this class's SigV4
        signing scheme."""
        try:
            async with asyncio.timeout(self._request_timeout):
                async with self._session.get(url) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise _http_error(
                            resp.status,
                            f"HTTP {resp.status} downloading map bundle from {url}",
                            text,
                            getattr(resp, "headers", None),
                        )
                    body: bytes = await resp.read()
                    return body
        except (aiohttp.ClientError, TimeoutError) as exc:
            _raise_transport_error(exc)

    async def set_map_name(self, p2map_id: str, name: str) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/settings, body {"name": ...}.

        CORRECTED (session 51): confirmed directly via
        EditMapSettingsRequest$Command$SetName$$serializer's
        <clinit> -- real field name is `name`, NOT `type` as
        previously implemented. This was a genuine bug: sending
        `{"type": name}` would very likely have been silently ignored
        or rejected by the real server, not just an unconfirmed
        guess that happened to work."""
        return await self._post_settings(p2map_id, {"name": name})

    async def set_map_orientation(self, p2map_id: str, orientation_rad: float) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/settings, body {"user_orientation_rad": ...}.

        Original clamps the angle into (-pi, pi] before sending (see
        EditMapSettingsRequest$Command$SetUserPreferredOrientation$Companion
        .clampRadians in FINDINGS) -- replicated here rather than trusting
        the caller to have already done it.
        """
        two_pi = 6.283185307179586
        pi = 3.141592653589793
        clamped = orientation_rad - (math.ceil((orientation_rad + pi) / two_pi) - 1) * 2 * pi
        return await self._post_settings(p2map_id, {"user_orientation_rad": clamped})

    async def delete_map(self, p2map_id: str) -> dict[str, Any]:
        """NEW (July 11, third session) -- confirmed from
        DeleteMapRequest.java: despite the name, NOT an HTTP DELETE,
        but a "soft delete" via the same settings endpoint as
        set_map_name()/set_map_orientation():

            POST /v1/p2maps/{p2mapId}/settings?trigger_fast_updates=true
            Body: {"visible": false}

        Field name "visible" -- CONFIRMED (session 50, re-verification
        pass) directly via DeleteMapRequest$Body$$serializer's
        <clinit>: this is the class's only field, confirmed the same
        way as the rest of this project's `$$serializer` findings, not
        merely "found without a @SerialName, presumably" as this
        docstring said before that specific check."""
        return await self._post_settings(p2map_id, {"visible": False})

    async def _post_settings(self, p2map_id: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}/settings"
        return await self._request_object("POST", url, query={"trigger_fast_updates": "true"}, body=body)

    async def edit_map_v2(self, p2map_id: str, command: MapEditCommand) -> dict[str, Any]:
        """POST /v2/p2maps/{p2mapId}/versions -- NOTE (July 11, fourth
        session): after a full re-decompilation of the app, confirmed
        that requestEditV2() is NEVER called anywhere in the ENTIRE app
        code. This path presumably still exists server-side (the
        endpoint itself isn't made up), but isn't used anywhere by the
        current app version (2.2.4). edit_map() (V1) is the actually
        active path -- see there. Kept available here, renamed from
        edit_map() to edit_map_v2(), so the name no longer suggests
        this is the standard way.

        Response shape (the updated P2PersistentMap, per the Kotlin
        repository interfaces) not modeled -- raw JSON."""
        url = f"{self._http_base_auth}/v2/p2maps/{_path_segment(p2map_id)}/versions"
        return await self._request_object("POST", url, body=command.to_command_body())

    async def edit_map(
        self, p2map_id: str, command: MapEditCommandV1,
        response_type: str | None = "link",
    ) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/versions -- NEW (July 11, fourth
        session), the ACTUALLY ACTIVE edit path (see models/map_editing.py's V1
        section and PRIME_APP_GAP_ANALYSIS): every single edit
        operation in the app code calls requestEditV1(), never
        requestEditV2(). Replaces the previous default assumption
        (edit_map() = V2) -- the old path is now separately available
        under edit_map_v2(), with a warning that it's unused code.

        CORRECTED (session 48): the request body envelope is now
        confirmed via EditMapV1Request$Body$$serializer --
        {"edit_cmd": {...the command's own fields...}, "response_type":
        "..."}, not the previously-assumed flat structure. See
        MapEditCommandV1's module docstring in models/map_editing.py for the full
        story, including which parts remain unconfirmed (the
        discriminator inside "edit_cmd", and SetRoomMetadata/
        VirtualWall's own custom serializers). `response_type`'s
        correct value for an EDIT (as opposed to the already-confirmed
        "link"/"binary" values for FETCHING a map) is not confirmed --
        left as a simple default here, honestly not verified.

        Response shape not modeled -- raw JSON.

        RESPONSE_TYPE IS NOW A PARAMETER (this session), because it is
        the least-verified part of this request and a real edit keeps
        failing with HTTP 500.

        Two field runs (DaRealGuGu) resent two untouched zones and got
        a 500 both times -- once with a payload that had a genuine
        extra point, and again after that was fixed. So the extra point
        was a real deviation from the documented format but not the
        cause.

        "link" asks the server for a presigned download URL. That is
        the confirmed value for FETCHING a map; for an EDIT it may well
        be meaningless or actively wrong, which would fit a 500 (the
        body parses, then something downstream cannot honour it).
        Passing None omits the key entirely.

        This is deliberately a parameter rather than a changed default:
        nothing has confirmed what the right value is, and quietly
        swapping one unverified guess for another would leave us
        exactly as uninformed."""
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}/versions"
        body: dict[str, Any] = {"edit_cmd": command.to_v1_command_body()}
        if response_type is not None:
            body["response_type"] = response_type
        return await self._request_object("POST", url, body=body)

    async def get_live_map_stream(self, blid: str) -> LiveMapStreamInit:
        """GET /v1/p2maps/livemap?robotId={blid} -> the MQTT topic to
        subscribe on the already-open AWS IoT connection (see
        mqtt_client.py's docstring and FINDINGS section 2)."""
        url = f"{self._http_base_auth}/v1/p2maps/livemap"
        data = await self._request("GET", url, query={"robotId": blid})
        return LiveMapStreamInit.from_json(data)

    # --- Favorites (FavoriteV1) -------------------------------------------
    #
    # NEW (July 11, fourth session). Base URL and app_edition query param
    # confirmed from FavoriteCommonRequest.java, see models/favorites.py's
    # favorites section for the full derivation including which
    # HTTP methods are confirmed vs. assumed.

    #: `app_edition=1` SELECTS WHICH FAVOURITES THE SERVER RETURNS, and
    #: that is the prime suspect for favourites going missing.
    #:
    #: The parameter comes from 2.2.4's `FetchFavoriteRequest`. It does
    #: not appear anywhere in the 3.0.0 analysis -- neither in the
    #: request classes nor in the wire keys -- so either the newer app
    #: omits it or it uses a different value.
    #:
    #: @chairstacker's two favourites appear as buttons on Roomba+
    #: v3.5.1, which reads them from the CLASSIC cloud coordinator, and
    #: not on the alpha, which calls this. His robot is on the Prime app
    #: 3.0. A server segmenting favourites by app edition would produce
    #: exactly that: the same account, two answers.
    #:
    #: UNPROVEN. Nothing here changes it, because a wrong edition value
    #: could hide favourites just as effectively -- and one field report
    #: is not enough to pick a new constant. What would settle it is a
    #: single call without the parameter, or with `2`, on an account
    #: whose favourites are known to exist.
    _FAVORITES_QUERY = {"app_edition": "1"}

    async def get_favorites(
        self, app_edition: str | None = "1"
    ) -> list[FavoriteV1]:
        """GET /v1/user/favorites?app_edition=1 -- CONFIRMED (FetchFavoriteRequest,
        httpMethod = "GET")."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        query = {"app_edition": app_edition} if app_edition else {}
        data = await self._request("GET", url, query=query)
        # A NON-LIST RESPONSE RETURNS NOTHING, SILENTLY, and that is
        # indistinguishable from an account with no favourites.
        #
        # @chairstacker has two favourites that appear as buttons on
        # v3.5.1 and not on the alpha. Everything between this call and
        # the entities is wired correctly, so if the buttons are missing
        # the list is empty -- and this line is the one place an empty
        # list can be manufactured from a perfectly good response.
        #
        # Logging it does not fix anything. It replaces "no favourites"
        # with "the server sent a dict where a list was expected", which
        # is the difference between a shrug and a next step.
        # THE RESPONSE IS SOMETIMES AN OBJECT WRAPPING THE LIST.
        #
        # Roomba+ v3.5.1 calls this same endpoint through its Classic
        # cloud client, which does:
        #
        #     result if isinstance(result, list) else result.get("favorites", [])
        #
        # This returned `[]` instead. So the same account, on the same
        # endpoint, produced two favourites there and none here -- which
        # is exactly what @chairstacker saw when he downgraded by
        # accident and found his buttons.
        #
        # That unwrap has been in the Classic path since it was written,
        # so the wrapped shape is not a new server behaviour. This side
        # simply never handled it.
        if isinstance(data, dict):
            raw_list = data.get("favorites") or []
            if not raw_list:
                _LOGGER.debug(
                    "roombapy-prime: favourites response was an object with "
                    "no 'favorites' key -- keys were %s",
                    sorted(data),
                )
        elif isinstance(data, list):
            raw_list = data
        else:
            _LOGGER.warning(
                "roombapy-prime: /v1/user/favorites returned %s -- neither a "
                "list nor an object, treating as no favourites",
                type(data).__name__,
            )
            raw_list = []
        # ONE UNPARSEABLE FAVOURITE USED TO COST ALL OF THEM.
        #
        # `_favorite_from_json` builds each command with
        # `MissionCommandType(c["command"])` -- a hard constructor that
        # raises ValueError on any value this enum does not know, and
        # KeyError if a command def has no `command` key at all. Either
        # exception escaped the whole list comprehension.
        #
        # The caller in ha_roomba_plus catches Exception, logs at DEBUG
        # and returns []. So a single stored favourite carrying an
        # unfamiliar command produced an account that looked empty, with
        # nothing above DEBUG to say otherwise. @chairstacker has seven
        # and saw none.
        #
        # Now each is parsed on its own and a failure costs exactly that
        # one, at WARNING with the id and the reason.
        parsed: list[FavoriteV1] = []
        for item in raw_list:
            try:
                parsed.append(self._favorite_from_json(item))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "roombapy-prime: skipping a favourite that failed to "
                    "parse (id=%s): %s",
                    (item or {}).get("favorite_id") if isinstance(item, dict) else None,
                    err,
                )
        if raw_list and not parsed:
            _LOGGER.warning(
                "roombapy-prime: the server returned %d favourite(s) and none "
                "could be parsed -- this looks like an empty account and is not",
                len(raw_list),
            )
        return parsed

    async def create_favorite(self, favorite: FavoriteV1) -> dict[str, Any]:
        """POST /v1/user/favorites?app_edition=1 -- CONFIRMED (eighth
        session: CreateFavoriteRequest.<init> sets httpMethod = "POST"
        directly, androguard bytecode inspection -- previously only
        assumed, since jadx had silently skipped the lambda class for
        this). Response is a FavoriteIdResponse -- CORRECTED (session
        48): its one field's real key is confirmed via
        FavoriteIdResponse$$serializer's <clinit> to be `favorite_id`,
        not a guessed/heuristically-detected key. Passed through here
        as raw JSON (not worth a dedicated dataclass for one field),
        but callers can now reliably do `result["favorite_id"]`."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        return await self._request_object(
            "POST", url, query=self._FAVORITES_QUERY, body=favorite.to_json()
        )

    async def update_favorite(self, favorite_id: str, favorite: FavoriteV1) -> dict[str, Any]:
        """PUT /v1/user/favorites/{favoriteId}?app_edition=1 --
        CONFIRMED (eighth session: UpdateFavoriteRequest.<init> sets
        httpMethod = "PUT" directly -- previously only assumed)."""
        url = f"{self._http_base_auth}/v1/user/favorites/{_path_segment(favorite_id)}"
        return await self._request_object(
            "PUT", url, query=self._FAVORITES_QUERY, body=favorite.to_json()
        )

    async def delete_favorite(self, favorite_id: str) -> dict[str, Any]:
        """DELETE /v1/user/favorites/{favoriteId}?app_edition=1 --
        CONFIRMED (DeleteFavoriteRequest, httpMethod = "DELETE")."""
        url = f"{self._http_base_auth}/v1/user/favorites/{_path_segment(favorite_id)}"
        return await self._request_object("DELETE", url, query=self._FAVORITES_QUERY)

    async def order_favorite(
        self,
        favorite_id: str,
        *,
        insert_at: int | None = None,
        insert_before: str | None = None,
        insert_after: str | None = None,
    ) -> dict[str, Any]:
        """PUT /v1/user/favorites/{favoriteId}/order?app_edition=1 --
        CONFIRMED (OrderFavoriteRequest, httpMethod = "PUT"). CORRECTED:
        insert_at/insert_before/insert_after are QUERY PARAMETERS
        (snake_case: insert_at/insert_before/insert_after), not body
        fields -- bytecode-confirmed from OrderFavoriteRequest.
        getQueryParams() (via androguard/jadx: r0.put("insert_at", ...),
        r0.put("insert_before", ...), r0.put("insert_after", ...)). No
        httpBody found for this request. Presumably exactly one of the
        three is expected -- which combination(s) the server actually
        accepts is not confirmed."""
        url = f"{self._http_base_auth}/v1/user/favorites/{_path_segment(favorite_id)}/order"
        query = dict(self._FAVORITES_QUERY)
        if insert_at is not None:
            query["insert_at"] = str(insert_at)
        if insert_before is not None:
            query["insert_before"] = insert_before
        if insert_after is not None:
            query["insert_after"] = insert_after
        return await self._request_object("PUT", url, query=query)

    async def get_mission_history(
        self,
        blid: str,
        *,
        max_reports: int | None = None,
        max_age: int | None = None,
        filter_type: str | None = None,
        exclusive_start_timestamp: int | None = None,
        supported_done_codes: list[str] | None = None,
    ) -> Any:
        """GET /v1/{blid}/missionhistory -- NEW (July 11, sixth
        session). CONFIRMED from FetchMissionHistoryRequest.java
        (httpMethod = "GET", urlString = "/v1/" + robotId +
        "/missionhistory"). Matches the endpoint of the same name in
        ha_roomba_plus' cloud_api.py for Classic devices -- Prime uses
        the same URL pattern.

        Query parameters all confirmed (camelCase, Kotlin property name
        = wire name, no @SerialName found): maxReports, maxAge,
        filterType, exclusiveStartTimestamp, supportedDoneCodes (list
        joined with commas -- confirmed from
        ProvisioningErrorConstants.LAST_ERROR_INTERNAL_LINE_DELIMITER =
        ","). Response shape NOW modeled (ninth session) --
        models/mission_history.py::parse_mission_history() converts this method's
        result into a list of typed MissionHistoryEntry objects
        (analogous to parse_map_bundle() -- a separate, optional step
        rather than automatic conversion here).

        The Classic app asks the same endpoint with different parameter
        names -- see ClassicRestClient.get_mission_history().

        THE RESPONSE IS A LIST of mission records, not an object. This
        was annotated `dict[str, Any]` until 0.4.0; the Classic capture
        in tests/fixtures (irobot_missionhistory_i3plus.json) is a list,
        and ha_roomba_plus once lost every cloud refresh to a caller that
        believed the annotation. Returned as-is."""
        url = f"{self._http_base_auth}/v1/{_path_segment(blid)}/missionhistory"
        query: dict[str, str] = {}
        if max_reports is not None:
            query["maxReports"] = str(max_reports)
        if max_age is not None:
            query["maxAge"] = str(max_age)
        if filter_type is not None:
            query["filterType"] = filter_type
        if exclusive_start_timestamp is not None:
            query["exclusiveStartTimestamp"] = str(exclusive_start_timestamp)
        if supported_done_codes:
            query["supportedDoneCodes"] = ",".join(supported_done_codes)
        return await self._request("GET", url, query=query)

    async def get_schedules(self, household_id: str) -> SchedulesResponse:
        """GET /v1/households/{householdId}/settings/schedule -- NEW
        (July 11, sixth session). CONFIRMED from SchedulesCommonRequest/
        FetchSchedulesRequest (httpMethod = "GET", urlString without a
        householdScheduleId suffix). CORRECTED (session 51): the
        response shape (SchedulesResponse -> household_schedules ->
        SchedulesList) is now confirmed via SchedulesResponse$$serializer/
        SchedulesList$$serializer -- previously the class names had
        been found but not their fields, so this returned raw JSON.
        Now parsed directly."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule"
        data = await self._request("GET", url)
        return SchedulesResponse.from_json(data)

    async def get_schedules_raw(self, household_id: str) -> Any:
        """Same endpoint as get_schedules(), but returns the UNPARSED
        response. Added for field diagnosis, same reasoning as
        get_favorites_raw().

        WHY THIS WAS NEEDED. A tester whose app shows three schedules
        was told by this library that he has none. Every layer between
        the server and him -- SchedulesResponse.from_json(), which drops
        anything not under "household_schedules", and the reporting tool
        above it -- could produce that same empty answer, and a parsed
        result cannot distinguish "the server sent nothing" from "we
        failed to read what it sent". Two field rounds were spent on
        that ambiguity.

        Diagnostic use only; the library's normal path uses
        get_schedules()."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule"
        return await self._request("GET", url)

    async def delete_schedule(self, household_id: str, household_schedule_id: str) -> dict[str, Any]:
        """DELETE /v1/households/{householdId}/settings/schedule/{id} --
        CONFIRMED from DeleteSchedulesRequest (httpMethod = "DELETE").

        THE OUTER ID, AND THE RESPONSE OFFERS TWO. A create response
        carries `household_schedule_id` at the top and a `schedule_id`
        nested inside `schedules[]` -- the second is the first plus a
        robot suffix, and both look equally like "the id of this
        schedule"::

            household_schedule_id  hh_irbt.hh.<uuid>_<token>_s
            schedules[0].schedule_id  hh_irbt.hh.<uuid>_<token>_s_K452

        The nested one returns HTTP 500 here. Field-confirmed by
        @Nguyen on a Combo: deleting by the outer id succeeded and was
        verified with `get_schedules()` either side; the suffixed one
        failed every time.

        A 500 rather than a 404 is what makes this expensive to work
        out -- it reads like a server fault rather than a wrong
        argument, which is why the parameter is named for the field it
        wants."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule/{_path_segment(household_schedule_id)}"
        return await self._request_object("DELETE", url)

    async def create_schedules(self, household_id: str, schedules: list[ScheduleOptions]) -> dict[str, Any]:
        """POST /v1/households/{householdId}/settings/schedule --
        CONFIRMED (eighth session: CreateSchedulesRequest.<init> sets
        httpMethod = "POST" directly, androguard bytecode inspection --
        previously only assumed). Field structure also confirmed, see
        models/schedules_dnd.py::ScheduleOptions.

        THE BODY NESTS EACH SCHEDULE, and getting that wrong is what
        returned AspenError.InternalError on every attempt for four
        field rounds.

        CreateSchedulesRequest.getHttpBody() serialises a
        ScheduleListUpdate whose entries are HouseholdScheduleUpdate
        objects, built as `HouseholdScheduleUpdate(options, null)`. With
        the @SerialName annotations from both $$serializers that is:

            {"schedules": [{"options": {...}}]}

        WITHOUT `schedule_id`, and that distinction was itself corrected
        during the analysis. The constructor does receive null, so the
        first reading was "sent explicitly as null". But both elements
        are registered with isOptional=true and default null, and
        CreateSchedulesRequest uses Json.Default -- no encodeDefaults --
        so write$Self skips the field entirely. (P2MapsRequest sets
        encodeDefaults=true; this one does not.)

        Sending `"schedule_id": null` and omitting the key are not the
        same thing to a server, and this library would have written the
        null.

        The top-level key was right all along. What was missing is the
        `options` wrapper: this used to put the ScheduleOptions straight
        into the array, so the server found `name`, `commands`,
        `frequency` where it expected `{options, schedule_id}`. A
        structural mismatch like that produces a 500 rather than a 400 --
        which is exactly what came back, with no field named, four
        times.

        WHY UPDATE WAS NEVER AFFECTED: update_schedules() takes
        HouseholdSchedule objects, and HouseholdSchedule.to_json()
        already emits {schedule_id, options}. So toggling a schedule
        worked in the field while creating one never did, from the same
        module -- the two paths happened to disagree about a shape only
        one of them had confirmed.

        ONE SCHEDULE PER CALL. The signature takes a list and the body
        is an array, so this reads as a batch create. It is not: with two
        entries the server answers HTTP 200 and keeps only the LAST one,
        silently. @Nguyen sent "Sat/Sun deep clean" and "Wed spot
        vacuum" together on a Combo, got a 200, and `get_schedules()`
        came back with one household_schedule_id holding only the
        second. Calling this once per schedule created both correctly.

        A warning is logged rather than an exception raised: the
        endpoint accepts the request, and refusing it here would break
        any caller that happens to pass one item in a list built
        elsewhere. But a 200 that drops data is worse than an error, so
        it does not pass unremarked."""
        if len(schedules) > 1:
            _LOGGER.warning(
                "create_schedules(): %d schedules in one call. The server "
                "returns 200 and keeps only the last -- send one per call, "
                "or the earlier ones are lost without an error",
                len(schedules),
            )
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule"
        body = {"schedules": [{"options": s.to_json()} for s in schedules]}
        return await self._request_object("POST", url, body=body)

    async def update_schedules(
        self, household_id: str, household_schedule_id: str, schedules: list[HouseholdSchedule]
    ) -> dict[str, Any]:
        """PUT /v1/households/{householdId}/settings/schedule/{id} --
        CONFIRMED (eighth session: UpdateSchedulesRequest.<init> sets
        httpMethod = "PUT" directly -- previously only assumed). Field
        structure confirmed, see models/schedules_dnd.py::HouseholdSchedule."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/schedule/{_path_segment(household_schedule_id)}"
        return await self._request_object("PUT", url, body={"schedules": [s.to_json() for s in schedules]})

    async def get_user_households(self) -> dict[str, Any]:
        """GET /v1/user/households -- NEW (July 11, seventh session).
        HTTP method was pure REST convention (this endpoint isn't
        called anywhere by app version 2.2.4 -- the constant
        HOUSEHOLDS_TEMPLATE exists, but no request class uses it).
        STILL LIVE CONFIRMED (session 28, chairstacker): works
        flawlessly, returns a real, clearly structured response --
        "unused in the current app code" here actually just meant
        "this version doesn't need it", not "the server no longer
        supports it".

        Response shape confirmed: household_id, owner_cognito_id,
        household_name (observed: "#AUTO_GENERATED_HOUSEHOLD#"),
        has_precise_location, household_robots, household_users. For a
        typed result use models/robot_info.py::parse_user_households()."""
        url = f"{self._http_base_auth}/v1/user/households"
        return await self._request_object("GET", url)

    async def get_dnd_settings(self, household_id: str) -> DNDStatusResponse:
        """GET /v1/households/{householdId}/settings/dnd -- NEW (July
        11, sixth session). CONFIRMED from DNDGetRequest (httpMethod
        = "GET"). Response shape confirmed (ninth session) --
        models/schedules_dnd.py::DNDStatusResponse. IMPORTANT: see
        DNDStatusResponse's docstring for the distinction from the
        separate DNDSchedule class family.

        CORRECTED (session 53): actually parsed into DNDStatusResponse
        now -- same architectural gap as get_robot_parts()/
        get_serial_number_data(), see get_robot_parts()'s docstring."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/dnd"
        data = await self._request("GET", url)
        return DNDStatusResponse.from_json(data)

    async def get_dnd_settings_raw(self, household_id: str) -> Any:
        """Same endpoint as get_dnd_settings(), UNPARSED.

        WHY THIS EXISTS. Quiet hours are the last unbuilt feature of the
        V4/Prime line, and the reason is not that nobody has asked --
        it is that nobody has ever seen a populated response. On three
        separate accounts DNDStatusResponse comes back with `status`
        empty and every other field None, because none of those users
        has quiet hours configured.

        So this library's DND model is four fields with no populated
        example behind any of them, and set_dnd_settings()'s own
        docstring admits the write body was "not further investigated".
        The one live write attempt returned HTTP 400 -- from a check
        that resent an empty settings object, which is what you get for
        writing a shape you have never read.

        Raw rather than parsed for the same reason as
        get_schedules_raw(): a parsed result cannot distinguish "the
        server sent nothing" from "we failed to read what it sent", and
        the first real response is exactly the one that must not be
        filtered through assumptions.

        Read-only. Nothing is sent."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/dnd"
        return await self._request("GET", url)

    async def set_dnd_settings(self, household_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """PUT /v1/households/{householdId}/settings/dnd -- CONFIRMED
        from DNDPutRequest (httpMethod = "PUT").

        BODY FORMAT CONFIRMED (APK, 2 August 2026), where this docstring
        previously said "not further investigated". DNDPutRequest
        serialises a DNDSchedule directly:

            Json.Default.encodeToString(DNDSchedule.serializer(), body)

        No envelope, no nesting, no discriminator -- and Json.Default,
        so defaults are omitted rather than sent as null.

        DNDSchedule is a SEALED CLASS with exactly two mutually
        exclusive variants:

            {"dailyStart": int, "dailyEnd": int}   quiet hours every day
            {"endsAt": long}                       quiet until one moment

        Build the body with models/schedules_dnd.py::DNDDailySchedule
        or ::DNDEndsAt. Never both: the app's own type system makes
        that impossible, and it is the shape the one live attempt sent
        before returning HTTP 400.

        Still a dict here rather than a typed parameter, because the
        two variants have no common base worth inventing for a
        two-field body -- but the two models exist and their to_json()
        produces exactly what belongs on the wire.

        BOTH VARIANTS ARE GONE FROM APP 3.0.0. A structural diff of
        2.2.4 against 3.0.0 lists `DNDSchedule$DailySchedule` and
        `DNDSchedule$EndsAt` under `only_224`, and neither appears among
        3.0.0's serialisable classes. The 3.0.0 report still names this
        endpoint and this structure -- by citing the 2.2.4 work, not by
        re-deriving it.

        WHAT THAT DOES AND DOES NOT MEAN. What a client stopped shipping
        is a fact about that client; the endpoint is the server's, and
        it has no reason to break older clients. This project holds the
        counter-example in `ScheduleOptions`, where a field arrived on
        real schedules while appearing nowhere in 2.2.4 -- the server
        was ahead of the app then.

        So the body shape stands, and the confidence behind it is one
        step weaker than it reads: confirmed in 2.2.4, absent in 3.0.0,
        never sent successfully by anyone. The one live attempt returned
        HTTP 400, and it sent both variants at once -- which the sealed
        class forbids and this docstring says to avoid, so that failure
        does not test the shape.

        A SINGLE SUCCESSFUL WRITE WOULD SETTLE IT, and nothing short of
        one will."""
        url = f"{self._http_base_auth}/v1/households/{_path_segment(household_id)}/settings/dnd"
        return await self._request_object("PUT", url, body=settings)

    async def get_cleaning_profiles(self, asset_id: str, p2map_id: str | None = None) -> dict[str, Any]:
        """GET /v1/profiles -- NEW (July 11, sixth session). CONFIRMED
        from CleaningProfileRequest (httpMethod = "GET").

        CORRECTED (session 38): the previous query parameter names
        ("asset_id"/"p2map_id") were wrong and are the confirmed cause
        of a live HTTP 400 (chairstacker). Read directly from
        CleaningProfileRequest.getQueryParams()'s decompiled Kotlin
        logic (jadx, cleanly decompiled -- not a guess this time):
          - robot/asset id key is "robotId" (NotificationCenterConsts
            .IN_APP_NAV_QUERY_PARAM_ROBOT_ID's literal value) --
            camelCase, NOT "asset_id" as previously assumed.
          - map id key is "p2map_id" (PushNotificationConsts
            .PERSISTENT_MAP_ID's literal value) -- this one was
            already correct.
          - a THIRD query parameter, "includeSmart", was completely
            missing before: "true" whenever p2map_id is present and
            non-blank, "false" otherwise -- and in the "false" case,
            p2map_id itself is dropped from the query entirely (not
            sent even as an empty string). `p2map_id` is therefore
            made optional here to mirror that real branching, not
            just to be permissive.

        NOT yet live-verified with this corrected query shape -- the
        previous snake_case attempt (session 33) was itself an
        unconfirmed guess that turned out wrong; this one is a direct
        bytecode read, a much stronger basis, but still unconfirmed
        against a real server until re-tested. Response shape modeled
        (ninth session) -- models/robot_info.py::CleaningProfile.from_json() per
        entry."""
        url = f"{self._http_base_auth}/v1/profiles"
        query = {"robotId": asset_id}
        if p2map_id:
            query["includeSmart"] = "true"
            query["p2map_id"] = p2map_id
        else:
            query["includeSmart"] = "false"
        return await self._request_object("GET", url, query=query)

    async def get_default_routines(self, p2map_id: str) -> RoutinesDefaultsResponse:
        """GET /v1/p2maps/{p2mapId}/routines/defaults -- NEW (July 11,
        sixth session). Automatically generated cleaning suggestions
        per map (e.g. "whole home", "kitchen only"). Response shape
        confirmed (forty-ninth session) --
        models/robot_info.py::RoutinesDefaultsResponse.

        CORRECTED (session 53): actually parsed into
        RoutinesDefaultsResponse now (which also captures
        routine_builder_defaults, previously not exposed here at all)
        -- same architectural gap as get_robot_parts() and others, see
        that method's docstring."""
        url = f"{self._http_base_auth}/v1/p2maps/{_path_segment(p2map_id)}/routines/defaults"
        data = await self._request("GET", url)
        return RoutinesDefaultsResponse.from_json(data)

    async def get_firmware_raw(
        self,
        sku: str | None = None,
        software_ver: str | None = None,
        track: str | None = None,
        dock_fw_ver: str | None = None,
        dock_fw_ver_sec: str | None = None,
        dock_hw_rev: str | None = None,
    ) -> Any:
        """Available firmware releases, from `GET /v2/firmware`.

        WHAT THE SHADOW CANNOT SAY. `softwareVer` reports what is
        installed; this reports what exists -- release notes, and
        `expectedInstallationTime`, which is what somebody deciding
        whether to start an update at nine in the evening actually needs.

        **THE METHOD QUESTION IS CLOSED AND A SMALLER ONE OPENED.**
        @utkjmitch got a **403**, not a 404 or 405: the path exists, GET
        resolves, and the consumer Cognito role has no
        `execute-api:Invoke` on it.

        So the app reaches its firmware information under some other
        role or channel. What is unknown now is not the verb but whether
        this endpoint is reachable by any credentials a consumer account
        can hold -- and if it is not, this method has no future beyond
        documenting that.

        RAW, AND THAT IS DELIBERATE. `FirmwareRequest` in app 3.0.0
        declares the path and no HTTP method, The response
        envelope is unknown too: `FirmwareItemDto` describes an item,
        not what wraps it.

        So this returns whatever comes back. `FirmwareItem.from_json`
        parses an item once a caller has found where the items are.
        Modelling a response nobody has seen is how this library got a
        `time_estimates` shape it had to replace wholesale.

        WRONG HOST, AND THAT WAS THE 403. @utkjmitch's 403 came from
        `httpBaseAuth`, the SigV4-signed API gateway. The firmware
        catalogue lives on the **content** host and needs **no auth at
        all** -- confirmed by samm-git/irobot-explore's independent
        reconstruction:

            GET https://content-prod.iot.irobotapi.com/v2/firmware
                ?sku=<SKU>&softwareVer=<ver>&track=prod&dockFwVer=

        `softwareVer` must be URL-encoded: the `+` in
        `p25-705+9.3.6+I3.8.149` becomes `%2B`.

        So "the path exists, the consumer role has no invoke rights"
        was the right reading of the response and the wrong conclusion
        about the endpoint. It was never gated -- we were knocking on
        a different door.

        UNVERIFIED HERE. Host and parameters come from that
        reconstruction, not from a capture in this project.
        """
        from urllib.parse import urlencode  # noqa: PLC0415

        # `Domain.ContentStack` in the APK resolves as
        # `content-{env}.iot.{domain}` -- a pattern, not a fixed host.
        # Hardcoding `content-prod.iot.irobotapi.com` matches every
        # production account anyone has, and would silently miss a
        # different environment.
        #
        # Left hardcoded because nothing in the discovery response
        # carries this host: it is not among the `deployments` fields.
        # If a caller ever needs another environment, this is the line.
        base = "https://content-prod.iot.irobotapi.com/v2/firmware"

        # `sku` and `softwareVer` are the only required parameters.
        # APK 3.0.0's `FirmwareRequest` sets the other four only when
        # non-null -- sending `track=prod&dockFwVer=` unconditionally,
        # as the reference implementation does, is a guess about
        # defaults rather than what the app sends.
        params: dict[str, str] = {}
        if sku:
            params["sku"] = sku
        if software_ver:
            # urlencode quotes the `+` for us; hand-building the query
            # is how it becomes a space and the lookup silently misses.
            params["softwareVer"] = software_ver
        for key, value in (
            ("track", track),
            ("dockFwVer", dock_fw_ver),
            # TWO PARAMETERS THE REFERENCE MISSED. `FirmwareRequest`
            # declares six; samm-git/irobot-explore names four.
            ("dockFwVerSec", dock_fw_ver_sec),
            ("dockHwRev", dock_hw_rev),
        ):
            if value is not None:
                params[key] = value

        return await self._request("GET", f"{base}?{urlencode(params)}")

    async def reset_robot_parts(
        self,
        blid: str,
        part_ids: Sequence[str],
        counters: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """POST /v1/robots/{blid}/parts: set the named parts' counters,
        to zero unless `counters` says otherwise -- "this part is new".

        THE BODY, AS THE APP SENDS IT (corrected in 0.6.0):

            {"parts": [{"part_id": "<id>", "counter": 0}]}

        `AssetHealthPartResetRequest` serialises an
        `AssetResetHealthPayloadDto` -- `parts` and nothing else -- whose
        entries are `AssetPartResetDto`, `part_id` and `counter`. The app
        builds it with one part and `counter` 0, and encodes defaults, so
        the zero is sent. Apps 3.0.0 and 3.2.0 agree.

        WHAT 0.5.0 SENT, AND WHY IT WAS WRONG. `robot_id` and
        `num_parts` beside `parts`. Those are `AssetHealthResetDto`'s
        fields, and `AssetHealthResetDto` is the request's RESPONSE type
        -- the same shape get_robot_parts() answers with. The body was
        modelled on the answer.

        THE SAME BODY CLASSIC ROBOTS ACCEPT. ClassicRestClient's
        set_robot_part_counter() sends exactly this, confirmed on
        Classic, compact. This one keeps json.dumps()'s spacing, like
        every Prime body.

        `part_ids` IS REQUIRED. 0.5.0 sent `robot_id` alone when none
        were named, a body that cannot mean anything; there is no
        "reset everything" form, and guessing one rewrites somebody's
        maintenance history. Several ids go in one body -- `parts` is a
        list -- although the app only ever sends one."""
        ids = [str(part_id) for part_id in part_ids]
        if not ids or not all(ids):
            raise ValueError("reset_robot_parts() needs at least one part id")
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/parts"
        # Keyed as the ids are sent, so {35: 5} and {"35": 5} mean the same.
        values = {str(k): v for k, v in (counters or {}).items()}
        body = {
            "parts": [
                {"part_id": part_id, "counter": int(values.get(part_id, 0))}
                for part_id in ids
            ]
        }
        return await self._request_object("POST", url, body=body)

    async def get_serial_number_data(self, blid: str) -> RobotSerialInfo:
        """GET /v1/robots?robot_id={blid} -- NEW (session 15). CONFIRMED
        from the same configuration file (commandId "GetSerialNumberData",
        httpMethod=GET, urlPath="/v1/robots?robot_id=%s").

        Response shape confirmed (session 26, real live response
        from chairstacker): RobotID, SerialNumber, built_as_sku,
        family_variant, is_raas, is_refurbished, is_smartcare,
        min_utc_reg_date, name (user-assigned robot name, e.g.
        "House_Bot"), sku, series (e.g. "G1"), family (e.g.
        "Roomba Combo" -- confirms a vacuum+mop combo device),
        serial_history.

        CORRECTED (session 53): actually parsed into RobotSerialInfo
        now -- same architectural gap as get_robot_parts(), see that
        method's docstring."""
        url = f"{self._http_base_auth}/v1/robots"
        data = await self._request("GET", url, query={"robot_id": blid})
        return RobotSerialInfo.from_json(data)

    async def poll_echo_value(self, blid: str) -> dict[str, Any]:
        """POST /v1/robots/{blid}/echo -- NEW (session 16). CONFIRMED
        from base_roomba_config.json (commandId "PollEchoValueCommand,Set",
        httpMethod=POST, urlPath="/v1/robots/%s/echo"). Matches the
        "echo" feature ("find my robot" -- audible chime/announcement)
        -- consistent with the SetRoombaEchoAwsIotSerializer finding
        from the native analysis. Body shape unknown -- presumably
        empty or a simple trigger, no payload needed for the simplest
        case. No body included, until proven otherwise."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/echo"
        return await self._request_object("POST", url)

    async def get_time_estimates(
        self,
        blid: str,
        smart_map_id: str | None = None,
        region_id: str | None = None,
        zone_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/time-estimates -- NEW (session 16). CONFIRMED from
        base_roomba_config.json (commandId "GetTimeEstimates",
        httpMethod=POST despite "read": true -- presumably POST because
        the request needs a body to know WHICH mission/rooms to
        estimate, not because it's a write).

        PARTIALLY CONFIRMED (parallel native-analysis track,
        MissionTimeEstimatesRepositoryImpl.java): the real call site is
        fetchTimeEstimatesWithAreasForAsset(assetId, mapId,
        commandDefRegions: ArrayList<String>, screen) --
        commandDefRegions is a LIST OF REGION-ID STRINGS specifically
        (not full region/command objects), and "screen" is an
        analytics-tracking parameter only, not part of the actual wire
        body. The exact JSON key names the body ultimately serializes
        to remain unconfirmed (native-level from here) -- body is
        still passed through as a raw dict, to be filled in by the
        caller themselves.

RESOLVED (30 July 2026) -- and the earlier "not determinable"
        note in this docstring was wrong.

        The body is a single field:

            {"robot_id": "<BLID>"}

        Confirmed by tracing the native call to StringUtils::vformat:
        the format string is `{ "%s": "%s" }` (length 0x1c>>1 = 14
        matches), x1 holds kRobotId and x2 the robot id itself. The
        csinc pairs around it are libc++'s standard short-string
        optimisation branch, so there is no ambiguity about which
        pointer is passed.

        THAT IS WHY NO SERIALIZER EXISTS. The body is assembled as a
        string with printf-style substitution -- there is no
        @Serializable class to find, which is what every earlier search
        was looking for. Worth remembering: absence of a serializer is
        not absence of a documented format.

        And an earlier abort was premature. This was closed as "native,
        therefore not determinable" on the grounds that native vtable
        reconstruction is unreliable. That rule is about reconstructing
        STRUCTURE from vtables; a format string with traced register
        arguments is direct evidence of a different kind.

        NOT IN THE BODY: mapId and the region list, despite both
        appearing in fetchTimeEstimatesWithAreasForAsset(). The server
        returns everything for the robot and the app filters
        client-side.

        RESPONSE SHAPE CONFIRMED 31 July 2026 (@DaRealGuGu, N185240):

            {"robot_id": "<BLID>",
             "api_version": "v1",
             "smart_maps": [
               {"smart_map_id": "<BLID>-<epoch>",
                "areas": [
                  {"area_id": "12", "area_type": "region",
                   "estimates": [
                     {"value": 533, "unit": "seconds", "deviation": 0.0,
                      "data_model_version": "app_prime",
                      "params": {"operatingMode": 512, "suctionLevel": 3,
                                 "swScrub": 0, "twoPass": false}},
                     ...
                   ]}
                ],
                "cleaning_rates": {"deep": 885.0, "light": 391.0,
                                   "standard": 479.0}}
             ]}

        THE SHAPE IS RICHER THAN EXPECTED. It is not one estimate per
        room -- it is one per room PER PARAMETER COMBINATION. A single
        room came back with 44 entries covering every mix of
        operatingMode, suctionLevel, swScrub and twoPass.

        So this answers "how long would this room take at these
        settings", not just "how long does this room take". Anything
        picking a single number has to select by params, and the sensible
        selection is the room's own last_operating_mode from the map
        metadata.

        `cleaning_rates` is per profile in area-per-hour terms and is
        map-wide rather than per room -- useful for a room that has no
        estimate yet.

        Note the parameter names are the camelCase wire keys used
        everywhere else in the command domain, not the snake_case of this
        library's models."""
        url = f"{self._http_base_auth}/v1/time-estimates"
        # Built here rather than taken as a raw dict. The old signature
        # made every caller invent the body, which meant every caller
        # could invent it differently -- and nobody could, because the
        # key was unknown.
        # THE BODY HAS FOUR FIELDS, and this sent one.
        #
        # `TimeEstimatesRequestBody` in app 3.0.0 declares `robot_id`,
        # `smart_map_id`, `region_id` and `zone_id`. Sending only the
        # first asks for every estimate on every map -- which works, and
        # is what a caller wanting one room's number pays for.
        #
        # The three narrowing fields are optional here for the same
        # reason they are nullable there: omitting them is a valid
        # request, and the broad answer is the one this library has
        # field-confirmed on two accounts.
        body: dict[str, Any] = {"robot_id": blid}
        if smart_map_id is not None:
            body["smart_map_id"] = smart_map_id
        if region_id is not None:
            body["region_id"] = region_id
        if zone_id is not None:
            body["zone_id"] = zone_id
        return await self._request_object("POST", url, body=body)

    async def get_clean_score_raw(self, p2map_id: str) -> Any:
        """POST /v1/p2maps/clean-score -- a per-ROOM cleanliness value.

RESPONSE WIRE KEYS CONFIRMED (APK, 2 August 2026) -- as
        LITERALS in libdataModule.so, read out of jsonToCleanScoreData,
        not from the Kotlin property names:

            {
              "clean_score_ranges": [0.0, ...],
              "clean_scores": [{
                "p2map_id", "active_p2mapv_id", "user_p2mapv_id",
                "smart_clean_id", "mission_last_processed": {...},
                "regions": [{
                  "region_id", "clean_score", "updated_ts",
                  "last_updated_by", "smart_clean_prefs"
                }]
              }]
            }

        THE WIRE IS snake_case; the Kotlin side is camelCase
        (cleanScoreData, cleanScoreRegions, regionId, updatedTs). An
        earlier draft of this docstring wrote the Kotlin names as if
        they were the wire format -- the same confusion that once
        produced 21 wrong wire keys in this library.

        Ten of the thirteen keys are confirmed as literals. `p2map_id`,
        `active_p2mapv_id` and `regions` resolve through shared
        serialization constants rather than their own literals here, so
        their spelling is inherited from confirmed uses elsewhere rather
        than proven at this call site.

        Range is fixed: CleanScoreConst.MIN_CLEAN_SCORE = 0.0f,
        MAX_CLEAN_SCORE = 1.0f -- a float, so directly a percentage.

        NOT a mission result. The values hang off pmapId/regionId and
        `missionLastProcessed` only says how far the running value has
        been carried -- it is accumulated state per room. This is the
        data behind Smart Clean / "Dirt Detective".

        GET WITH A QUERY PARAMETER, not a POST body -- and that came
        from reading a second integration rather than from the APK.

        The APK could not supply it: the call goes through
        SmartCleanWrappedUseCase.fetchCleanScoreDataForMap() and across
        the Djinni boundary, exactly as /v1/time-estimates did. An
        earlier draft of this method guessed by analogy with that one --
        POST with `{"robot_id": ...}` there, so POST with
        `{"p2map_id": ...}` here. Plausible, and wrong.

        a-mavrides/roomba_v4 sends `GET .../clean-score?p2map_id=<id>`
        and, unlike its call to /v1/user/automations, actually CONSUMES
        the response -- it reads clean_scores[].regions[].region_id and
        smart_clean_prefs to derive per-room defaults. Code that reads
        what it fetched is evidence the fetch works; code that swallows
        errors and never looks is not.

        Still one integration's usage rather than a protocol document,
        so the check that calls this prints the request and the raw
        response.

        Returns the UNPARSED response. CleanScoreResponse.from_json()
        parses it and is built from the confirmed keys above -- the
        raw form stays available because a parsed result cannot
        distinguish "the server sent nothing" from "we failed to read
        what it sent", and this project has spent field rounds on
        exactly that ambiguity.
        """
        url = f"{self._http_base_auth}/v1/p2maps/clean-score"
        return await self._request("GET", url, query={"p2map_id": p2map_id})

    async def reset_robot(
        self,
        blid: str,
        robot_password: str | None = None,
        synchronous: bool | None = None,
        send_wipe: bool | None = None,
    ) -> dict[str, Any]:
        """POST /v1/{blid}/reset -- NEW (session 16). CONFIRMED from
        base_roomba_config.json (commandId "ResetRobotCommand",
        httpMethod=POST, networkList contains both awsApiGateway and
        lss -- so it exists for both Classic AND Prime). WARNING:
        presumably a factory reset or at least a significant reset
        operation -- the name and "write": true suggest this triggers
        a REAL, potentially consequential action on the device. Never
        live-tested. Don't call this lightly.

        THE BODY IS DECLARED AND THIS SENT NONE. `ResetRequest$Body`
        (app 3.0.0) carries three fields:

            robot_password   the robot's own password
            synchronous      wait for completion, or fire and forget
            send_wipe        WHETHER TO WIPE

        `send_wipe` is the field that decides how consequential this
        is, and until now it was neither sent nor known to exist. A
        bodyless POST leaves that decision to the server's default, and
        nobody here knows what that default is -- which is a worse
        position than choosing, and it was arrived at by not looking.

        DEFAULT BEHAVIOUR IS DELIBERATELY UNCHANGED: with no arguments
        this still sends no body, exactly as before. Nothing here has
        ever called this endpoint, so switching an untested call to a
        different untested call would trade one unknown for another on
        an operation that may wipe somebody's robot.

        What is new is that a caller CAN be explicit. `send_wipe=False`
        is the conservative choice and the one worth trying first if
        this is ever exercised."""
        url = f"{self._http_base_auth}/v1/{_path_segment(blid)}/reset"
        body: dict[str, Any] = {}
        if robot_password is not None:
            body["robot_password"] = robot_password
        if synchronous is not None:
            body["synchronous"] = synchronous
        if send_wipe is not None:
            body["send_wipe"] = send_wipe
        if not body:
            return await self._request_object("POST", url)
        return await self._request_object("POST", url, body=body)

    async def get_notifications(self, blid: str, app_version: str = "2.2.4") -> dict[str, Any]:
        """GET /v1/robots/{blid}/timeline.

        MOSTLY MARKETING, and worth saying before anyone builds on it.
        Six of the seven `details_type` values are surveys, banners and
        commerce: `NPS_SURVEY`, `MISSION_SURVEY`, `E_COMMERCE`,
        `CONTEXTUAL_MESSAGE`, `CONTEXTUAL_MESSAGE_NON_DISMISSIBLE`,
        `BANNER`.

        The robot events an integration wants are in `MissionTimelineDto`
        / `MissionEventDto`, which this library already parses and which
        arrive over MQTT without a REST call.

        There is also a `GET /v1/user/timeline` covering every robot with
        a filter. Same content; the per-robot path just saves the
        filtering.

        Originally: NEW (session 16). CONFIRMED
        from base_roomba_config.json (commandId "GetNotifications",
        urlPath="/v1/robots/%s/timeline?event_type=HKC&
        details_type_filter=all&app_version=%s&limit=50"). "HKC" as an
        event_type value not resolved (abbreviation unknown) -- carried
        over 1:1 from the configuration file, not guessed.

        KNOWN BUG, LIKELY CAUSE NOW IDENTIFIED (session 36): live against a
        real account (chairstacker, session 25), this call failed with
        HTTP 400 using the previous placeholder value ("1.0") -- a value
        with zero evidentiary basis, never anything but a guess. The
        analyzed APK's own `com.irobot.home.BuildConfig.VERSION_NAME`
        and the `AndroidManifest.xml`'s `android:versionName` both
        confirm the real app build used for this analysis was "2.2.4" --
        a strong candidate for what `app_version` is actually meant to
        carry (the calling app's own version string), now used as the
        default here instead of the old placeholder. NOT yet live-tested
        with this corrected value -- the real Prime app in the field may
        since have moved to a newer version than "2.2.4", so this
        remains a best-effort default, not a guaranteed-correct one. If
        this call still fails with the corrected value, the cause lies
        elsewhere (missing header/parameter not visible in the
        configuration file, or a version the server no longer accepts).
        Do NOT treat this as working until this is
        resolved."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/timeline"
        return await self._request_object(
            "GET",
            url,
            query={
                "event_type": "HKC",
                "details_type_filter": "all",
                "app_version": app_version,
                "limit": "50",
            },
        )

    @staticmethod
    def _favorite_from_json(data: dict[str, Any]) -> FavoriteV1:
        """Builds a FavoriteV1 from raw JSON. Deliberately tolerant
        (.get() everywhere) -- never seen a real server response to
        know which fields are truly always present.

        NEW (this session, parallel native-analysis track): Favorite's
        own Kotlin/Java field is typed List<String>, not a list of
        already-structured objects -- meaning each entry may arrive as
        a JSON-ENCODED STRING (a serialized RoutineCommand) rather than
        a dict directly. CONFIRMED (same track, follow-up): the real
        app deserializes each string to a full RoutineCommand object
        (bytecode shows `check-cast ... RoutineCommand`, not a plain
        string being used opaquely) -- a favorite genuinely carries
        complete command definitions (including regions/params), not
        just a reference. Defensively handles both wire shapes here:
        json.loads() first if an entry is a str, used as-is if it's
        already a dict. Without this, a real string-shaped response
        would crash outright on c["command"] (subscripting a string
        by a non-integer key)."""
        command_defs_raw = _either(data, "commanddefs", "command_defs") or []
        # A STRING THAT IS NOT JSON DROPS THAT COMMAND, not the whole
        # favourite. `json.loads` raised straight out of the parser, and
        # the caller catches per favourite -- so one malformed command
        # def deleted a working favourite from the user's list entirely.
        #
        # The rest of this function is deliberately tolerant: an unknown
        # command parses to None rather than failing. This one line was
        # not.
        decoded: list[Any] = []
        for entry in command_defs_raw:
            if not isinstance(entry, str):
                decoded.append(entry)
                continue
            try:
                decoded.append(json.loads(entry))
            except ValueError:
                _LOGGER.warning(
                    "roombapy-prime: skipping a command def that is not "
                    "valid JSON on favourite %s",
                    _either(data, "favoriteid", "favorite_id", "id"),
                )
        command_defs_raw = [c for c in decoded if isinstance(c, dict)]
        return FavoriteV1(
            favorite_id=_either(data, "favoriteid", "favorite_id", "id"),
            name=data.get("name"),
            color=data.get("color"),
            icon=data.get("icon"),
            order=data.get("order"),
            display_order=_either(data, "display_order", "displayorder"),
            is_default=bool(data.get("default", False)),
            is_deleted=bool(data.get("deleted", False)),
            is_hidden=bool(data.get("hidden", False)),
            modification_secs=_either(
                data, "modification_secs", "modificationsecs"
            ),
            version=data.get("version"),
            command_defs=[
                RoutineCommand(
                    # TOLERANT, like every other enum read in this
                    # library. A stored favourite is the server's data,
                    # not ours, and it may carry a command a given
                    # library version does not model -- `MissionCommandType`
                    # lost two members today for being wrong, which is
                    # exactly the kind of change that must not delete
                    # somebody's favourites.
                    #
                    # `.get("command")` rather than `c["command"]`: a
                    # command def with no command key raised KeyError
                    # from a subscript, which read as a crash rather than
                    # as missing data.
                    command_type=_enum_or_none(
                        MissionCommandType, c.get("command")
                    ),
                    asset_id=c.get("robot_id", ""),
                    map_id=c.get("p2map_id"),
                    ordered=c.get("ordered", 0),
                    id_multipolys=c.get("id_multipolys"),
                    params=c.get("params"),
                    regions=c.get("regions"),
                    pmap_version_id=c.get("user_p2mapv_id"),
                    clean_all=bool(c.get("select_all", False)),
                    spot_geometry=c.get("geom"),
                    favorite_id=c.get("favorite_id"),
                    # READ, NOT DROPPED. The stored command def carries
                    # `initiator` and this did not parse it, so
                    # `to_json()` omitted the key entirely -- while the
                    # one region command CONFIRMED working on hardware
                    # (@Echovictor37) carries `initiator: "rmtApp"`.
                    #
                    # That is the only difference between the confirmed
                    # payload and the one a favourite button sends, and
                    # @chairstacker reports his button does nothing.
                    # Not proof: a PUBACK with no effect has had several
                    # causes in this project. But sending a field the
                    # server stored, in a payload otherwise identical to
                    # one that works, is the change worth making before
                    # looking further.
                    #
                    # `localApp` when the record carries none, matching
                    # every other command this library sends.
                    initiator=c.get("initiator") or "localApp",
                    # Passthrough only -- see RoutineCommand.command_id's
                    # own comment. Preserving what the server sent beats
                    # dropping it, even while its meaning is unknown.
                    command_id=c.get("id"),
                )
                for c in command_defs_raw
            ],
            creation_timestamp=data.get("creation_timestamp"),
            last_user_modified=data.get("last_user_modified"),
            last_modified=data.get("last_modified"),
            time_estimates=None,
        )


class ClassicRestClient(CloudRestClient):
    """The cloud calls of Classic robots (900 series, i/s/j series).

    NEW IN 0.4.0. These are the requests ha_roomba_plus sent for Classic
    robots through its own cloud_api.py up to 4.2.12, moved here
    unchanged so both generations use one library. "Unchanged" is
    literal and tested: the query and its order, the body bytes and so
    the SigV4 signature match what 4.2.12 sent (tests/test_rest_client.py,
    "Classic parity"). All of them are confirmed on Classic hardware in
    the integration's field use.

    Controlling a Classic robot is not part of this library -- that
    stays local, over roombapy. Only its cloud calls live here.

    Classic robots use pmaps; Prime robots use a different map system
    (p2maps, PrimeRestClient.get_active_map_versions()). Where both
    generations share an endpoint but the apps send it differently --
    mission history, favorites, the part-counter write -- each client
    sends its own generation's form. Whether the Prime forms also work
    on a Classic robot is what `roombapy-prime-verify-classic-cloud`
    (tools package) measures.

    `app_id` is the id the account logged in with. The Classic app sends
    it again with every mission-history request, so the client keeps it
    and adds it by default (CloudAccount passes it in)."""

    #: What the Classic app asks mission history for by default -- the
    #: values ha_roomba_plus sent with every request up to 4.2.12.
    MISSION_HISTORY_FILTER = "omit_quickly_canceled_not_scheduled"
    MISSION_HISTORY_DONE_CODES = ("dndEnd", "returnHomeEnd")
    MISSION_HISTORY_COUNT = 100

    def __init__(
        self,
        session: aiohttp.ClientSession,
        http_base_auth: str,
        credentials: CloudCredentials,
        relogin: Relogin | None = None,
        app_id: str | None = None,
        *,
        request_timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        super().__init__(
            session, http_base_auth, credentials, relogin, request_timeout=request_timeout
        )
        self.app_id = app_id

    async def get_mission_history(
        self,
        blid: str,
        *,
        app_id: str | None = None,
        filter_type: str | None = MISSION_HISTORY_FILTER,
        supported_done_codes: Sequence[str] | None = MISSION_HISTORY_DONE_CODES,
        count: int | None = MISSION_HISTORY_COUNT,
        before: int | None = None,
    ) -> Any:
        """GET /v1/{blid}/missionhistory with the Classic parameters --
        `count` and `before` (a start time, for paging), where the Prime
        app sends `maxReports` and `exclusiveStartTimestamp`.

        The query is built in Classic's own order -- app_id, filterType,
        supportedDoneCodes, count, before -- so the request is the same
        on the wire, not merely the same set of keys.

        THE DEFAULTS ARE THE CLASSIC APP'S, so `get_mission_history(blid)`
        is the request ha_roomba_plus sent: its filter, its two done
        codes, 100 records, and the account's `app_id` (this client's
        own, unless another is passed). Pass None to leave a key out.

        `count` AND `before` DO NOTHING. Measured on a Roomba 980
        (R980040) and an i7 (i755840): 10 asked, every mission of the
        cloud's recent window returned (33 and 67); `before` answered
        with that same window again. The request is kept as it is
        because it is the one ha_roomba_plus has always sent for its
        regular refresh. To page, use get_mission_history_page().

        Returns a LIST of mission records (see
        tests/fixtures/classic_missionhistory_i3plus.json), as-is."""
        url = f"{self._http_base_auth}/v1/{_path_segment(blid)}/missionhistory"
        query: dict[str, str] = {}
        app_id = app_id if app_id is not None else self.app_id
        if app_id is not None:
            query["app_id"] = app_id
        if filter_type is not None:
            query["filterType"] = filter_type
        if supported_done_codes:
            query["supportedDoneCodes"] = ",".join(supported_done_codes)
        if count is not None:
            query["count"] = str(count)
        if before is not None:
            query["before"] = str(before)
        return await self._request("GET", url, query=query)

    async def get_mission_history_page(
        self,
        blid: str,
        *,
        before: int | None = None,
        page_size: int | None = MISSION_HISTORY_COUNT,
        filter_type: str | None = MISSION_HISTORY_FILTER,
        supported_done_codes: Sequence[str] | None = MISSION_HISTORY_DONE_CODES,
    ) -> Any:
        """GET /v1/{blid}/missionhistory with the parameters that page on
        a Classic robot. NEW IN 0.4.0b3.

        `page_size` is sent as `maxReports`, `before` (a mission start
        time; only older missions come back) as `exclusiveStartTimestamp`
        -- the names from `FetchMissionHistoryRequest`, as
        PrimeRestClient.get_mission_history() sends them. No `app_id`:
        the measurements below sent none. (The Roomba Home app 3.0.0 does
        not page at all: it asks with `app_id` and `maxAge=10`.)

        MEASURED ON TWO CLASSIC GENERATIONS with
        `roombapy-prime-verify-classic-cloud`: a Roomba 980 (R980040) and
        an i7 (i755840). Both returned exactly 10 records for 10 asked,
        then, from the oldest start time, 10 older ones with none
        repeated -- and the same fields as get_mission_history(). The
        Classic parameters did neither (see get_mission_history()).

        NOT MEASURED: a page size other than 10. The default is 100, the
        Classic app's own count. A caller that pages should treat an
        error as "no further pages" rather than as a failed refresh.

        Returns a LIST of mission records, as-is."""
        url = f"{self._http_base_auth}/v1/{_path_segment(blid)}/missionhistory"
        # PrimeRestClient.get_mission_history()'s order: maxReports,
        # filterType, exclusiveStartTimestamp, supportedDoneCodes -- the
        # request the measurements made, not merely the same keys.
        query: dict[str, str] = {}
        if page_size is not None:
            query["maxReports"] = str(page_size)
        if filter_type is not None:
            query["filterType"] = filter_type
        if before is not None:
            query["exclusiveStartTimestamp"] = str(before)
        if supported_done_codes:
            query["supportedDoneCodes"] = ",".join(supported_done_codes)
        return await self._request("GET", url, query=query)

    async def get_favorites_raw(self, app_edition: str | None = None) -> list[dict[str, Any]]:
        """The Classic request: no `app_edition`. The base default,
        `app_edition=1`, is the Prime app's. Raw as in the base: an
        object with no `favorites` key comes back whole, because that
        is the case a diagnostics download is taken for."""
        return await super().get_favorites_raw(app_edition=app_edition)

    async def get_favorites(self) -> list[dict[str, Any]]:
        """The favourites as a list, and ONLY favourites: an answer with
        no list in it is no favourites, not one favourite made of the
        whole answer. That is what ha_roomba_plus built its favourite
        buttons from -- a button per entry, so an object passed through
        whole became a button with no name and no id.

        Use get_favorites_raw() to see what the server actually sent."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        data = await self._request("GET", url)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("favorites"), list):
            return list(data["favorites"])
        return []

    async def get_automations(self) -> dict[str, Any]:
        """GET /v1/user/automations as an object; anything else comes
        back as `{}`, as it did in ha_roomba_plus. The raw answer is
        get_automations_raw()."""
        data = await self.get_automations_raw()
        return data if isinstance(data, dict) else {}

    async def get_pmaps(self, blid: str) -> list[dict[str, Any]]:
        """GET /v1/{blid}/pmaps?visible=true&activeDetails=2 -- the
        Classic persistent-map list. Each entry carries `pmap_id`,
        `active_pmapv_id` and `active_pmapv_details` (regions, zones,
        the UMF header). Anything other than a list comes back empty,
        as it did in the integration."""
        url = f"{self._http_base_auth}/v1/{_path_segment(blid)}/pmaps"
        data = await self._request(
            "GET", url, query={"visible": "true", "activeDetails": "2"}
        )
        return data if isinstance(data, list) else []

    async def get_pmap_umf(
        self, blid: str, pmap_id: str, version_id: str
    ) -> dict[str, Any]:
        """GET /v1/{blid}/pmaps/{pmap_id}/versions/{version_id}/umf
        ?activeDetails=2 -- one Classic map version in Unified Map
        Format, with room polygons (get_pmaps() regions carry metadata
        only).

        AN ANSWER THAT IS NOT A NON-EMPTY OBJECT RAISES RestError --
        including an empty body, which the request layer turns into `{}`.
        An empty dict would be read as a map with nothing on it:
        ha_roomba_plus shows a mission map from this, and a server
        answering oddly would then draw a blank map instead of saying
        the map is unavailable, which is what it said until 4.2.12."""
        url = (
            f"{self._http_base_auth}/v1/{_path_segment(blid)}"
            f"/pmaps/{_path_segment(pmap_id)}"
            f"/versions/{_path_segment(version_id)}/umf"
        )
        data = await self._request("GET", url, query={"activeDetails": "2"})
        if not isinstance(data, dict) or not data:
            raise RestError(
                f"UMF for pmap {pmap_id} version {version_id} is not a non-empty object "
                f"({type(data).__name__}, {len(data) if isinstance(data, (dict, list)) else '-'} entries)",
                raw_response=str(data)[:300],
            )
        return data

    async def set_robot_part_counter(
        self, blid: str, part_id: str, counter: int
    ) -> dict[str, Any]:
        """POST /v1/robots/{blid}/parts with the body Classic robots
        accept: `{"parts": [{"part_id": ..., "counter": ...}]}`, sent
        compact, byte for byte what the integration sent.

        `counter` is PERCENT USED, the field get_robot_parts() returns,
        so 0 means "this part is new". Measured on Classic, where two
        other bodies fail without saying so: a flat
        `{"part_id": "35"}` is answered 200 with `num_parts: 0` and
        changes nothing, and PUT/PATCH come back 403 as if the
        credentials were wrong.

        The Prime client's reset_robot_parts() sends the same body
        since 0.6.0 -- in 0.5.0 it added `robot_id` and `num_parts`,
        modelled on the response -- with json.dumps()'s spacing. The two
        stay apart because each client keeps to its own generation."""
        url = f"{self._http_base_auth}/v1/robots/{_path_segment(blid)}/parts"
        body = {"parts": [{"part_id": str(part_id), "counter": int(counter)}]}
        data = await self._request("POST", url, body=body, compact_body=True)
        return data if isinstance(data, dict) else {}
