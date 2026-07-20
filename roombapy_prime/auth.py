"""
roombapy_prime.auth — Gigya login → iRobot cloud login → Custom Authorizer
connection tokens.

Extracted and cleaned up from validated, live-tested standalone scripts
(stage1-4, smart_tier_shadow_check.py). Originally confirmed only
against Classic-protocol accounts:
  - one EPHEMERAL-tier robot (900-series, Classic app account)
  - two SMART-tier robots (i7-series, Classic app account)
Native binary analysis of the Prime app (liblegacyCore.so) confirmed the
same field names (connection_tokens, iot_token, iot_signature,
iot_authorizer_name, client_id, robots) as a hypothesis at the time.

UPDATE (v0.1.2a0, 2026-07-13): this full chain (Discovery -> Gigya ->
iRobot auth) is now live-confirmed against a real Prime/V4 account
(chairstacker, Roomba 405/SKU G185020), and again independently against
a second, different account (jadestar1864, same SKU, different
household) -- see CHANGELOG.md for both. The hypothesis above is
resolved: this is not just plausible, it works.

Token lifetime is short (~1 hour) — callers should re-run the full login
flow rather than trying to refresh in place; there's no known refresh
endpoint, only re-login.

Also has: CloudCredentials + http_base_auth, carried over from
ha_roomba_plus's already-production cloud_api.py (a third, independent
confirmation source alongside live tests and APK analysis) -- see the
CloudCredentials docstring for details and the limits of this
carry-over.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from json.decoder import JSONDecodeError
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

_USER_AGENT_APP = "iRobot/7.16.2.140449 CFNetwork/1568.100.1.2.1 Darwin/24.0.0"
_APP_ID = "roombapy-prime"

# NEW (this session, prompted by a real "onboarding is slow" field report):
# the full login chain (discovery -> Gigya -> iRobot cloud login) is
# genuinely sequential -- each step needs the previous one's output, so it
# can't be parallelized. But the discovery step itself
# (disc-prod.iot.irobotapi.com/v1/discover/endpoints?country_code=...)
# depends ONLY on country_code -- no username, password, or any other
# per-user data goes into the request, and the response describes static
# service infrastructure (deployment endpoints, Gigya app config), not
# anything user- or session-specific. Caching it is a fundamentally
# different, much lower-risk kind of caching than caching credentials
# would be: nothing sensitive is stored, and it helps EVERY login this
# process makes (not just a one-time config-flow-to-setup handoff) --
# including ha_roomba_plus's own known duplicate-login pattern during
# initial onboarding (config flow validates, then async_setup_entry logs
# in again immediately after), where this cache removes one of the two
# now-redundant discovery round-trips essentially for free.
# In-memory only, keyed by country_code, with a conservative TTL --
# deliberately not indefinite, since this is inherently guessing that
# iRobot's own infrastructure config doesn't change; a bounded TTL means
# a real change is picked up within an hour rather than requiring a
# process restart.
_DISCOVERY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DISCOVERY_CACHE_TTL_SECONDS = 3600.0


async def _get_discovery(session: aiohttp.ClientSession, country_code: str) -> dict[str, Any]:
    """Fetches (or returns a cached copy of) the discovery response for
    this country_code. See _DISCOVERY_CACHE's own comment for why this
    specific response is safe to cache (no per-user data in or out) and
    why that makes it a fundamentally different, lower-risk decision
    than caching login credentials would be."""
    cached = _DISCOVERY_CACHE.get(country_code)
    if cached is not None:
        fetched_at, response = cached
        if time.monotonic() - fetched_at < _DISCOVERY_CACHE_TTL_SECONDS:
            return response

    try:
        async with session.get(_discovery_url(country_code)) as resp:
            if resp.status != 200:
                raise AuthError(f"Endpoint discovery failed: HTTP {resp.status}")
            disc = await resp.json()
    except aiohttp.ClientSSLError as exc:
        _raise_clear_ssl_error(exc)
    except aiohttp.ServerTimeoutError as exc:
        _raise_clear_timeout_error(exc)
    except aiohttp.ClientConnectorError as exc:
        _raise_clear_connection_error(exc)

    _DISCOVERY_CACHE[country_code] = (time.monotonic(), disc)
    return disc


class AuthError(Exception):
    """Raised for any failure in the discovery/Gigya/login chain, with
    the offending stage's raw response attached where available.

    Subclassed below (this session, ha_roomba_plus translation-key
    prep) into more specific categories -- callers that only care
    about "something failed" can keep catching AuthError itself
    (every subclass IS-A AuthError), while callers that need to map
    to different user-facing messages/translation keys (e.g.
    ha_roomba_plus's config_flow.py: "invalid_cloud_credentials" vs
    "cannot_connect") can catch the specific subclass instead of
    string-matching the message text -- fragile, and exactly what
    this change avoids."""

    def __init__(self, message: str, raw_response: Any = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


class AuthCredentialsError(AuthError):
    """The login attempt itself was rejected -- wrong username/password
    (Gigya) or the login was otherwise refused by iRobot's backend
    post-Gigya-success. NOT raised for the "mqtt slot" rate-limit case
    (see AuthRateLimitedError) or for malformed/incomplete responses
    (those stay a plain AuthError -- they indicate a response-shape
    problem, not something a user did wrong)."""


class AuthRateLimitedError(AuthError):
    """iRobot's backend rejected the login due to too many active app
    sessions/tokens (the real, confirmed "mqtt slot" failure mode --
    see _login_irobot()). Distinct from AuthCredentialsError: telling
    someone to re-check their password when the actual fix is "close
    the iRobot app and try again" would be actively misleading."""


class AuthSSLError(AuthError):
    """TLS/certificate verification failure -- see
    _raise_clear_ssl_error()."""


class AuthConnectionError(AuthError):
    """Could not establish a connection at all (DNS failure, connection
    refused, network unreachable) -- see _raise_clear_connection_error().
    Deliberately does NOT claim to know whether this is iRobot's fault
    or the caller's own network, unlike AuthSSLError's confident
    "definitely temporary, definitely not you" framing -- that
    confidence isn't justified here."""


class AuthTimeoutError(AuthError):
    """Request was sent but no response came back in time -- see
    _raise_clear_timeout_error()."""


def _raise_clear_ssl_error(exc: aiohttp.ClientSSLError) -> None:
    """Re-raise an aiohttp SSL/certificate failure as a clear
    AuthSSLError instead of letting the raw aiohttp exception bubble
    up as an opaque "unknown error occurred".

    NEW (V4/Prime prep, ha_roomba_plus login consolidation). Carried
    over from ha_roomba_plus's cloud_api.py::_raise_clear_ssl_error()
    (v3.5.0 bug-hunt fix, real-world report from wecoyote5: iRobot's
    own disc-prod.iot.irobotapi.com TLS certificate briefly expired on
    their end -- not a bug in the calling code, and not something a
    user can fix locally). Belongs here rather than only in
    ha_roomba_plus: every consumer of this library hits the exact same
    endpoints, including chairstacker/jadestar1864 running the
    standalone verify-* scripts directly, not just through Roomba+.

    Deliberately does NOT offer any way to skip/ignore certificate
    verification -- that would remove protection against
    man-in-the-middle attacks for every future connection this
    library ever makes, to work around a problem that is both
    temporary (resolves once iRobot renews their certificate) and
    outside any caller's control either way."""
    raise AuthSSLError(
        "Could not verify iRobot's cloud server certificate. This is "
        "almost always a temporary problem on iRobot's servers (an "
        "expired or currently-renewing TLS certificate), not something "
        "wrong with your setup -- it should resolve on its own within a "
        "few hours."
    ) from exc


def _raise_clear_connection_error(exc: aiohttp.ClientConnectorError) -> None:
    """Re-raise a connection failure (DNS, connection refused, network
    unreachable) as a clear AuthConnectionError.

    NEW (this session). Unlike _raise_clear_ssl_error(), deliberately
    does NOT claim confident fault attribution -- a ClientConnectorError
    genuinely could be either iRobot's servers being unreachable or the
    caller's own network/DNS being down, and there's no way to tell
    which from this exception alone."""
    raise AuthConnectionError(
        "Could not connect to iRobot's cloud servers. This could be a "
        "temporary problem with iRobot's servers, or with your own "
        "internet connection -- check that other internet-dependent "
        "services are working, and try again in a few minutes."
    ) from exc


def _raise_clear_timeout_error(exc: BaseException) -> None:
    """Re-raise a request timeout as a clear AuthTimeoutError.

    NEW (this session). Accepts BaseException rather than a specific
    timeout type since both aiohttp.ServerTimeoutError and plain
    asyncio.TimeoutError are plausible here depending on exactly where
    the timeout occurs -- the message is the same either way."""
    raise AuthTimeoutError(
        "iRobot's cloud servers took too long to respond. This is "
        "usually temporary -- please try again in a few minutes."
    ) from exc


@dataclass(frozen=True)
class CloudCredentials:
    """AWS Cognito credentials for signing REST calls (AWS SigV4) --
    separate from ConnectionToken, which is for the AWS IoT MQTT
    Custom Authorizer path. Two independent credential sets from the
    same login response (under the "credentials" key).

    Field names (AccessKeyId, SecretKey, SessionToken, CognitoId,
    Expiration) carried over 1:1 from ha_roomba_plus's already-
    production cloud_api.py -- used there since version 3.x to sign
    the Classic-protocol REST endpoints (/v1/{blid}/pmaps,
    /v1/{blid}/missionhistory, etc.). That's a third, independent
    confirmation source (alongside live tests and APK analysis) --
    BUT: never tested against a p2maps endpoint or a Prime/V4 account,
    only against the Classic REST endpoints. Whether Prime needs the
    same signing at all is a carry-over assumption (see rest_client.py),
    not a confirmed fact.

    Expiration is an ISO-8601 string ("2026-07-10T17:29:39+00:00"),
    NOT a Unix-epoch int like ConnectionToken.expires -- a different
    format convention for the same login response payload, carried
    over unchanged from the original code.

    UPDATE (session 52): secret_key/session_token are now `repr=False`
    -- a genuine, pre-existing gap found while adding a similarly
    credential-bearing new model (RobotLoginEntry). Without this, the
    default dataclass repr would print the full secret key/session
    token in plain text on any accidental print()/log/exception
    traceback involving this object -- exactly the kind of thing this
    project has otherwise been careful about (e.g. never logging full
    tokens elsewhere), just missed here until now."""

    access_key_id: str
    secret_key: str = field(repr=False)
    session_token: str = field(repr=False)
    cognito_id: str
    expiration: datetime | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CloudCredentials:
        expiration_raw = data.get("Expiration")
        expiration = None
        if expiration_raw:
            try:
                expiration = datetime.fromisoformat(expiration_raw)
            except ValueError:
                expiration = None
        return cls(
            access_key_id=data["AccessKeyId"],
            secret_key=data["SecretKey"],
            session_token=data["SessionToken"],
            cognito_id=data["CognitoId"],
            expiration=expiration,
        )

    @property
    def region(self) -> str:
        """Extracted from CognitoId (format "region:uuid") -- confirmed
        pattern from cloud_api.py's _aws_get()."""
        return self.cognito_id.split(":")[0]


@dataclass(frozen=True)
class ConnectionToken:
    """One entry from the login response's connection_tokens list —
    everything needed to open an AWS IoT Custom Authorizer connection.

    Confirmation levels differ by field, deliberately reflected below:
      - client_id, iot_token, iot_signature, iot_authorizer_name: confirmed
        both in live-captured responses AND as literal strings in Prime's
        native library (liblegacyCore.so). Required — a KeyError here
        means something is fundamentally wrong with the response shape,
        not a minor field difference.
      - expires, devices: confirmed present in live-captured Classic/
        EPHEMERAL responses, but never specifically searched for/found
        as native strings in Prime's binary the way the other four were.
        Treated defensively here (.get() with a safe default) rather
        than required, since a Prime/V4 response's exact shape for these
        two fields hasn't been verified — don't let a missing/differently
        -shaped field here be fatal for something otherwise usable.

    UPDATE (session 52): iot_token/iot_signature are now `repr=False`
    -- same fix, same reason as CloudCredentials' secret_key/
    session_token (see that class' docstring). These are genuine
    connection credentials and shouldn't appear in a default repr.
    """

    client_id: str
    iot_token: str = field(repr=False)
    iot_signature: str = field(repr=False)
    iot_authorizer_name: str
    expires: int | None
    devices: list[str]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ConnectionToken:
        return cls(
            client_id=data["client_id"],
            iot_token=data["iot_token"],
            iot_signature=data["iot_signature"],
            iot_authorizer_name=data["iot_authorizer_name"],
            expires=data.get("expires"),
            devices=list(data.get("devices") or []),
        )

    def seconds_until_expiry(self, now: float | None = None) -> float | None:
        """None if expires is unknown (see the class docstring for why
        that field is defensive rather than required) -- callers that
        want to proactively refresh (see mqtt_client.py's
        seconds_until_token_refresh_due()) must treat None as "can't
        schedule a refresh", not as "never expires"."""
        if self.expires is None:
            return None
        current = now if now is not None else time.time()
        return self.expires - current


@dataclass(frozen=True)
class RobotCapabilities:
    """NEW (session 52). CONFIRMED via
    Robot$Capabilities$$serializer's <clinit>: binFullDetect, addOnHw,
    oMode, pose, ota, multiPass. Nested inside RobotLoginEntry.cap."""

    bin_full_detect: Any | None = None
    add_on_hw: Any | None = None
    o_mode: Any | None = None
    pose: Any | None = None
    ota: Any | None = None
    multi_pass: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotCapabilities:
        return cls(
            bin_full_detect=data.get("binFullDetect"),
            add_on_hw=data.get("addOnHw"),
            o_mode=data.get("oMode"),
            pose=data.get("pose"),
            ota=data.get("ota"),
            multi_pass=data.get("multiPass"),
        )


@dataclass(frozen=True)
class RobotDigitalCapabilities:
    """NEW (session 52). CONFIRMED via
    Robot$DigitalCapabilities$$serializer's <clinit>: smartClean.
    Nested inside RobotLoginEntry.digi_cap."""

    smart_clean: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotDigitalCapabilities:
        return cls(smart_clean=data.get("smartClean"))


@dataclass(frozen=True)
class RobotLoginEntry:
    """NEW (session 52) -- REPLACES the previous completely-unmodeled
    `dict[str, Any]` shape of `LoginResult.robots`' per-BLID entries.
    Found while doing a broader sweep of the `foundation/models`
    package (a different one from `missioncommand`/`maps` covered by
    prior sessions) -- CONFIRMED via `Robot$$serializer`'s `<clinit>`:
    id, password, sku, softwareVer, name, cap (RobotCapabilities),
    digiCap (RobotDigitalCapabilities), svcDeplId, user_cert.

    `cap`/`digiCap` matching the exact top-level keys already seen in
    real `get_state()` capture data (chairstacker's account) is a nice
    independent cross-check that this is genuinely the same concept.

    SECURITY NOTE: `password`/`user_cert` are genuine credential
    material for this specific robot (distinct from the account-level
    CloudCredentials/ConnectionToken) -- both marked `repr=False`,
    following the same fix applied to CloudCredentials/ConnectionToken
    in this same session, for the same reason (a default dataclass
    repr would otherwise print them in plain text on any accidental
    print()/log/traceback)."""

    robot_id: str | None = None
    password: str | None = field(default=None, repr=False)
    sku: str | None = None
    software_version: str | None = None
    name: str | None = None
    cap: RobotCapabilities | None = None
    digi_cap: RobotDigitalCapabilities | None = None
    svc_deployment_id: str | None = None
    user_cert: str | None = field(default=None, repr=False)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotLoginEntry:
        cap_raw = data.get("cap")
        digi_cap_raw = data.get("digiCap")
        return cls(
            robot_id=data.get("id"),
            password=data.get("password"),
            sku=data.get("sku"),
            software_version=data.get("softwareVer"),
            name=data.get("name"),
            cap=RobotCapabilities.from_json(cap_raw) if isinstance(cap_raw, dict) else None,
            digi_cap=RobotDigitalCapabilities.from_json(digi_cap_raw) if isinstance(digi_cap_raw, dict) else None,
            svc_deployment_id=data.get("svcDeplId"),
            user_cert=data.get("user_cert"),
        )


@dataclass(frozen=True)
class LoginResult:
    """Everything extracted from a successful login — the raw response
    is kept too, since callers may want fields this class doesn't
    explicitly model (e.g. per-robot capability/state data).

    http_base is carried through from the discovery response (the same
    value used internally for the /v2/login POST). http_base_auth is a
    SEPARATE field (see rest_client.py) -- confirmed pattern from
    ha_roomba_plus's cloud_api.py: httpBase is for /v2/login only,
    httpBaseAuth is the base for all authenticated data endpoints. Using
    http_base for both (an earlier mistake in this module) would be
    wrong for anything beyond login itself.

    credentials: AWS Cognito credentials for SigV4-signing REST calls
    (see CloudCredentials) -- required, not optional, mirroring
    cloud_api.py's "validate at the gate" lesson (a response missing
    these should fail loudly at login(), not with a confusing KeyError
    deep inside a later REST call).

    irbt_topic_prefix / iot_topic_prefix: CONFIRMED (session 43, see
    below) -- this field's long uncertainty is resolved. Needed to
    build MQTT topics outside the shadow system (mission commands via
    cmd_topic(), the live map topic via livemap_topic()).

    UPDATE (session 36): traced the two underlying native constants
    (core::ServiceDiscoveryImpl::kIotTopicPrefixFieldName /
    kIrbtTopicPrefixFieldName) further via native disassembly. Found
    them used as key arguments to a generic
    `AccountServiceImpl::sendUserRequest(key, callback)` call inside
    `onAccountInfoRefreshed()`, right alongside near-identical
    conditional checks for account country/locale/notification-center/
    commercial-messages settings -- a pattern that reads more like "sync
    this one account attribute via its own request if a pending-change
    flag is set" than "read this key out of the discovery response
    body". This is new, genuine context, but doesn't resolve the
    original question -- if anything, it opens a competing hypothesis
    (these values might come from a follow-up account-info fetch,
    not from ServiceDiscoveryData/login discovery directly) that
    wasn't previously considered. The literal JSON key string itself
    remains unfound either way (it's stored in a std::string bss
    global, filled in by a static initializer that couldn't be
    isolated among the many other things AccountServiceImpl's
    translation unit initializes at load time). Still needs either a
    real traffic capture or a substantially deeper native trace to
    resolve -- not further pursued this session.

    UPDATE (session 39): the underlying CONCEPT and its NECESSITY are
    now much more strongly evidenced, even though the literal JSON
    field name here is still unconfirmed. A live test (chairstacker)
    showed every mission command sent via update_shadow() (the classic
    shadow -- this library's previous best guess for mission control)
    timing out with zero response. Independently, this library's own
    native disassembly (objdump on libcorebase.so) found the literal
    format string "/things/%s/cmd" -- a topic family entirely separate
    from the shadow system, requiring exactly this kind of prefix.
    Separately, a third-party, unaffiliated GitHub project
    (lvigilantecorreo-commits/roomba-v4, MIT-licensed, author reports
    the command actually moving a real robot) documents the same shape
    explicitly: "{irbt_topics}/things/{BLID}/cmd", confirming the
    prefix is genuinely required for mission control, not just the
    live-map topic as previously assumed. This is an external,
    unverified-by-us source, but its topic pattern independently
    matches this library's own native string discovery -- see
    mqtt_client.py's cmd_topic()/publish_cmd() docstrings for the full
    trail and prime_robot.py's send_simple_command() for the new,
    corrected mission-control path built on this. The literal
    discovery-response JSON key itself remains the same long-standing
    guess ("irbtTopicPrefix"/"iotTopicPrefix") -- not resolved by any
    of this, only its importance is now much clearer.

    UPDATE (session 43): DEFINITIVELY RESOLVED. chairstacker's
    diagnostics run (using the new _report_topic_prefix_status()
    reporting from session 41) showed the guessed keys really were
    wrong, and the follow-up --dump-config capture showed the actual
    deployment object in full. The real keys are "irbtTopics" and
    "iotTopics" (plural "Topics", not "TopicPrefix" as guessed --
    close, but not exact). Confirmed real values from a live account:
    `irbtTopics: "v011-irbthbu"`, `iotTopics: "$aws"`. Two things this
    also confirms in passing: (1) "v011" matches the same account's
    `svcDeplId: "v011"` -- the same correlation already suspected from
    session 28's "v007" observation on a different account, now
    confirmed as a general pattern (`irbtTopics ==
    f"{svcDeplId}-irbthbu"`), though the field itself should still be
    read directly rather than reconstructed from svcDeplId. (2) the
    "v011-irbthbu" value is byte-for-byte identical to the example
    value shown in the third-party GitHub project cited in the
    thirty-ninth session's update -- as strong a confirmation as this
    project could hope for that project's corroboration was genuine,
    not coincidental. `login()` updated to read the correct keys.

    UPDATE (session 52): a fourth, independent confirmation, this time
    directly from the app's own bytecode rather than live/external
    data. A systematic `$$serializer` scan (the same technique behind
    most of this project's other confirmed models) found
    `DiscoveryResponse$Deployment$$serializer`, whose confirmed fields
    include `iotTopics`/`irbtTopics` -- an exact, direct bytecode match
    for the field names chairstacker's real account had already
    settled. This closes the loop about as completely as this kind of
    question can be closed: live account data, an independent
    third-party project, and the app's own compiled source all agree."""

    mqtt_endpoint: str
    http_base: str
    http_base_auth: str
    credentials: CloudCredentials
    robots: dict[str, RobotLoginEntry]
    connection_tokens: list[ConnectionToken]
    raw: dict[str, Any]
    deployment: dict[str, Any] = field(default_factory=dict)
    """NEW (session 41). The raw discovery-response deployment object
    (`disc["deployments"][disc["current_deployment"]]`) -- previously a
    local variable inside login(), discarded after use, meaning there was
    no way to inspect it even when irbt_topic_prefix/iot_topic_prefix
    guessing turned out wrong. A live test (chairstacker) confirmed
    exactly that: both guessed keys came back missing. Captured here so
    diagnostics.py can report the actual keys present, closing the loop
    instead of guessing again without evidence."""
    irbt_topic_prefix: str | None = None
    iot_topic_prefix: str | None = None

    def primary_token(self) -> ConnectionToken:
        if not self.connection_tokens:
            raise AuthError("Login succeeded but no connection_tokens were returned", self.raw)
        return self.connection_tokens[0]

    def token_for_blid(self, blid: str) -> ConnectionToken:
        """Finds the connection_tokens entry whose devices list actually
        covers this blid, falling back to primary_token() if none match.

        This matters for multi-robot accounts: connection_tokens[0]
        isn't necessarily the token for the robot the caller cares
        about. Both real fixtures captured so far have exactly one
        token covering exactly one device, so this distinction has
        never actually been exercised against real data -- multi-robot
        accounts are plausible but unconfirmed."""
        for token in self.connection_tokens:
            if blid in token.devices:
                return token
        return self.primary_token()

    def primary_blid(self) -> str:
        if not self.robots:
            raise AuthError("Login succeeded but no robots were returned", self.raw)
        return next(iter(self.robots.keys()))


def _discovery_url(country_code: str) -> str:
    return f"https://disc-prod.iot.irobotapi.com/v1/discover/endpoints?country_code={country_code}"


async def login(
    session: aiohttp.ClientSession,
    username: str,
    password: str,
    country_code: str,
    app_id: str = _APP_ID,
) -> LoginResult:
    """Run the full discovery -> Gigya -> iRobot cloud login chain.

    Raises AuthError at whichever stage fails, with that stage's raw
    response attached for diagnostics.

    UPDATE (this session): the discovery step is now served from an
    in-memory cache when a recent-enough one exists for this
    country_code -- see _get_discovery()'s own docstring for why this
    specific response (unlike credentials) is safe to cache at all.
    """
    disc = await _get_discovery(session, country_code)

    try:
        deployment = disc["deployments"][disc["current_deployment"]]
        gigya_cfg = disc["gigya"]
    except KeyError as exc:
        raise AuthError(f"Unexpected discovery response shape, missing {exc}", disc) from exc

    mqtt_endpoint = deployment.get("mqtt") or deployment.get("mqttApp") or deployment.get("mqttAts")
    if not mqtt_endpoint:
        raise AuthError("No mqtt endpoint field found in discovery response", disc)

    http_base_auth = deployment.get("httpBaseAuth")
    if not http_base_auth:
        raise AuthError("No httpBaseAuth field found in discovery response", disc)

    gigya_result = await _login_gigya(session, gigya_cfg, username, password)
    login_result = await _login_irobot(session, deployment["httpBase"], gigya_result, app_id)

    tokens_raw = login_result.get("connection_tokens") or []
    connection_tokens = [ConnectionToken.from_json(t) for t in tokens_raw]

    # Validate-at-the-gate (lesson from ha_roomba_plus's cloud_api.py):
    # a response missing credentials should fail loudly here, not with
    # a confusing KeyError deep inside a later REST call.
    creds_raw = login_result.get("credentials")
    if not creds_raw:
        raise AuthError("No credentials in iRobot login response", login_result)
    for key in ("CognitoId", "AccessKeyId", "SecretKey", "SessionToken"):
        if key not in creds_raw:
            raise AuthError(f"Missing '{key}' in iRobot credentials response", login_result)
    credentials = CloudCredentials.from_json(creds_raw)

    raw_robots = login_result.get("robots") or {}
    result = LoginResult(
        mqtt_endpoint=mqtt_endpoint,
        http_base=deployment["httpBase"],
        http_base_auth=http_base_auth,
        credentials=credentials,
        robots={blid: RobotLoginEntry.from_json(v) for blid, v in raw_robots.items()},
        connection_tokens=connection_tokens,
        raw=login_result,
        deployment=deployment,
        # Best-guess field names (see LoginResult docstring) -- .get(),
        # not a gate failure, since it's too uncertain to enforce strictly.
        # CONFIRMED (session 43, chairstacker): real keys are "irbtTopics"/
        # "iotTopics" (plural "Topics", not "TopicPrefix" as previously
        # guessed) -- see LoginResult's docstring for the full story.
        irbt_topic_prefix=deployment.get("irbtTopics"),
        iot_topic_prefix=deployment.get("iotTopics"),
    )
    _LOGGER.info("roombapy-prime: authenticated, %d robot(s) found", len(result.robots))
    return result


async def _login_gigya(
    session: aiohttp.ClientSession,
    gigya_cfg: dict[str, Any],
    username: str,
    password: str,
) -> dict[str, str]:
    base = f"https://accounts.{gigya_cfg['datacenter_domain']}/accounts."
    payload = {
        "loginMode": "standard",
        "loginID": username,
        "password": password,
        "include": "profile,data,emails,subscriptions,preferences,",
        "includeUserInfo": "true",
        "targetEnv": "mobile",
        "source": "showScreenSet",
        "sdk": "ios_swift_1.3.0",
        "sessionExpiration": "-2",
        "apikey": gigya_cfg["api_key"],
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": _USER_AGENT_APP,
    }
    try:
        async with session.post(f"{base}login", headers=headers, data=urllib.parse.urlencode(payload)) as resp:
            text = await resp.text()
    except aiohttp.ClientSSLError as exc:
        _raise_clear_ssl_error(exc)
    except aiohttp.ServerTimeoutError as exc:
        _raise_clear_timeout_error(exc)
    except aiohttp.ClientConnectorError as exc:
        _raise_clear_connection_error(exc)

    try:
        result = json.loads(text)
    except JSONDecodeError as exc:
        raise AuthError(f"Invalid Gigya response (not JSON): {text[:300]}") from exc

    if result.get("errorCode", 0) != 0:
        raise AuthCredentialsError(f"Gigya login failed: {result.get('errorMessage', result)}", result)

    return {
        "uid": result["UID"],
        "signature": result["UIDSignature"],
        "timestamp": result["signatureTimestamp"],
    }


async def _login_irobot(
    session: aiohttp.ClientSession,
    http_base: str,
    gigya: dict[str, str],
    app_id: str,
) -> dict[str, Any]:
    payload = {
        "app_id": app_id,
        "app_info": {
            "device_id": app_id,
            "device_name": "python",
            "language": "en_US",
            "version": "7.16.2",
        },
        "assume_robot_ownership": "0",
        "authorizer_params": {"devices_per_token": 5},
        "gigya": {
            "signature": gigya["signature"],
            "timestamp": gigya["timestamp"],
            "uid": gigya["uid"],
        },
        # Confirmed already present in cloud_api.py's existing payload —
        # this is what makes connection_tokens appear in the response at
        # all; omitting it silently drops the whole Custom Authorizer path.
        "multiple_authorizer_token_support": True,
        "push_info": {
            "platform": "APNS",
            "push_token": "0" * 64,
            "supported_push_types": ["cr", "cse", "bf", "ae", "pm", "te", "dt"],
        },
        "skip_ownership_check": "0",
    }
    try:
        async with session.post(
            f"{http_base}/v2/login",
            headers={"Content-Type": "application/json"},
            json=payload,
        ) as resp:
            text = await resp.text()
    except aiohttp.ClientSSLError as exc:
        _raise_clear_ssl_error(exc)
    except aiohttp.ServerTimeoutError as exc:
        _raise_clear_timeout_error(exc)
    except aiohttp.ClientConnectorError as exc:
        _raise_clear_connection_error(exc)

    try:
        result = json.loads(text)
    except JSONDecodeError as exc:
        raise AuthError(f"Invalid iRobot login response (not JSON): {text[:300]}") from exc

    if result.get("errorCode"):
        msg = result.get("errorMessage") or str(result)
        # Known, real failure mode -- carried over 1:1 from cloud_api.py
        # (confirmed there for the same /v2/login endpoint). Its own
        # category (AuthRateLimitedError), not AuthCredentialsError --
        # telling someone to re-check their password when the actual
        # fix is "close the iRobot app and try again" would be
        # actively misleading.
        if "mqtt slot" in msg.lower():
            raise AuthRateLimitedError(
                f"Cloud auth rate-limited. Close the iRobot app and try again. ({msg})", result
            )
        raise AuthCredentialsError(f"iRobot cloud login failed: {msg}", result)

    return result
