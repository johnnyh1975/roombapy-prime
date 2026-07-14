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

import pytest

from roombapy_prime.auth import AuthError, ConnectionToken, login


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

    async def __aenter__(self) -> "_FakeResp":
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
    assert result.robots == {"BLID123": {}}
    assert len(session.calls) == 3
    assert session.calls[0].startswith("GET")
    assert session.calls[1].startswith("POST")
    assert session.calls[2].startswith("POST")


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

    with pytest.raises(AuthError, match="Gigya login failed"):
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

    with pytest.raises(AuthError, match="rate-limited"):
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
