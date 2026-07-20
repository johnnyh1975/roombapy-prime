"""Tests for roombapy_prime.auth — ConnectionToken parsing plus the
login() orchestration chain.

Real-fixture tests use anonymized-but-structurally-real captures from
Classic-protocol robots (EPHEMERAL 980, SMART-tier i7x2). Synthetic
tests (clearly labeled) cover edge cases not present in any real
capture yet — missing optional fields, missing required fields.

login()/_login_gigya()/_login_irobot() ARE covered below (thirteenth
session, systematic review finding -- auth.py stood at only 55% test
coverage, with almost the entire login() chain including all "fail
loudly" validation gates unused). A _FakeSequentialSession replays the
three sequential HTTP calls (discovery GET, Gigya POST, iRobot POST)
in exactly the order login() actually triggers them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from roombapy_prime.auth import (
    AuthConnectionError,
    AuthCredentialsError,
    AuthError,
    AuthRateLimitedError,
    AuthSSLError,
    AuthTimeoutError,
    ConnectionToken,
    login,
)


def _load(fixtures_dir: Path, name: str) -> dict:
    return json.loads((fixtures_dir / name).read_text())


def test_connection_token_from_real_ephemeral_fixture(fixtures_dir: Path) -> None:
    data = _load(fixtures_dir, "login_response_ephemeral.json")
    token = ConnectionToken.from_json(data["connection_tokens"][0])

    assert token.client_id == "app-IOS-fixture-example-00000000"
    assert token.iot_authorizer_name == "ElPaso256Login-AspenIoTAuthorizer-FAKESUFFIX"
    assert token.expires == 1783704579
    assert token.devices == ["0000000000000000"]


def test_connection_token_from_real_smart_tier_fixture(fixtures_dir: Path) -> None:
    data = _load(fixtures_dir, "login_response_smart_tier.json")
    token = ConnectionToken.from_json(data["connection_tokens"][0])

    assert token.client_id == "app-IOS-fixture-example-11111111"
    assert token.devices == ["1111111111111111"]
    assert token.expires == 1783704579


def test_connection_token_missing_optional_fields_is_defensive() -> None:
    """SYNTHETIC — no real capture is missing these fields (both real
    fixtures have expires/devices present). This only confirms the
    documented defensive .get()-handling in auth.py actually holds,
    in case a future (e.g. V4) response ever omits them."""
    minimal = {
        "client_id": "x",
        "iot_token": "t",
        "iot_signature": "s",
        "iot_authorizer_name": "a",
    }
    token = ConnectionToken.from_json(minimal)
    assert token.expires is None
    assert token.devices == []


def test_connection_token_missing_required_field_raises_keyerror() -> None:
    """SYNTHETIC — confirms the four fields documented as "required, not
    a minor field difference if missing" (see auth.py docstring) really
    do raise loudly rather than silently producing a broken token."""
    incomplete = {"iot_token": "t", "iot_signature": "s", "iot_authorizer_name": "a"}
    with pytest.raises(KeyError):
        ConnectionToken.from_json(incomplete)


def test_tier_signal_cap_pose_differs_between_fixtures(fixtures_dir: Path) -> None:
    """Not strictly an auth.py test, but pins down the fact the whole
    tier-detection logic (see CLOUD_SHADOW_PUSH_FINDINGS.md) hinges on:
    cap.pose=1 for EPHEMERAL, cap.pose=2 for SMART-tier. Regression
    guard against this fixture pair silently drifting apart."""
    ephemeral = _load(fixtures_dir, "login_response_ephemeral.json")
    smart = _load(fixtures_dir, "login_response_smart_tier.json")

    (_, robot_ephemeral), = ephemeral["robots"].items()
    (_, robot_smart), = smart["robots"].items()

    assert robot_ephemeral["cap"]["pose"] == 1
    assert robot_smart["cap"]["pose"] == 2


# --- ConnectionToken.seconds_until_expiry / LoginResult.token_for_blid --

def test_seconds_until_expiry_with_known_expires() -> None:
    token = ConnectionToken(
        client_id="x", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=2000, devices=[],
    )
    assert token.seconds_until_expiry(now=1000) == 1000


def test_seconds_until_expiry_unknown_returns_none() -> None:
    token = ConnectionToken(
        client_id="x", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=None, devices=[],
    )
    assert token.seconds_until_expiry() is None


def test_token_for_blid_finds_matching_token() -> None:
    from roombapy_prime.auth import CloudCredentials, LoginResult

    token_a = ConnectionToken(
        client_id="a", iot_token="t", iot_signature="s",
        iot_authorizer_name="auth", expires=None, devices=["BLID_A"],
    )
    token_b = ConnectionToken(
        client_id="b", iot_token="t", iot_signature="s",
        iot_authorizer_name="auth", expires=None, devices=["BLID_B"],
    )
    creds = CloudCredentials(
        access_key_id="ak", secret_key="sk", session_token="st", cognito_id="us-east-1:0",
    )
    result = LoginResult(
        mqtt_endpoint="e", http_base="h", http_base_auth="ha", credentials=creds,
        robots={"BLID_A": {}, "BLID_B": {}},
        connection_tokens=[token_a, token_b], raw={},
    )

    assert result.token_for_blid("BLID_B") is token_b
    assert result.token_for_blid("BLID_A") is token_a


def test_token_for_blid_falls_back_to_primary_when_no_match() -> None:
    """SYNTHETIC -- no real capture with multiple non-matching tokens
    exists; both real fixtures have exactly one token covering exactly
    one device (see docstring in auth.py's token_for_blid)."""
    from roombapy_prime.auth import CloudCredentials, LoginResult

    token_a = ConnectionToken(
        client_id="a", iot_token="t", iot_signature="s",
        iot_authorizer_name="auth", expires=None, devices=["OTHER_BLID"],
    )
    creds = CloudCredentials(
        access_key_id="ak", secret_key="sk", session_token="st", cognito_id="us-east-1:0",
    )
    result = LoginResult(
        mqtt_endpoint="e", http_base="h", http_base_auth="ha", credentials=creds,
        robots={},
        connection_tokens=[token_a], raw={},
    )

    assert result.token_for_blid("UNKNOWN_BLID") is token_a


# --- CloudCredentials (from ha_roomba_plus's cloud_api.py cross-reference) --

def test_cloud_credentials_from_real_ephemeral_fixture(fixtures_dir: Path) -> None:
    from roombapy_prime.auth import CloudCredentials

    data = _load(fixtures_dir, "login_response_ephemeral.json")
    creds = CloudCredentials.from_json(data["credentials"])

    assert creds.access_key_id == "test-access-key-id-not-a-real-aws-key"
    assert creds.cognito_id == "us-east-1:00000000-0000-0000-0000-000000000000"
    assert creds.region == "us-east-1"
    assert creds.expiration is not None
    assert creds.expiration.year == 2026


def test_cloud_credentials_from_real_smart_tier_fixture(fixtures_dir: Path) -> None:
    from roombapy_prime.auth import CloudCredentials

    data = _load(fixtures_dir, "login_response_smart_tier.json")
    creds = CloudCredentials.from_json(data["credentials"])

    assert creds.region == "us-east-1"
    assert creds.session_token


def test_robot_login_entry_from_real_smart_tier_fixture(fixtures_dir: Path) -> None:
    """NEW (session 52) -- RobotLoginEntry (confirmed via
    Robot$$serializer, found while sweeping the foundation/models
    package) against the same real fixture already used for
    CloudCredentials -- this fixture's "robots" entry has genuine,
    anonymized-but-authentic field content matching every confirmed
    key exactly."""
    from roombapy_prime.auth import RobotLoginEntry

    data = _load(fixtures_dir, "login_response_smart_tier.json")
    (blid, robot_raw), = data["robots"].items()
    robot = RobotLoginEntry.from_json(robot_raw)

    assert blid == "1111111111111111"
    assert robot.sku == "i755640"
    assert robot.software_version == "v2.x.x-fixture"
    assert robot.name == "Roomba"
    assert robot.svc_deployment_id == "v007"
    assert robot.cap is not None
    assert robot.cap.bin_full_detect == 2
    assert robot.cap.multi_pass == 2
    assert robot.digi_cap is not None
    # password/user_cert ARE present in the real fixture -- confirm they parse,
    # without asserting their literal value in this test's output (see the
    # repr=False protection on both fields, tested separately below)
    assert robot.password is not None


def test_robot_login_entry_password_and_user_cert_hidden_from_repr() -> None:
    """Regression test for the session-52 security fix: a default
    dataclass repr would otherwise print credential material in plain
    text on any accidental print()/log/traceback."""
    from roombapy_prime.auth import RobotLoginEntry

    robot = RobotLoginEntry.from_json({"password": "SUPERSECRET", "user_cert": "CERTDATA123", "sku": "i7"})

    assert "SUPERSECRET" not in repr(robot)
    assert "CERTDATA123" not in repr(robot)
    assert "i7" in repr(robot)  # non-sensitive fields still show normally
    # the actual values are still accessible, just not in the default repr
    assert robot.password == "SUPERSECRET"
    assert robot.user_cert == "CERTDATA123"


def test_cloud_credentials_missing_expiration_is_defensive() -> None:
    """SYNTHETIC -- both real fixtures have Expiration present. Confirms
    the defensive .get() handling doesn't blow up if a future response
    omits it."""
    from roombapy_prime.auth import CloudCredentials

    creds = CloudCredentials.from_json({
        "AccessKeyId": "a", "SecretKey": "s", "SessionToken": "t",
        "CognitoId": "eu-west-1:xyz",
    })
    assert creds.expiration is None
    assert creds.region == "eu-west-1"


# =========================================================================
# login() chain (thirteenth session -- see docstring above)
# =========================================================================

_DISCOVERY_RESPONSE = {
    "current_deployment": "prod",
    "deployments": {
        "prod": {
            "mqtt": "mqtt.example.invalid",
            "httpBase": "https://api.example.invalid",
            "httpBaseAuth": "https://api-auth.example.invalid",
        }
    },
    "gigya": {"datacenter_domain": "eu1.gigya.com", "api_key": "fake-api-key"},
}

_GIGYA_RESPONSE = {
    "errorCode": 0,
    "UID": "fake-uid",
    "UIDSignature": "fake-signature",
    "signatureTimestamp": "1234567890",
}

_IROBOT_LOGIN_RESPONSE = {
    "credentials": {
        "CognitoId": "us-east-1:fake",
        "AccessKeyId": "AKIDEXAMPLE",
        "SecretKey": "fakesecretkey",
        "SessionToken": "fakesessiontoken",
    },
    "connection_tokens": [],
    "robots": {"BLID123": {}},
}


class _FakeResp:
    def __init__(self, status: int, json_body: dict | None = None, text_body: str | None = None) -> None:
        self.status = status
        self._json_body = json_body
        self._text_body = text_body if text_body is not None else (json.dumps(json_body) if json_body is not None else "")

    async def json(self) -> Any:
        return self._json_body

    async def text(self) -> str:
        return self._text_body

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeSequentialSession:
    """Returns the prepared responses in the order login() actually
    calls them: GET (discovery), POST (Gigya), POST (iRobot). The
    method (get/post) is deliberately not checked -- login() calls
    them in a fixed, known order, that's enough."""

    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: object) -> _FakeResp:
        self.calls.append(f"GET {url}")
        return self._responses.pop(0)

    def post(self, url: str, **kwargs: object) -> _FakeResp:
        self.calls.append(f"POST {url}")
        return self._responses.pop(0)


def _full_success_session() -> _FakeSequentialSession:
    return _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
        ]
    )


@pytest.mark.asyncio
async def test_login_full_success_chain() -> None:
    session = _full_success_session()

    result = await login(session, "user@example.com", "hunter2", "US")

    assert result.mqtt_endpoint == "mqtt.example.invalid"
    assert result.http_base_auth == "https://api-auth.example.invalid"
    assert result.credentials.access_key_id == "AKIDEXAMPLE"
    assert "BLID123" in result.robots
    assert result.robots["BLID123"].robot_id is None  # empty fixture entry, see test below for real fields
    assert len(session.calls) == 3
    assert session.calls[0].startswith("GET")
    assert session.calls[1].startswith("POST")
    assert session.calls[2].startswith("POST")


@pytest.mark.asyncio
async def test_login_reuses_cached_discovery_on_second_call() -> None:
    """NEW (this session, prompted by a real "onboarding is slow" field
    report): the discovery step depends only on country_code, not on
    any per-user data, so it's cached -- unlike credentials, which
    never are. Two full login() calls for the same country_code should
    only hit the discovery endpoint once between them (6 total HTTP
    calls for two full chains, not 3+3=6... wait, 2+3=5: one shared
    discovery GET, then two full Gigya+iRobot POST pairs)."""
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
            # No second discovery response queued -- if login() asks for
            # one anyway, .pop(0) on an empty list raises IndexError,
            # failing this test loudly rather than silently passing.
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
        ]
    )

    first = await login(session, "user@example.com", "hunter2", "US")
    second = await login(session, "user2@example.com", "hunter3", "US")

    assert first.mqtt_endpoint == second.mqtt_endpoint == "mqtt.example.invalid"
    assert len(session.calls) == 5
    get_calls = [c for c in session.calls if c.startswith("GET")]
    assert len(get_calls) == 1, "second login() should have reused the cached discovery response"


@pytest.mark.asyncio
async def test_login_uses_separate_cache_entries_per_country_code() -> None:
    """The cache is keyed by country_code -- a login for a different
    country must NOT reuse another country's cached discovery response
    (different countries can have genuinely different deployment
    endpoints)."""
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),  # separate country -> fresh discovery expected
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
        ]
    )

    await login(session, "user@example.com", "hunter2", "US")
    await login(session, "user@example.com", "hunter2", "DE")

    get_calls = [c for c in session.calls if c.startswith("GET")]
    assert len(get_calls) == 2, "a different country_code must not reuse another country's cached discovery"


@pytest.mark.asyncio
async def test_login_refetches_discovery_after_cache_expires(monkeypatch) -> None:
    """The cache has a bounded TTL, not an indefinite one -- see
    _DISCOVERY_CACHE's own comment for why (a real infrastructure
    change should be picked up within an hour, not require a process
    restart)."""
    from roombapy_prime import auth

    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
        ]
    )

    fake_now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: fake_now[0])

    await login(session, "user@example.com", "hunter2", "US")
    fake_now[0] += auth._DISCOVERY_CACHE_TTL_SECONDS + 1
    await login(session, "user@example.com", "hunter2", "US")

    get_calls = [c for c in session.calls if c.startswith("GET")]
    assert len(get_calls) == 2, "an expired cache entry should trigger a fresh discovery fetch"


@pytest.mark.asyncio
async def test_login_discovery_http_error_raises() -> None:
    session = _FakeSequentialSession([_FakeResp(403)])

    with pytest.raises(AuthError, match="Endpoint discovery failed"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_discovery_missing_mqtt_endpoint_raises() -> None:
    """"Fail loudly, nicht mit einem verwirrenden KeyError tiefer unten" --
    genau der Gate-Fund, den diese Tests eigentlich pruefen sollen."""
    broken_discovery = {
        "current_deployment": "prod",
        "deployments": {"prod": {"httpBase": "https://api.example.invalid", "httpBaseAuth": "https://x"}},
        "gigya": _DISCOVERY_RESPONSE["gigya"],
    }
    session = _FakeSequentialSession([_FakeResp(200, json_body=broken_discovery)])

    with pytest.raises(AuthError, match="No mqtt endpoint"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_discovery_missing_http_base_auth_raises() -> None:
    broken_discovery = {
        "current_deployment": "prod",
        "deployments": {"prod": {"httpBase": "https://api.example.invalid", "mqtt": "mqtt.example.invalid"}},
        "gigya": _DISCOVERY_RESPONSE["gigya"],
    }
    session = _FakeSequentialSession([_FakeResp(200, json_body=broken_discovery)])

    with pytest.raises(AuthError, match="No httpBaseAuth"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_gigya_error_code_raises() -> None:
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps({"errorCode": 403042, "errorMessage": "Invalid login"})),
        ]
    )

    with pytest.raises(AuthCredentialsError, match="Gigya login failed"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_irobot_missing_credentials_raises() -> None:
    """The central "validate at the gate" finding from ha_roomba_plus's
    cloud_api.py, tested here for the first time."""
    response_without_creds = {"connection_tokens": [], "robots": {}}
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(response_without_creds)),
        ]
    )

    with pytest.raises(AuthError, match="No credentials"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_irobot_missing_single_credential_key_raises() -> None:
    incomplete_creds = {
        "credentials": {"CognitoId": "x", "AccessKeyId": "y", "SecretKey": "z"},  # SessionToken missing
        "connection_tokens": [],
        "robots": {},
    }
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(incomplete_creds)),
        ]
    )

    with pytest.raises(AuthError, match="SessionToken"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_irobot_mqtt_slot_rate_limit_gets_friendlier_message() -> None:
    """Confirmed, real failure mode from cloud_api.py -- the message
    is reworded, not just passed through."""
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps({"errorCode": 1, "errorMessage": "no more mqtt slot available"})),
        ]
    )

    with pytest.raises(AuthRateLimitedError, match="rate-limited"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_irobot_generic_error_code_is_credentials_error() -> None:
    """NEW (this session) -- the non-rate-limit branch of the same
    errorCode check must land in AuthCredentialsError, distinct from
    the mqtt-slot case above."""
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps({"errorCode": 42, "errorMessage": "account locked"})),
        ]
    )

    with pytest.raises(AuthCredentialsError, match="account locked"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_irobot_invalid_json_response_raises() -> None:
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body="not valid json{{{"),
        ]
    )

    with pytest.raises(AuthError, match="not JSON"):
        await login(session, "user@example.com", "hunter2", "US")


@pytest.mark.asyncio
async def test_login_topic_prefixes_default_to_none_when_absent() -> None:
    """irbt_topic_prefix/iot_topic_prefix are best-guess field names --
    they're allowed to be absent, without causing login to fail."""
    session = _full_success_session()

    result = await login(session, "user@example.com", "hunter2", "US")

    assert result.irbt_topic_prefix is None
    assert result.iot_topic_prefix is None


@pytest.mark.asyncio
async def test_login_extracts_confirmed_topic_prefixes() -> None:
    """DEFINITIVELY CONFIRMED (session 43, chairstacker) -- regression
    test against ever reverting to the wrong, previously-guessed field
    names ("irbtTopicPrefix"/"iotTopicPrefix"). Real keys are
    "irbtTopics"/"iotTopics" (plural "Topics", not "TopicPrefix").
    Values below are the real, confirmed values from a live account,
    not placeholders -- see auth.py's LoginResult docstring for the
    full story, including the independent confirmation this "v011-
    irbthbu" value gives for the third-party GitHub project cited in
    the thirty-ninth session."""
    discovery_with_confirmed_fields = {
        "current_deployment": "prod",
        "deployments": {
            "prod": {
                "mqtt": "mqtt.example.invalid",
                "httpBase": "https://api.example.invalid",
                "httpBaseAuth": "https://api-auth.example.invalid",
                "irbtTopics": "v011-irbthbu",
                "iotTopics": "$aws",
            }
        },
        "gigya": {"datacenter_domain": "eu1.gigya.com", "api_key": "fake-api-key"},
    }
    session = _FakeSequentialSession(
        [
            _FakeResp(200, json_body=discovery_with_confirmed_fields),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
            _FakeResp(200, text_body=json.dumps(_IROBOT_LOGIN_RESPONSE)),
        ]
    )

    result = await login(session, "user@example.com", "hunter2", "US")

    assert result.irbt_topic_prefix == "v011-irbthbu"
    assert result.iot_topic_prefix == "$aws"
    assert result.deployment["irbtTopics"] == "v011-irbthbu"


# =========================================================================
# SSL certificate error clarity (this session, moved here from
# ha_roomba_plus's cloud_api.py -- see _raise_clear_ssl_error()'s
# docstring for why this belongs in the shared library rather than only
# in the integration: every consumer of this library hits the exact
# same endpoints, including the standalone verify-* scripts chairstacker
# and jadestar1864 run directly, not just through Roomba+).
# =========================================================================


class _NetworkFailingSession:
    """Raises a given exception on the Nth HTTP call (0=discovery GET,
    1=Gigya POST, 2=iRobot POST), returns the prepared responses
    normally before that -- same call-order assumption as
    _FakeSequentialSession above. Generalized (this session) from the
    SSL-only _SSLFailingSession to also cover ClientConnectorError/
    ServerTimeoutError with the same call-order machinery."""

    def __init__(self, fail_at_call: int, exc: BaseException, prior_responses: list[_FakeResp]) -> None:
        self._fail_at_call = fail_at_call
        self._exc = exc
        self._responses = list(prior_responses)
        self._call_count = 0

    def _next(self) -> _FakeResp:
        call_index = self._call_count
        self._call_count += 1
        if call_index == self._fail_at_call:
            raise self._exc
        return self._responses.pop(0)

    def get(self, url: str, **kwargs: object) -> _FakeResp:
        return self._next()

    def post(self, url: str, **kwargs: object) -> _FakeResp:
        return self._next()


def _ssl_error() -> aiohttp.ClientSSLError:
    return aiohttp.ClientSSLError(None, OSError("certificate has expired"))


def _connector_error() -> aiohttp.ClientConnectorError:
    return aiohttp.ClientConnectorError(None, OSError("Name or service not known"))


def _timeout_error() -> aiohttp.ServerTimeoutError:
    return aiohttp.ServerTimeoutError("Connection timeout to host")


@pytest.mark.asyncio
async def test_login_discovery_ssl_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(fail_at_call=0, exc=_ssl_error(), prior_responses=[])

    with pytest.raises(AuthSSLError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert "certificate" in str(excinfo.value).lower()
    assert "temporary" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientSSLError)


@pytest.mark.asyncio
async def test_login_gigya_ssl_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(
        fail_at_call=1,
        exc=_ssl_error(),
        prior_responses=[_FakeResp(200, json_body=_DISCOVERY_RESPONSE)],
    )

    with pytest.raises(AuthSSLError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert "certificate" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientSSLError)


@pytest.mark.asyncio
async def test_login_irobot_ssl_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(
        fail_at_call=2,
        exc=_ssl_error(),
        prior_responses=[
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
        ],
    )

    with pytest.raises(AuthSSLError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert "certificate" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientSSLError)


# =========================================================================
# ClientConnectorError / ServerTimeoutError -> AuthConnectionError /
# AuthTimeoutError (this session, following the user's request to also
# cover these -- deliberately hedged messages, no confident fault
# attribution, unlike the SSL case).
# =========================================================================


@pytest.mark.asyncio
async def test_login_discovery_connector_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(fail_at_call=0, exc=_connector_error(), prior_responses=[])

    with pytest.raises(AuthConnectionError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert "connect" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ClientConnectorError)


@pytest.mark.asyncio
async def test_login_gigya_connector_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(
        fail_at_call=1,
        exc=_connector_error(),
        prior_responses=[_FakeResp(200, json_body=_DISCOVERY_RESPONSE)],
    )

    with pytest.raises(AuthConnectionError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert isinstance(excinfo.value.__cause__, aiohttp.ClientConnectorError)


@pytest.mark.asyncio
async def test_login_irobot_connector_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(
        fail_at_call=2,
        exc=_connector_error(),
        prior_responses=[
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
        ],
    )

    with pytest.raises(AuthConnectionError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert isinstance(excinfo.value.__cause__, aiohttp.ClientConnectorError)


@pytest.mark.asyncio
async def test_login_discovery_timeout_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(fail_at_call=0, exc=_timeout_error(), prior_responses=[])

    with pytest.raises(AuthTimeoutError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert "too long" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, aiohttp.ServerTimeoutError)


@pytest.mark.asyncio
async def test_login_gigya_timeout_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(
        fail_at_call=1,
        exc=_timeout_error(),
        prior_responses=[_FakeResp(200, json_body=_DISCOVERY_RESPONSE)],
    )

    with pytest.raises(AuthTimeoutError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert isinstance(excinfo.value.__cause__, aiohttp.ServerTimeoutError)


@pytest.mark.asyncio
async def test_login_irobot_timeout_error_gets_clear_message() -> None:
    session = _NetworkFailingSession(
        fail_at_call=2,
        exc=_timeout_error(),
        prior_responses=[
            _FakeResp(200, json_body=_DISCOVERY_RESPONSE),
            _FakeResp(200, text_body=json.dumps(_GIGYA_RESPONSE)),
        ],
    )

    with pytest.raises(AuthTimeoutError) as excinfo:
        await login(session, "user@example.com", "hunter2", "US")

    assert isinstance(excinfo.value.__cause__, aiohttp.ServerTimeoutError)
