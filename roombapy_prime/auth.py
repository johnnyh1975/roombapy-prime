"""
roombapy_prime.auth — Gigya login → iRobot cloud login → Custom Authorizer
connection tokens.

Extracted and cleaned up from validated, live-tested standalone scripts
(stage1-4, smart_tier_shadow_check.py). Confirmed working against:
  - one EPHEMERAL-tier robot (900-series, Classic app account)
  - two SMART-tier robots (i7-series, Classic app account)
Native binary analysis of the Prime app (liblegacyCore.so) confirms the
same field names (connection_tokens, iot_token, iot_signature,
iot_authorizer_name, client_id, robots) — but this has NOT been live
verified against an actual Prime/V4 account yet. Treat V4 usage as
plausible, not confirmed, until tested.

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


class AuthError(Exception):
    """Raised for any failure in the discovery/Gigya/login chain, with
    the offending stage's raw response attached where available."""

    def __init__(self, message: str, raw_response: Any = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


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
    """
    async with session.get(_discovery_url(country_code)) as resp:
        if resp.status != 200:
            raise AuthError(f"Endpoint discovery failed: HTTP {resp.status}")
        disc = await resp.json()

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
    async with session.post(f"{base}login", headers=headers, data=urllib.parse.urlencode(payload)) as resp:
        text = await resp.text()

    try:
        result = json.loads(text)
    except JSONDecodeError as exc:
        raise AuthError(f"Invalid Gigya response (not JSON): {text[:300]}") from exc

    if result.get("errorCode", 0) != 0:
        raise AuthError(f"Gigya login failed: {result.get('errorMessage', result)}", result)

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
    async with session.post(
        f"{http_base}/v2/login",
        headers={"Content-Type": "application/json"},
        json=payload,
    ) as resp:
        text = await resp.text()

    try:
        result = json.loads(text)
    except JSONDecodeError as exc:
        raise AuthError(f"Invalid iRobot login response (not JSON): {text[:300]}") from exc

    if result.get("errorCode"):
        msg = result.get("errorMessage") or str(result)
        # Known, real failure mode -- carried over 1:1 from cloud_api.py
        # (confirmed there for the same /v2/login endpoint).
        if "mqtt slot" in msg.lower():
            msg = f"Cloud auth rate-limited. Close the iRobot app and try again. ({msg})"
        raise AuthError(f"iRobot cloud login failed: {msg}", result)

    return result
