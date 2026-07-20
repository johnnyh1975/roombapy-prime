"""Smoke test for roombapy_prime.prime_factory.PrimeFactory.

Monkeypatches auth.login() (no real network) to confirm the factory
wires blid, mqtt endpoint and http_base through to the right places --
not the login/network behaviour itself, which is out of scope here
(login() needs a real or heavily mocked aiohttp session, see
test_auth.py's docstring for why that's not covered at the unit level).
"""
from __future__ import annotations

import pytest

from roombapy_prime import prime_factory
from roombapy_prime.auth import CloudCredentials, ConnectionToken, LoginResult


def _fake_login_result() -> LoginResult:
    token = ConnectionToken(
        client_id="c1", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=123, devices=["BLID123"],
    )
    creds = CloudCredentials(
        access_key_id="ak", secret_key="sk", session_token="st", cognito_id="us-east-1:0",
    )
    return LoginResult(
        mqtt_endpoint="mqtt.example.invalid",
        http_base="https://http-base.example.invalid",
        http_base_auth="https://http-base-auth.example.invalid",
        credentials=creds,
        robots={"BLID123": {"sku": "i755640"}},
        connection_tokens=[token],
        raw={},
        irbt_topic_prefix="irbt-fake-prefix",
    )


@pytest.mark.asyncio
async def test_create_prime_robot_wires_blid_and_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_login(session, username, password, country_code, app_id="roombapy-prime"):
        return _fake_login_result()

    monkeypatch.setattr(prime_factory, "login", fake_login)

    robot = await prime_factory.PrimeFactory.create_prime_robot(
        session=object(),  # never touched, since login() is monkeypatched
        username="user@example.invalid",
        password="hunter2",
        country_code="DE",
    )

    assert robot.blid == "BLID123"
    assert robot._mqtt._blid == "BLID123"
    assert robot._mqtt._endpoint == "mqtt.example.invalid"
    assert robot._rest._http_base_auth == "https://http-base-auth.example.invalid"
    assert robot._rest._credentials.access_key_id == "ak"
    assert robot._irbt_topic_prefix == "irbt-fake-prefix"


@pytest.mark.asyncio
async def test_create_prime_robot_respects_explicit_blid(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the account has multiple robots, an explicit blid should win
    over primary_blid()'s "first in dict" default."""

    async def fake_login(session, username, password, country_code, app_id="roombapy-prime"):
        result = _fake_login_result()
        result.robots["OTHER_BLID"] = {"sku": "i755840"}
        return result

    monkeypatch.setattr(prime_factory, "login", fake_login)

    robot = await prime_factory.PrimeFactory.create_prime_robot(
        session=object(),
        username="user@example.invalid",
        password="hunter2",
        country_code="DE",
        blid="OTHER_BLID",
    )

    assert robot.blid == "OTHER_BLID"


@pytest.mark.asyncio
async def test_create_prime_robot_accepts_pre_fetched_login_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """NEW (this session, prompted by a real "onboarding is slow" field
    report): passing an already-obtained LoginResult skips the internal
    login() call entirely -- this is the mechanism ha_roomba_plus's own
    short-lived cache uses to avoid a fully redundant second Gigya+
    iRobot login chain during the very first setup right after config
    flow. login() must NOT be called at all in this path."""
    login_call_count = 0

    async def fake_login(session, username, password, country_code, app_id="roombapy-prime"):
        nonlocal login_call_count
        login_call_count += 1
        return _fake_login_result()

    monkeypatch.setattr(prime_factory, "login", fake_login)

    pre_fetched = _fake_login_result()
    robot = await prime_factory.PrimeFactory.create_prime_robot(
        session=object(),
        username="user@example.invalid",
        password="hunter2",
        country_code="DE",
        login_result=pre_fetched,
    )

    assert login_call_count == 0, "login() should never be called when login_result is provided"
    assert robot.blid == "BLID123"
    assert robot._mqtt._endpoint == "mqtt.example.invalid"


@pytest.mark.asyncio
async def test_create_prime_robot_still_logs_in_when_login_result_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every existing caller (login_result left as the None default)
    must see no behaviour change -- a fresh login() call still
    happens, exactly as before this parameter existed."""
    login_call_count = 0

    async def fake_login(session, username, password, country_code, app_id="roombapy-prime"):
        nonlocal login_call_count
        login_call_count += 1
        return _fake_login_result()

    monkeypatch.setattr(prime_factory, "login", fake_login)

    robot = await prime_factory.PrimeFactory.create_prime_robot(
        session=object(), username="u", password="p", country_code="DE",
    )

    assert login_call_count == 1
    assert robot.blid == "BLID123"


@pytest.mark.asyncio
async def test_create_prime_robot_default_has_no_relogin(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto_refresh defaults to False -- existing callers of this
    factory method see no behaviour change."""

    async def fake_login(session, username, password, country_code, app_id="roombapy-prime"):
        return _fake_login_result()

    monkeypatch.setattr(prime_factory, "login", fake_login)

    robot = await prime_factory.PrimeFactory.create_prime_robot(
        session=object(), username="u", password="p", country_code="DE",
    )

    assert robot._relogin is None


@pytest.mark.asyncio
async def test_create_prime_robot_auto_refresh_wires_relogin_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    login_call_count = 0

    async def fake_login(session, username, password, country_code, app_id="roombapy-prime"):
        nonlocal login_call_count
        login_call_count += 1
        return _fake_login_result()

    monkeypatch.setattr(prime_factory, "login", fake_login)

    robot = await prime_factory.PrimeFactory.create_prime_robot(
        session=object(), username="u", password="p", country_code="DE",
        auto_refresh=True,
    )

    assert login_call_count == 1  # only the initial login so far
    assert robot._relogin is not None

    # calling relogin() re-runs the full login flow (re-using the same
    # closed-over credentials) rather than doing anything token-specific
    second_result = await robot._relogin()
    assert login_call_count == 2
    assert isinstance(second_result, LoginResult)
