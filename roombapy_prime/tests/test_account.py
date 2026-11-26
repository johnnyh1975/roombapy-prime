"""CloudAccount: one login per account, the right client per robot, and
one relogin however many clients notice a 403 at once."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from roombapy_prime import account as account_module
from roombapy_prime.account import CLASSIC, PRIME, CloudAccount
from roombapy_prime.auth import CloudCredentials, ConnectionToken, LoginResult, RobotLoginEntry
from roombapy_prime.rest_client import ClassicRestClient, PrimeRestClient

_CLASSIC_BLID = "CLASSIC1"
_PRIME_BLID = "PRIME1"
_UNKNOWN_BLID = "UNKNOWN1"


def _login_result(token: str = "t1") -> LoginResult:
    return LoginResult(
        mqtt_endpoint="mqtt.example.invalid",
        http_base="https://base.example.invalid",
        http_base_auth="https://auth.example.invalid",
        credentials=CloudCredentials(
            access_key_id="A", secret_key="S", session_token=token, cognito_id="us-east-1:0",
        ),
        robots={
            _CLASSIC_BLID: RobotLoginEntry(sku="i355640"),
            _PRIME_BLID: RobotLoginEntry(sku="W155020"),
            _UNKNOWN_BLID: RobotLoginEntry(sku="Z000000"),
        },
        connection_tokens=[ConnectionToken(
            client_id="app-client", iot_token="tok", iot_signature="sig",
            iot_authorizer_name="auth", expires=None,
            devices=[_CLASSIC_BLID, _PRIME_BLID, _UNKNOWN_BLID],
        )],
        raw={},
    )


def _account(login_result: LoginResult | None = None, session=None) -> CloudAccount:
    return CloudAccount(
        session, "user", "secret", "DE", login_result or _login_result(), app_id="IOS-APP",
    )


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body
        self.url = ""

    async def text(self) -> str:
        return self._body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Session:
    """Answers 403 to any request signed with a token in `expired`."""

    def __init__(self, expired: set[str]) -> None:
        self.expired = expired
        self.tokens_seen: list[str] = []

    def get(self, url, params=None, headers=None, data=None) -> _Response:
        token = headers["x-amz-security-token"]
        self.tokens_seen.append(token)
        if token in self.expired:
            return _Response(403, "{}")
        return _Response(200, json.dumps({"robot_id": "X", "num_parts": 0, "parts": []}))


@pytest.mark.asyncio
async def test_login_logs_in_once_with_the_app_id() -> None:
    fake_login = AsyncMock(return_value=_login_result())
    with patch.object(account_module, "login", fake_login):
        account = await CloudAccount.login(None, "user", "secret", "DE", app_id="IOS-APP")

    fake_login.assert_awaited_once_with(
        None, "user", "secret", "DE", app_id="IOS-APP", request_timeout=30.0
    )
    assert account.app_id == "IOS-APP"
    assert set(account.robots) == {_CLASSIC_BLID, _PRIME_BLID, _UNKNOWN_BLID}


def test_generation_has_three_answers_and_refuses_a_foreign_blid() -> None:
    account = _account()

    assert account.generation(_CLASSIC_BLID) == CLASSIC
    assert account.generation(_PRIME_BLID) == PRIME
    assert account.generation(_UNKNOWN_BLID) is None
    with pytest.raises(KeyError):
        account.generation("NOT-ON-THIS-ACCOUNT")


def test_rest_hands_out_the_generations_client_and_never_guesses() -> None:
    account = _account()

    classic = account.rest(_CLASSIC_BLID)
    prime = account.rest(_PRIME_BLID)

    assert type(classic) is ClassicRestClient
    assert type(prime) is PrimeRestClient
    assert classic.app_id == "IOS-APP"
    with pytest.raises(ValueError, match="neither SKU table"):
        account.rest(_UNKNOWN_BLID)


@pytest.mark.asyncio
async def test_prime_robot_reuses_the_login_and_refuses_a_classic_robot() -> None:
    from unittest.mock import MagicMock

    account = _account()
    built = MagicMock()
    create = AsyncMock(return_value=built)
    with patch.object(account_module.PrimeFactory, "create_prime_robot", create):
        assert await account.prime_robot(_PRIME_BLID, auto_refresh=True) is built
        with pytest.raises(ValueError, match="Classic"):
            await account.prime_robot(_CLASSIC_BLID)

    create.assert_awaited_once()
    assert create.await_args.kwargs["login_result"] is account.login_result
    # The account's relogin, not one of the robot's own (0.5.0b2).
    assert create.await_args.kwargs["relogin"] is not None


@pytest.mark.asyncio
async def test_a_classic_robot_is_built_when_the_caller_allows_it() -> None:
    """0.9.3 moved the Essentials to Classic; a caller with one set up over
    the cloud, where it worked, needs a way to keep it (0.9.4)."""
    from unittest.mock import MagicMock

    account = _account()
    built = MagicMock()
    create = AsyncMock(return_value=built)
    with patch.object(account_module.PrimeFactory, "create_prime_robot", create):
        assert await account.prime_robot(_CLASSIC_BLID, allow_classic=True) is built

    assert create.await_args.kwargs["blid"] == _CLASSIC_BLID


@pytest.mark.asyncio
async def test_a_403_relogs_through_the_account_once() -> None:
    session = _Session(expired={"t1"})
    account = _account(session=session)
    classic = account.classic_rest()
    fresh = _login_result("t2")
    fake_login = AsyncMock(return_value=fresh)

    with patch.object(account_module, "login", fake_login):
        await classic.get_robot_parts_raw(_CLASSIC_BLID)

    fake_login.assert_awaited_once_with(
        session, "user", "secret", "DE", app_id="IOS-APP", request_timeout=30.0
    )
    assert account.login_result is fresh
    assert session.tokens_seen == ["t1", "t2"]


@pytest.mark.asyncio
async def test_a_client_with_replaced_credentials_takes_the_new_ones_without_a_login() -> None:
    """The case that would otherwise cost one session per client."""
    session = _Session(expired={"t1"})
    account = _account(session=session)
    first, second = account.classic_rest(), account.prime_rest()
    fake_login = AsyncMock(return_value=_login_result("t2"))

    with patch.object(account_module, "login", fake_login):
        await first.get_robot_parts_raw(_CLASSIC_BLID)
        await second.get_robot_parts_raw(_PRIME_BLID)

    assert fake_login.await_count == 1
    assert session.tokens_seen == ["t1", "t2", "t1", "t2"]


@pytest.mark.asyncio
async def test_clients_that_hit_403_together_share_one_login() -> None:
    session = _Session(expired={"t1"})
    account = _account(session=session)
    clients = [account.classic_rest(), account.prime_rest(), account.classic_rest()]

    async def slow_login(*_a, **_k):
        await asyncio.sleep(0.01)
        return _login_result("t2")

    fake_login = AsyncMock(side_effect=slow_login)
    with patch.object(account_module, "login", fake_login):
        await asyncio.gather(*(c.get_robot_parts_raw(_CLASSIC_BLID) for c in clients))

    assert fake_login.await_count == 1


@pytest.mark.asyncio
async def test_an_explicit_relogin_always_logs_in() -> None:
    account = _account()
    fresh = _login_result("t2")
    with patch.object(account_module, "login", AsyncMock(return_value=fresh)):
        assert await account.relogin() is fresh
    assert account.login_result is fresh


@pytest.mark.asyncio
async def test_the_request_timeout_reaches_every_login_and_every_client() -> None:
    fake_login = AsyncMock(return_value=_login_result())
    with patch.object(account_module, "login", fake_login):
        account = await CloudAccount.login(None, "u", "p", "DE", request_timeout=7.5)
        await account.relogin()

    assert [c.kwargs["request_timeout"] for c in fake_login.await_args_list] == [7.5, 7.5]
    assert account.classic_rest()._request_timeout == 7.5
    assert account.prime_rest()._request_timeout == 7.5


# --- one login for every Prime robot (0.5.0b2) -------------------------------


def _prime_login(token: str, expires_in: float | None, blids=(_PRIME_BLID, "PRIME2")) -> LoginResult:
    import time as _time

    from roombapy_prime.auth import ConnectionToken

    result = _login_result(token)
    return LoginResult(
        mqtt_endpoint=result.mqtt_endpoint,
        http_base=result.http_base,
        http_base_auth=result.http_base_auth,
        credentials=result.credentials,
        robots={**result.robots, "PRIME2": RobotLoginEntry(sku="W155020")},
        connection_tokens=[
            ConnectionToken(
                client_id=f"cid-{blid}", iot_token=token, iot_signature="s",
                iot_authorizer_name="a",
                expires=None if expires_in is None else int(_time.time() + expires_in),
                devices=[blid],
            )
            for blid in blids
        ],
        raw={},
    )


class TestOneLoginForEveryPrimeRobot:
    """b1 of ha_roomba_plus 4.3 promised it: Prime robots renew their MQTT
    token through the account's login. Until 0.5.0b2 every robot had a
    relogin of its own, so three robots from one login logged in three
    times when their tokens ran out together."""

    @pytest.mark.asyncio
    async def test_two_robots_due_together_log_in_once(self) -> None:
        account = _account(_prime_login("old", expires_in=60))
        first = await account.prime_robot(_PRIME_BLID, auto_refresh=True)
        second = await account.prime_robot("PRIME2", auto_refresh=True)
        fresh = _prime_login("new", expires_in=3600)
        fake_login = AsyncMock(return_value=fresh)

        with patch.object(account_module, "login", fake_login):
            got = await asyncio.gather(first._relogin(), second._relogin())

        fake_login.assert_awaited_once()
        assert got[0] is fresh and got[1] is fresh
        assert account.login_result is fresh

    @pytest.mark.asyncio
    async def test_a_robot_takes_a_fresh_login_another_made(self) -> None:
        account = _account(_prime_login("fresh", expires_in=3600))
        robot = await account.prime_robot(_PRIME_BLID, auto_refresh=True)
        fake_login = AsyncMock()

        with patch.object(account_module, "login", fake_login):
            got = await robot._relogin()

        fake_login.assert_not_awaited()
        assert got is account.login_result

    @pytest.mark.asyncio
    async def test_a_token_without_expiry_logs_in(self) -> None:
        """Nothing says it is fresh, and the robot only asks when it
        believes its token is due."""
        account = _account(_prime_login("t", expires_in=None))
        robot = await account.prime_robot(_PRIME_BLID, auto_refresh=True)
        fresh = _prime_login("new", expires_in=3600)

        with patch.object(account_module, "login", AsyncMock(return_value=fresh)):
            assert await robot._relogin() is fresh

    @pytest.mark.asyncio
    async def test_the_robots_rest_client_relogs_through_the_account(self) -> None:
        account = _account(_prime_login("t1", expires_in=3600))
        robot = await account.prime_robot(_PRIME_BLID, auto_refresh=True)
        fresh = _prime_login("t2", expires_in=3600)
        fake_login = AsyncMock(return_value=fresh)

        with patch.object(account_module, "login", fake_login):
            await robot._rest._relogin()

        fake_login.assert_awaited_once()
        assert account.login_result is fresh

    @pytest.mark.asyncio
    async def test_without_auto_refresh_there_is_no_relogin(self) -> None:
        account = _account(_prime_login("t", expires_in=3600))
        robot = await account.prime_robot(_PRIME_BLID)

        assert robot._relogin is None

    @pytest.mark.asyncio
    async def test_a_failed_login_answers_for_half_a_minute(self) -> None:
        """Against a locked account every attempt extends the lock."""
        from roombapy_prime.auth import AuthRateLimitedError

        account = _account(_prime_login("old", expires_in=60))
        robot = await account.prime_robot(_PRIME_BLID, auto_refresh=True)
        fake_login = AsyncMock(side_effect=AuthRateLimitedError("locked"))

        with patch.object(account_module, "login", fake_login):
            for _ in range(3):
                with pytest.raises(AuthRateLimitedError):
                    await robot._relogin()

        fake_login.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_after_half_a_minute_it_tries_again(self) -> None:
        from roombapy_prime.auth import AuthRateLimitedError

        account = _account(_prime_login("old", expires_in=60))
        robot = await account.prime_robot(_PRIME_BLID, auto_refresh=True)
        fresh = _prime_login("new", expires_in=3600)

        with patch.object(account_module, "login", AsyncMock(side_effect=AuthRateLimitedError("x"))):
            with pytest.raises(AuthRateLimitedError):
                await robot._relogin()
        account._failure = (account._failure[0] - 31, account._failure[1])
        with patch.object(account_module, "login", AsyncMock(return_value=fresh)):
            assert await robot._relogin() is fresh


@pytest.mark.asyncio
async def test_associated_robots_needs_no_robot_and_relogs_through_the_account() -> None:
    """0.7.1: the account-level call works on a login that listed no
    robot -- the very case it exists for -- and a 403 relogs once."""
    empty = LoginResult(
        mqtt_endpoint="mqtt.example.invalid",
        http_base="https://base.example.invalid",
        http_base_auth="https://auth.example.invalid",
        credentials=CloudCredentials(
            access_key_id="A", secret_key="S", session_token="t1", cognito_id="us-east-1:0",
        ),
        robots={}, connection_tokens=[], raw={},
    )
    session = _Session(expired={"t1"})
    account = _account(empty, session=session)
    fake_login = AsyncMock(return_value=_login_result("t2"))

    with patch.object(account_module, "login", fake_login):
        data = await account.get_associated_robots_raw()

    assert data == {"robot_id": "X", "num_parts": 0, "parts": []}
    assert session.tokens_seen == ["t1", "t2"]
    fake_login.assert_awaited_once()


def test_robots_of_one_token_share_one_connection_also_after_a_new_client_id() -> None:
    """#91: one connection per token. A login that brought a new client
    id must not give a robot added after it a second connection."""
    account = _account()
    first = account._connection_for(_PRIME_BLID)
    assert account._connection_for(_PRIME_BLID) is first
    assert account._connection_for(_UNKNOWN_BLID) is first

    renewed = _login_result("t2")
    renewed.connection_tokens[0] = ConnectionToken(
        client_id="app-client-2", iot_token="tok2", iot_signature="sig",
        iot_authorizer_name="auth", expires=None, devices=[_PRIME_BLID, _UNKNOWN_BLID],
    )
    account._login_result = renewed
    assert account._connection_for(_UNKNOWN_BLID) is first

    other = _login_result("t3")
    other.connection_tokens[0] = ConnectionToken(
        client_id="elsewhere", iot_token="tok3", iot_signature="sig",
        iot_authorizer_name="auth", expires=None, devices=["0000000000000001"],
    )
    account._login_result = other
    assert account._connection_for("0000000000000001") is not first


@pytest.mark.asyncio
async def test_login_signals_changed_companions_without_blocking_construction(deployment_login):
    a, b = '0123456789ABCDEF', 'FEDCBA9876543210'
    account = _account(deployment_login({a: 'v007', b: 'v007'}, token='old'))
    first, second = await account.prime_robot(a), await account.prime_robot(b)
    calls = []
    second.set_topic_prefix_changed_callback(lambda: calls.append('B'))
    await first._share.lock.acquire()  # represents existing persistent recovery
    try:
        third = await asyncio.wait_for(account.prime_robot(b), .5)
        with patch.object(account_module, 'login', AsyncMock(return_value=deployment_login({a: 'v007', b: 'v011'}))):
            await account.relogin()
        assert first.topic_prefix_changed is False
        assert second.topic_prefix_changed is third.topic_prefix_changed is True
        assert calls == ['B']
        fresh = await asyncio.wait_for(account.prime_robot(b), .5)
        assert fresh.topic_prefix_changed is False
        assert fresh._irbt_topic_prefix == 'v011-irbthbu'
        assert second._irbt_topic_prefix == 'v007-irbthbu'
    finally:
        first._share.lock.release()


@pytest.mark.asyncio
@pytest.mark.parametrize('separate', [False, True])
async def test_rest_relogin_notifies_only_changed_handles_on_shared_or_separate_connections(deployment_login, separate):
    from dataclasses import replace
    a, b = '0123456789ABCDEF', 'FEDCBA9876543210'
    before = deployment_login({a: 'v007', b: 'v007'}, token='old')
    after = deployment_login({a: 'v007', b: 'v011'})
    if separate:
        def partition(result):
            return replace(result, connection_tokens=[replace(result.primary_token(), client_id=f'synthetic-{i}', devices=[blid])
                                                       for i, blid in enumerate([a, b])])
        before, after = partition(before), partition(after)
    session = _Session(expired={'old'})
    account = _account(before, session)
    first, second = await account.prime_robot(a), await account.prime_robot(b)
    assert (first._mqtt.connection is second._mqtt.connection) is not separate
    with patch.object(account_module, 'login', AsyncMock(return_value=after)) as authenticate:
        await account.prime_rest().get_robot_parts_raw(a)
    authenticate.assert_awaited_once()
    assert account.login_result is after
    assert first.topic_prefix_changed is False
    assert second.topic_prefix_changed is True
    assert second._irbt_topic_prefix == 'v007-irbthbu'
    assert all(c._client is None for c in account._connections)  # signal opens nothing


@pytest.mark.asyncio
async def test_account_observation_does_not_keep_abandoned_robot_handles(deployment_login):
    import gc
    import weakref
    blid = '0123456789ABCDEF'
    account = _account(deployment_login({blid: 'v007'}))
    robot = await account.prime_robot(blid)
    reference = weakref.ref(robot)
    del robot
    gc.collect()
    assert reference() is None
    assert len(account._prime_robots) == 0


@pytest.mark.asyncio
async def test_prefix_observer_reads_the_committed_login_snapshot(deployment_login):
    blid = '0123456789ABCDEF'
    before = deployment_login({blid: 'v007'}, token='old')
    after = deployment_login({blid: 'v011'})
    account = _account(before)
    robot = await account.prime_robot(blid)
    observed = []
    robot.set_topic_prefix_changed_callback(lambda: observed.append(account.login_result))
    with patch.object(account_module, 'login', AsyncMock(return_value=after)):
        await account.relogin()
    assert observed == [after]


def _discovery(current: str, robot_deployment: str | None, listed: tuple[str, ...]) -> LoginResult:
    """A login as login() builds it: the account prefix and `deployment` both
    come from the CURRENT deployment; `deployments` lists what discovery has."""
    blid = '0123456789ABCDEF'
    known = {d: {'irbtTopics': f'{d}-irbthbu', 'mqtt': 'mqtt.example.invalid'} for d in listed}
    return LoginResult(
        mqtt_endpoint='mqtt.example.invalid', http_base='https://base.example.invalid',
        http_base_auth='https://auth.example.invalid',
        credentials=CloudCredentials(access_key_id='A', secret_key='S',
                                     session_token=current, cognito_id='synthetic'),
        robots={blid: RobotLoginEntry(sku='W155020', svc_deployment_id=robot_deployment)},
        connection_tokens=[ConnectionToken(client_id='synthetic', iot_token=current,
            iot_signature='synthetic', iot_authorizer_name='synthetic', expires=None, devices=[blid])],
        raw={}, irbt_topic_prefix=f'{current}-irbthbu',
        deployment={'irbtTopics': f'{current}-irbthbu', 'mqtt': 'mqtt.example.invalid'},
        deployments=known,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('case, before, after, signalled', [
    # The account's fallback is what the robot was built with, and it changes.
    ('no_robot_deployment', ('v007', None, ('v007',)), ('v011', None, ('v011',)), True),
    ('own_deployment_becomes_current', ('v007', 'v011', ()), ('v011', 'v011', ()), True),
    ('fallback_to_explicit', ('v007', 'v011', ('v007',)), ('v011', 'v011', ('v011',)), True),
    # Explicit prefixes, as before.
    ('explicit_to_explicit', ('v007', 'v007', ('v007',)), ('v007', 'v011', ('v007', 'v011')), True),
    # No evidence of a move: rebuilding would leave a known prefix for a guess.
    ('explicit_replaced_by_fallback', ('v007', 'v011', ('v007', 'v011')), ('v007', 'v011', ('v007',)), False),
    ('deployment_dropped_from_login', ('v007', 'v011', ('v007', 'v011')), ('v007', None, ('v007',)), False),
    ('explicit_equal_to_account_then_deployment_lost', ('v007', 'v007', ('v007',)), ('v011', None, ('v011',)), False),
    # A fallback 0.9.1 itself warns "may be wrong" for the robot.
    ('fallback_not_the_robots_deployment', ('v007', 'v011', ('v007',)), ('v028', 'v011', ('v028',)), False),
    ('fallback_unchanged', ('v007', None, ('v007',)), ('v007', None, ('v007',)), False),
])
async def test_signal_follows_the_prefix_a_rebuild_resolves(case, before, after, signalled, caplog):
    """The signal compares the robot with what LoginResult.irbt_topic_prefix_for()
    and the factory would build now -- and a rebuild then stays quiet (no loop)."""
    blid = '0123456789ABCDEF'
    account = _account(_discovery(*before))
    robot = await account.prime_robot(blid)
    assert robot._irbt_topic_prefix == account.login_result.irbt_topic_prefix_for(blid)
    renewed = _discovery(*after)
    with patch.object(account_module, 'login', AsyncMock(return_value=renewed)):
        await account.relogin()
    assert robot.topic_prefix_changed is signalled
    assert caplog.text.count('MQTT topic prefix changed') == int(signalled)
    rebuilt = await account.prime_robot(blid)
    assert rebuilt._irbt_topic_prefix == renewed.irbt_topic_prefix_for(blid)
    if signalled:
        assert rebuilt._irbt_topic_prefix != robot._irbt_topic_prefix
    with patch.object(account_module, 'login', AsyncMock(return_value=_discovery(*after))):
        await account.relogin()
    assert rebuilt.topic_prefix_changed is False


@pytest.mark.asyncio
async def test_a_robot_missing_from_the_renewed_login_is_not_a_move():
    from dataclasses import replace
    blid = '0123456789ABCDEF'
    account = _account(_discovery('v007', None, ('v007',)))
    robot = await account.prime_robot(blid)
    renewed = replace(_discovery('v011', None, ('v011',)), robots={})
    with patch.object(account_module, 'login', AsyncMock(return_value=renewed)):
        await account.relogin()
    assert robot.topic_prefix_changed is False


@pytest.mark.asyncio
async def test_an_own_prefix_named_unlike_its_deployment_is_not_replaced_by_the_fallback():
    """Deployment ids and prefixes are separate discovery fields: an own prefix
    need not start with its id. Losing that mapping is still not a move."""
    from dataclasses import replace
    blid = '0123456789ABCDEF'
    before = _discovery('v007', 'v011', ('v007',))
    before = replace(before, deployments={**before.deployments, 'v011': {'irbtTopics': 'v028-irbthbu'}})
    account = _account(before)
    robot = await account.prime_robot(blid)
    assert robot._irbt_topic_prefix == 'v028-irbthbu'
    with patch.object(account_module, 'login', AsyncMock(return_value=_discovery('v011', None, ('v011',)))):
        await account.relogin()
    assert robot.topic_prefix_changed is False
