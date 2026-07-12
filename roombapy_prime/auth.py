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

Seit heute: CloudCredentials + http_base_auth, uebernommen aus
ha_roomba_plus's bereits produktiv laufender cloud_api.py (dritte,
unabhaengige Bestaetigungsquelle neben Live-Tests und APK-Analyse) --
siehe CloudCredentials-Docstring fuer Details und Grenzen dieser
Uebertragung.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
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
    """AWS-Cognito-Zugangsdaten zum Signieren von REST-Aufrufen (AWS
    SigV4) -- getrennt von ConnectionToken, das fuer den AWS-IoT-MQTT-
    Custom-Authorizer-Weg ist. Zwei unabhaengige Zugangsdaten-Saetze aus
    derselben Login-Antwort (unter dem "credentials"-Schluessel).

    Feldnamen (AccessKeyId, SecretKey, SessionToken, CognitoId,
    Expiration) 1:1 uebernommen aus ha_roomba_plus's bereits produktiv
    laufender cloud_api.py -- dort seit Version 3.x genutzt, um die
    Classic-Protokoll-REST-Endpunkte (/v1/{blid}/pmaps,
    /v1/{blid}/missionhistory, etc.) zu signieren. Das ist eine
    dritte, unabhaengige Bestaetigungsquelle (neben Live-Tests und
    APK-Analyse) -- ABER: nie gegen einen p2maps-Endpunkt oder ein
    Prime/V4-Konto getestet, nur gegen die Classic-REST-Endpunkte.
    Ob Prime dieselbe Signierung ueberhaupt braucht, ist eine
    Uebertragungs-Annahme (siehe rest_client.py), keine bestaetigte
    Tatsache.

    Expiration ist ein ISO-8601-String ("2026-07-10T17:29:39+00:00"),
    NICHT ein Unix-Epoch-Int wie ConnectionToken.expires -- andere
    Formatkonvention fuer denselben Login-Antwort-Payload, im
    Original-Code unveraendert uebernommen."""

    access_key_id: str
    secret_key: str
    session_token: str
    cognito_id: str
    expiration: datetime | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CloudCredentials":
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
        """Aus CognitoId extrahiert (Format "region:uuid") -- bestaetigtes
        Muster aus cloud_api.py's _aws_get()."""
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
    """

    client_id: str
    iot_token: str
    iot_signature: str
    iot_authorizer_name: str
    expires: int | None
    devices: list[str]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ConnectionToken":
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

    irbt_topic_prefix / iot_topic_prefix: NEU, UNSICHER. Noetig, um
    MQTT-Topics ausserhalb des Shadow-Systems zu bilden (z.B. den
    Live-Map-Topic, siehe mqtt_client.py's livemap_topic()) --
    bestaetigt als KONZEPT (core::MQTTTopicResolverAdapter.resolve()
    liefert "{prefix}/{identifier}", der Prefix kommt aus einer
    "ServiceDiscoveryData"-Struktur), aber die JSON-Feldnamen hier
    ("irbtTopicPrefix"/"iotTopicPrefix") sind ein Best-Guess aus den
    nativen Getter-Namen (getIrbtTopicPrefix()/getIotTopicPrefix()),
    NICHT aus einer echten JSON-Antwort gelesen. Optional (None wenn
    nicht vorhanden/falsch benannt) -- kein Gate-Fehler beim Login,
    da die Unsicherheit zu hoch ist, um das hart zu erzwingen."""

    mqtt_endpoint: str
    http_base: str
    http_base_auth: str
    credentials: CloudCredentials
    robots: dict[str, dict[str, Any]]
    connection_tokens: list[ConnectionToken]
    raw: dict[str, Any]
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

    result = LoginResult(
        mqtt_endpoint=mqtt_endpoint,
        http_base=deployment["httpBase"],
        http_base_auth=http_base_auth,
        credentials=credentials,
        robots=login_result.get("robots") or {},
        connection_tokens=connection_tokens,
        raw=login_result,
        # Best-guess field names (siehe LoginResult-Docstring) -- .get(),
        # kein Gate-Fehler, da unsicher genug, um nicht hart zu erzwingen.
        irbt_topic_prefix=deployment.get("irbtTopicPrefix"),
        iot_topic_prefix=deployment.get("iotTopicPrefix"),
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
        # Bekannter, echter Fehlermodus -- 1:1 aus cloud_api.py uebernommen
        # (dort fuer denselben /v2/login-Endpunkt bestaetigt).
        if "mqtt slot" in msg.lower():
            msg = f"Cloud auth rate-limited. Close the iRobot app and try again. ({msg})"
        raise AuthError(f"iRobot cloud login failed: {msg}", result)

    return result
