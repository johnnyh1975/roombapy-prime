"""Tests for roombapy_prime.mqtt_client — shadow topic construction,
get_shadow/update_shadow response handling, subscriptions, the
connection lifecycle and its errors.

No network. Since 0.5.0 the transport is aiomqtt, and _FakeAioClient
below stands in for `aiomqtt.Client`: it records subscribe/publish
calls, answers SUBACKs and PUBACKs the way a test asks it to, and lets
publish() deliver a fixture payload straight into the client's own
_dispatch() -- "the broker responded", without timing.

The field history behind each behaviour is kept in the test docstrings:
most of these exist because a tester hit the failure they guard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiomqtt import MqttCodeError, MqttError
from aiomqtt.exceptions import MqttConnectError

from roombapy_prime.auth import ConnectionToken
from roombapy_prime.errors import CloudError, CloudErrorReason
from roombapy_prime.mqtt_client import (
    PrimeMqttClient,
    ShadowConnectionError,
    ShadowError,
    ShadowResponse,
    ShadowSSLError,
    SubscriptionRejectedError,
    _shadow_base,
    _suback_is_failure,
)

#: A SUBACK that never comes.
NEVER = object()


def _load(fixtures_dir: Path, name: str) -> dict:
    return json.loads((fixtures_dir / name).read_text())


class _FakeAioClient:
    """Stand-in for aiomqtt.Client. No sockets involved.

    `suback(topic)` decides each SUBACK: a list of reason codes, an
    exception to raise (MqttCodeError: never sent), NEVER (no SUBACK),
    or a coroutine function awaited first (a delayed SUBACK)."""

    def __init__(self, suback: Callable[[str], Any] | None = None) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.published: list[tuple[str, Any]] = []
        self.on_publish_react: Callable[[str, Any], None] | None = None
        #: False: no PUBACK (aiomqtt raises "Operation timed out").
        self.publish_confirmed = True
        #: Raised by publish() instead, e.g. MqttCodeError(4, ...).
        self.publish_error: BaseException | None = None
        self.suback = suback or (lambda _topic: [0])
        self.order: list[str] = []
        self.exited = False

    async def subscribe(self, topic: str, qos: int = 0, timeout: float | None = None) -> Any:
        self.subscribed.append(topic)
        self.order.append(f"subscribe:{topic}")
        answer = self.suback(topic)
        if asyncio.iscoroutine(answer):
            answer = await answer
        if answer is NEVER:
            await asyncio.Event().wait()
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def unsubscribe(self, topic: str, timeout: float | None = None) -> None:
        self.unsubscribed.append(topic)

    async def publish(self, topic: str, payload: Any = None, qos: int = 0, timeout: float | None = None) -> None:
        self.published.append((topic, payload))
        self.order.append(f"publish:{topic}")
        if self.publish_error is not None:
            raise self.publish_error
        if self.on_publish_react is not None:
            self.on_publish_react(topic, payload)
        if not self.publish_confirmed:
            raise MqttError("Operation timed out")

    async def __aexit__(self, *_exc: Any) -> None:
        self.exited = True


def _dummy_token(client_id: str = "x") -> ConnectionToken:
    return ConnectionToken(
        client_id=client_id, iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=None, devices=[],
    )


def _connected_client(
    blid: str = "0000000000000000", fake: _FakeAioClient | None = None
) -> tuple[PrimeMqttClient, _FakeAioClient]:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid=blid)
    fake = fake or _FakeAioClient()
    client._client = fake  # type: ignore[assignment]  # bypass connect() -- no network
    client._connected = True
    return client, fake


def _react_with(client: PrimeMqttClient, verb: str, response_topic_suffix: str, payload: dict) -> Callable:
    """When publish() goes to .../{verb}, deliver `payload` on
    .../{verb}/{response_topic_suffix} through the client's _dispatch()."""

    def react(topic: str, _payload: object) -> None:
        if topic.endswith(f"/{verb}"):
            response_topic = topic[: -len(f"/{verb}")] + f"/{verb}/{response_topic_suffix}"
            client._dispatch(response_topic, json.dumps(payload).encode())

    return react


# --- _shadow_base and topic builders ------------------------------------

def test_shadow_base_classic() -> None:
    assert _shadow_base("BLID123", None) == "$aws/things/BLID123/shadow"


def test_shadow_base_named() -> None:
    assert _shadow_base("BLID123", "rw-settings") == "$aws/things/BLID123/shadow/name/rw-settings"


def test_shadow_topic_helper() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.shadow_topic("update/delta") == "$aws/things/BLID1/shadow/update/delta"
    assert (
        client.shadow_topic("update/delta", named="rw-settings")
        == "$aws/things/BLID1/shadow/name/rw-settings/update/delta"
    )


def test_livemap_topic_helper() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.livemap_topic("irbt-prefix") == "irbt-prefix/things/BLID1/livemap/update"


def test_dock_report_topic_named_and_wildcard() -> None:
    """dock/{reportType}/report family. `paddry` is confirmed live;
    the no-argument form is a `+` wildcard for discovering siblings."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.dock_report_topic("pfx", "paddry") == "pfx/things/BLID1/dock/paddry/report"
    assert client.dock_report_topic("pfx") == "pfx/things/BLID1/dock/+/report"


def test_dock_report_model_reads_the_family_key() -> None:
    from roombapy_prime.models import DockPadDryReport, DockReport

    assert DockReport is DockPadDryReport
    report = DockReport.from_json({"reportType": "paddry", "dockId": "NA"})
    assert report.report_type == "paddry"
    assert report.dock_id == "NA"


def test_cmd_topic_helper() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.cmd_topic("irbt-prefix") == "irbt-prefix/things/BLID1/cmd"


def test_mission_timeline_topic_helper_report_and_request() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.mission_timeline_topic("irbt-prefix") == "irbt-prefix/things/BLID1/mission/timeline/report"
    assert (
        client.mission_timeline_topic("irbt-prefix", report=False)
        == "irbt-prefix/things/BLID1/mission/timeline/request"
    )


def test_rejected_report_topic_helper() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.rejected_report_topic("irbt-prefix") == "irbt-prefix/things/BLID1/rejected/report"


# --- get_shadow / update_shadow ------------------------------------------

@pytest.mark.asyncio
async def test_get_shadow_classic_ephemeral(fixtures_dir: Path) -> None:
    client, fake = _connected_client(blid="0000000000000000")
    fake.on_publish_react = _react_with(
        client, "get", "accepted", _load(fixtures_dir, "shadow_get_classic_ephemeral.json")
    )

    response = await client.get_shadow(timeout=1.0)

    assert response.payload["state"]["reported"]["sku"] == "R980040"
    assert response.payload["state"]["reported"]["cap"]["pose"] == 1
    assert response.payload["version"] == 90131


@pytest.mark.asyncio
async def test_get_shadow_classic_smart_tier(fixtures_dir: Path) -> None:
    client, fake = _connected_client(blid="1111111111111111")
    fake.on_publish_react = _react_with(
        client, "get", "accepted", _load(fixtures_dir, "shadow_get_classic_smart_tier.json")
    )

    response = await client.get_shadow(timeout=1.0)

    assert response.payload["state"]["reported"]["sku"] == "i755640"
    assert response.payload["state"]["reported"]["cap"]["pmaps"] == 9


@pytest.mark.asyncio
async def test_get_shadow_named_responds_on_smart_tier(fixtures_dir: Path) -> None:
    client, fake = _connected_client(blid="1111111111111111")
    fake.on_publish_react = _react_with(
        client, "get", "accepted", _load(fixtures_dir, "shadow_get_rw_settings_smart_tier.json")
    )

    response = await client.get_shadow(named="rw-settings", timeout=1.0)

    assert response.payload["state"]["reported"]["audio"]["volume"] == 100
    assert fake.subscribed == [
        "$aws/things/1111111111111111/shadow/name/rw-settings/get/accepted",
        "$aws/things/1111111111111111/shadow/name/rw-settings/get/rejected",
    ]


@pytest.mark.asyncio
async def test_get_shadow_reconnects_first_when_connection_known_down(fixtures_dir: Path) -> None:
    """A caller doing sequential get_shadow() calls with no reconnect
    logic of its own (verify_named_shadows.py) would, after one silent
    mid-run disconnect, time out forever. get_shadow() reconnects when it
    already knows the connection is down."""
    client, fake = _connected_client()
    client._connected = False

    async def fake_reconnect(timeout: float = 10.0) -> None:
        client._connected = True

    client.reconnect = fake_reconnect  # type: ignore[method-assign]
    fake.on_publish_react = _react_with(
        client, "get", "accepted", _load(fixtures_dir, "shadow_get_classic_ephemeral.json")
    )

    response = await client.get_shadow(timeout=1.0)

    assert response.payload["state"]["reported"]["sku"] == "R980040"


@pytest.mark.asyncio
async def test_get_shadow_named_times_out_on_ephemeral() -> None:
    """EPHEMERAL's named-shadow behaviour IS total silence: the publish
    goes out, nothing arrives, and get_shadow times out rather than
    hanging or raising the wrong error."""
    client, _fake = _connected_client()

    with pytest.raises(ShadowError, match="No response to GET") as excinfo:
        await client.get_shadow(named="rw-settings", timeout=0.1)

    assert excinfo.value.reason is CloudErrorReason.TIMEOUT


@pytest.mark.asyncio
async def test_a_timed_out_read_leaves_no_waiter_behind() -> None:
    """Up to 0.4.x the waiter stayed registered after a timeout and took
    the next answer on that topic. Harmless under the lock, but a waiter
    nobody awaits is a leak."""
    client, _fake = _connected_client()

    with pytest.raises(ShadowError):
        await client.get_shadow(timeout=0.05)

    assert client._pending == {}


@pytest.mark.asyncio
async def test_get_shadow_rejected() -> None:
    client, fake = _connected_client()
    fake.on_publish_react = _react_with(client, "get", "rejected", {"code": 404, "message": "no shadow"})

    with pytest.raises(ShadowError, match="rejected") as excinfo:
        await client.get_shadow(timeout=1.0)

    assert excinfo.value.reason is CloudErrorReason.SHADOW_REJECTED


@pytest.mark.asyncio
async def test_update_shadow_accepted(fixtures_dir: Path) -> None:
    client, fake = _connected_client()
    fake.on_publish_react = _react_with(
        client, "update", "accepted", _load(fixtures_dir, "shadow_update_accepted.json")
    )

    response = await client.update_shadow({"binPause": False}, timeout=1.0)

    assert response.payload["version"] == 90132
    publish_topic, publish_payload = fake.published[0]
    assert publish_topic.endswith("/update")
    assert json.loads(publish_payload)["state"]["desired"] == {"binPause": False}


@pytest.mark.asyncio
async def test_update_shadow_rejected_and_unanswered() -> None:
    client, fake = _connected_client()
    fake.on_publish_react = _react_with(client, "update", "rejected", {"code": 400, "message": "no"})
    with pytest.raises(ShadowError, match="UPDATE rejected") as rejected:
        await client.update_shadow({"binPause": False}, timeout=1.0)

    client, fake = _connected_client()
    with pytest.raises(ShadowError, match="No response to UPDATE") as unanswered:
        await client.update_shadow({"binPause": False}, timeout=0.1)

    assert rejected.value.reason is CloudErrorReason.SHADOW_REJECTED
    assert unanswered.value.reason is CloudErrorReason.TIMEOUT


@pytest.mark.asyncio
async def test_get_shadow_before_connect_raises_a_readable_error() -> None:
    """The exact path a field tester hit: a diagnostic script asked for
    a named shadow without opening the connection. The error used to be
    a bare assert four frames down."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="x")

    with pytest.raises(ShadowError, match="Not connected") as excinfo:
        await client.get_shadow(timeout=0.1)

    assert excinfo.value.reason is CloudErrorReason.NOT_CONNECTED
    assert "MQTT, not REST" in str(excinfo.value)


class TestAShadowGetIsActuallySent:
    """A queued-but-unsent request produced exactly the symptom
    @DaRealGuGu reported: no answer within eight seconds, no error, and
    nothing to distinguish "this robot has no such shadow" from "we
    never asked"."""

    @pytest.mark.asyncio
    async def test_a_refused_publish_says_the_request_never_left(self) -> None:
        client, fake = _connected_client()
        fake.publish_error = MqttCodeError(4, "Could not publish message")

        with pytest.raises(ShadowError, match="never left") as excinfo:
            await client.get_shadow(timeout=1.0)

        assert "rc=4" in str(excinfo.value)
        assert excinfo.value.reason is CloudErrorReason.PUBLISH_NOT_DELIVERED

    @pytest.mark.asyncio
    async def test_a_publish_without_puback_is_caught(self) -> None:
        """The connection accepts messages and does not deliver them --
        the state that looks healthiest and works least."""
        client, fake = _connected_client()
        fake.publish_confirmed = False

        with pytest.raises(ShadowError, match="not acknowledged") as excinfo:
            await client.get_shadow(timeout=1.0)

        assert excinfo.value.reason is CloudErrorReason.PUBLISH_NOT_DELIVERED

    @pytest.mark.asyncio
    async def test_an_answer_that_arrived_anyway_wins(self, fixtures_dir: Path) -> None:
        """No PUBACK, but the answer came: the request evidently arrived."""
        client, fake = _connected_client()
        fake.publish_confirmed = False
        fake.on_publish_react = _react_with(
            client, "get", "accepted", _load(fixtures_dir, "shadow_get_classic_ephemeral.json")
        )

        response = await client.get_shadow(timeout=1.0)

        assert response.topic.endswith("/get/accepted")


class TestTheBrokersReasonIsCarriedIntoTheError:
    """Three accounts, three symptoms, one wall: @DaRealGuGu (publish
    queued, never sent), @jouwdan (no SUBACK, then no response),
    @utkjmitch (publish refused, rc=4). A broker that drops a client for
    an unauthorised subscribe says why on disconnect, and the library
    recorded that reason and never showed it."""

    async def _refuse(self, reason: str | None) -> str:
        client, fake = _connected_client()
        client._disconnect_reason = reason
        fake.publish_error = MqttCodeError(4, "Could not publish message")
        with pytest.raises(ShadowError) as excinfo:
            await client.request_mission_timeline("pfx", 1)
        return str(excinfo.value)

    @pytest.mark.asyncio
    async def test_the_reason_reaches_the_message(self) -> None:
        assert "Not authorized to subscribe" in await self._refuse("Not authorized to subscribe")

    @pytest.mark.asyncio
    async def test_without_a_reason_the_message_does_not_invent_one(self) -> None:
        message = await self._refuse(None)
        assert "rc=4" in message
        assert "disconnect reason" not in message

    def test_the_suback_warning_says_when_the_socket_looks_open(self) -> None:
        import inspect

        from roombapy_prime import mqtt_client

        source = inspect.getsource(mqtt_client)
        assert "the socket is" in source
        assert "probably still open" in source


# --- SUBSCRIBE before PUBLISH, and not twice -----------------------------

@pytest.mark.asyncio
async def test_get_shadow_waits_for_subscribe_confirmation_before_publishing() -> None:
    """Session 33: publish() may only happen AFTER the SUBACKs. A
    response arriving before the SUBACK was lost -- chairstacker's
    "get_settings() sometimes responds, sometimes doesn't"."""

    async def late(_topic: str) -> list[int]:
        await asyncio.sleep(0.02)
        return [1]

    fake = _FakeAioClient(suback=late)
    client, fake = _connected_client(blid="X", fake=fake)
    fake.on_publish_react = lambda topic, _p: client._dispatch(
        "$aws/things/X/shadow/get/accepted", b"{}"
    ) if topic.endswith("/get") else None

    await client.get_shadow(timeout=2.0)

    publish_index = next(i for i, e in enumerate(fake.order) if e.startswith("publish:"))
    assert all(
        i < publish_index for i, e in enumerate(fake.order) if e.startswith("subscribe:")
    )


class TestARepeatReadDoesNotResubscribe:
    """@DaRealGuGu's second `rw-settings` read in one session got no
    SUBACK within three seconds and then no response within eight,
    while the first read had worked: every read re-subscribed to topics
    the broker had already granted."""

    @pytest.mark.asyncio
    async def test_a_second_read_of_the_same_shadow_does_not_subscribe(self) -> None:
        client, fake = _connected_client(blid="B")
        fake.on_publish_react = _react_with(client, "get", "accepted", {})

        await client.get_shadow(named="rw-settings", timeout=1.0)
        await client.get_shadow(named="rw-settings", timeout=1.0)

        assert len(fake.subscribed) == 2  # accepted + rejected, once

    @pytest.mark.asyncio
    async def test_a_different_shadow_still_subscribes(self) -> None:
        client, fake = _connected_client(blid="B")
        fake.on_publish_react = _react_with(client, "get", "accepted", {})

        await client.get_shadow(named="rw-settings", timeout=1.0)
        await client.get_shadow(timeout=1.0)

        assert len(fake.subscribed) == 4

    def test_a_disconnect_forgets_everything(self) -> None:
        """A NEW SESSION GRANTS NOTHING."""
        client, _fake = _connected_client()
        client._subscribed_topics.update(["a/get/accepted", "b/get/rejected"])

        client._connection_lost("session ended")

        assert client._subscribed_topics == set()


# --- persistent subscribe / unsubscribe / dispatch -----------------------

@pytest.mark.asyncio
async def test_persistent_wildcard_subscription_receives_matching_messages() -> None:
    """A live wildcard capture came back empty despite matching traffic
    (chairstacker): persistent subscribers were looked up by exact key,
    and a message never arrives on the literal wildcard string."""
    client, _fake = _connected_client(blid="BLID1")
    received: list[ShadowResponse] = []
    await client.subscribe("prefix/things/BLID1/#", received.append)

    client._dispatch("prefix/things/BLID1/mission/timeline/report", b'{"phase": "run"}')

    assert [r.payload for r in received] == [{"phase": "run"}]


@pytest.mark.asyncio
async def test_persistent_exact_and_wildcard_subscriptions_both_fire_for_same_message() -> None:
    client, _fake = _connected_client(blid="BLID1")
    exact: list[ShadowResponse] = []
    wildcard: list[ShadowResponse] = []
    topic = "prefix/things/BLID1/mission/timeline/report"
    await client.subscribe(topic, exact.append)
    await client.subscribe("prefix/things/BLID1/#", wildcard.append)

    client._dispatch(topic, b'{"phase": "run"}')

    assert len(exact) == 1 and len(wildcard) == 1


@pytest.mark.asyncio
async def test_persistent_wildcard_subscription_ignores_non_matching_topics() -> None:
    client, _fake = _connected_client(blid="BLID1")
    received: list[ShadowResponse] = []
    await client.subscribe("prefix/things/BLID1/mission/#", received.append)

    client._dispatch("prefix/things/BLID1/rejected/report", b'{"reason": "busy"}')

    assert received == []


@pytest.mark.asyncio
async def test_subscribe_delivers_every_message_not_just_first() -> None:
    client, _fake = _connected_client()
    received: list[Any] = []
    await client.subscribe("some/topic", lambda resp: received.append(resp.payload))

    for n in (1, 2, 3):
        client._dispatch("some/topic", json.dumps({"n": n}).encode())

    assert received == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_a_payload_that_is_not_json_arrives_as_text() -> None:
    client, _fake = _connected_client()
    received: list[Any] = []
    client._persistent["t"] = [lambda resp: received.append(resp.payload)]

    client._dispatch("t", b"\xffnot json")
    client._dispatch("t", None)

    assert received[0].endswith("not json") and received[1] == ""


@pytest.mark.asyncio
async def test_subscribe_only_calls_broker_subscribe_once_per_topic() -> None:
    client, fake = _connected_client()
    await client.subscribe("t", lambda resp: None)
    await client.subscribe("t", lambda resp: None)

    assert fake.subscribed.count("t") == 1


@pytest.mark.asyncio
async def test_unsubscribe_removes_only_that_callback() -> None:
    client, fake = _connected_client()
    received_a: list[Any] = []
    received_b: list[Any] = []

    def cb_a(resp: ShadowResponse) -> None:
        received_a.append(resp.payload)

    def cb_b(resp: ShadowResponse) -> None:
        received_b.append(resp.payload)

    await client.subscribe("t", cb_a)
    await client.subscribe("t", cb_b)
    await client.unsubscribe("t", cb_a)
    client._dispatch("t", b'{"x": 1}')

    assert received_a == [] and received_b == [{"x": 1}]


@pytest.mark.asyncio
async def test_unsubscribe_last_callback_unsubscribes_at_broker_level() -> None:
    """Broker-level unsubscribe only when the LAST callback goes, so two
    watchers on one topic don't kill each other's subscription."""
    client, fake = _connected_client()

    def cb_a(_resp: ShadowResponse) -> None: ...

    def cb_b(_resp: ShadowResponse) -> None: ...

    await client.subscribe("t", cb_a)
    await client.subscribe("t", cb_b)
    await client.unsubscribe("t", cb_a)
    assert "t" not in fake.unsubscribed

    await client.unsubscribe("t", cb_b)
    assert "t" in fake.unsubscribed


@pytest.mark.asyncio
async def test_unsubscribe_unknown_topic_is_a_noop() -> None:
    client, fake = _connected_client()
    await client.unsubscribe("never/subscribed", lambda resp: None)
    assert fake.unsubscribed == []


@pytest.mark.asyncio
async def test_unsubscribe_on_a_dead_connection_does_not_reconnect_or_raise() -> None:
    """It runs from finally-blocks: raising would mask the original
    error, and rebuilding a connection to tear it down is absurd."""
    client, fake = _connected_client()
    await client.subscribe("t", print)
    client._connected = False
    client.reconnect = AsyncMock()  # type: ignore[method-assign]

    await client.unsubscribe("t", print)

    client.reconnect.assert_not_awaited()
    assert fake.unsubscribed == []


@pytest.mark.asyncio
async def test_an_unconfirmed_unsubscribe_is_only_logged() -> None:
    client, fake = _connected_client()
    await client.subscribe("t", print)

    async def fails(topic: str, timeout: float | None = None) -> None:
        raise MqttError("Operation timed out")

    fake.unsubscribe = fails  # type: ignore[method-assign]

    await client.unsubscribe("t", print)  # must not raise

    assert "t" not in client._persistent


class TestACallbackCannotTakeDownTheConnection:
    """In 0.4.x a raising callback killed paho's network thread, and the
    connection looked alive while delivering nothing (@jouwdan: 21 keys,
    then silence; @DaRealGuGu: "queued but never sent"). Since 0.5.0 it
    would end the message task -- the same silence."""

    def test_a_raising_one_shot_callback_is_survived(self) -> None:
        client, _fake = _connected_client()
        client._pending["t/1"] = [lambda _r: (_ for _ in ()).throw(ValueError("x"))]
        client._dispatch("t/1", b"{}")

    def test_a_raising_watcher_is_survived(self) -> None:
        client, _fake = _connected_client()
        client._persistent["t/#"] = [lambda _r: (_ for _ in ()).throw(RuntimeError("x"))]
        client._dispatch("t/1", b"{}")

    def test_one_bad_callback_does_not_stop_the_others(self) -> None:
        seen: list[ShadowResponse] = []
        client, _fake = _connected_client()
        client._pending["t/1"] = [lambda _r: (_ for _ in ()).throw(ValueError("x")), seen.append]
        client._persistent["t/#"] = [lambda _r: (_ for _ in ()).throw(ValueError("y")), seen.append]

        client._dispatch("t/1", b"{}")

        assert len(seen) == 2


def test_dispatch_survives_a_subscription_starting_mid_message() -> None:
    """A watcher that registers another subscription while dispatch walks
    the registrations must not break the walk ("dictionary changed
    size during iteration" would end the message task)."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    seen: list[str] = []

    def watcher(_response: object) -> None:
        seen.append("first")
        client._persistent.setdefault(f"other/{len(seen)}", []).append(lambda _r: None)

    client._persistent["prefix/things/BLID1/#"] = [watcher]

    client._dispatch("prefix/things/BLID1/livemap/update", b"{}")

    assert seen == ["first"]


class TestAFailedSubscribeLeavesNoPoisonedTopic:
    """@jouwdan's Max 705 stayed broken across retries (PR #62): the
    callback was registered before the broker subscribe, so a failing
    subscribe left the topic registered with no subscription, and every
    later subscribe() skipped the broker call."""

    @pytest.mark.asyncio
    async def test_a_rejected_subscribe_registers_nothing_and_a_retry_subscribes(self) -> None:
        client, _fake = _connected_client()
        client._subscribe_and_wait = AsyncMock(  # type: ignore[method-assign]
            side_effect=SubscriptionRejectedError("broker denied")
        )
        with pytest.raises(SubscriptionRejectedError):
            await client.subscribe("things/x/shadow", MagicMock())
        assert "things/x/shadow" not in client._persistent

        client._subscribe_and_wait.reset_mock(side_effect=True)
        await client.subscribe("things/x/shadow", MagicMock())

        assert client._subscribe_and_wait.await_count == 1
        assert len(client._persistent["things/x/shadow"]) == 1


# --- SUBACK outcomes -----------------------------------------------------

class TestSubscribeAndWaitOutcomes:
    """Three outcomes, kept apart: never sent, rejected, unconfirmed.
    A rejection used to be recorded exactly like a success -- chairstacker
    triggered a favourite and a room clean while our wildcard watcher saw
    nothing at all."""

    @pytest.mark.asyncio
    async def test_a_granted_subscription_does_not_raise(self) -> None:
        client, fake = _connected_client(fake=_FakeAioClient(suback=lambda _t: [1]))
        await client.subscribe("some/topic", lambda msg: None)
        assert "some/topic" in fake.subscribed
        assert client.last_subscribe_unconfirmed == []

    @pytest.mark.asyncio
    async def test_a_rejected_suback_raises(self) -> None:
        client, _fake = _connected_client(fake=_FakeAioClient(suback=lambda _t: [0x80]))

        with pytest.raises(SubscriptionRejectedError) as exc_info:
            await client.subscribe("restricted/topic", lambda msg: None)

        assert "restricted/topic" in str(exc_info.value)
        assert exc_info.value.reason is CloudErrorReason.SUBSCRIPTION_REJECTED

    @pytest.mark.asyncio
    async def test_only_the_rejected_topic_is_named(self) -> None:
        codes = {"good/topic": [1], "bad/topic": [0x80]}
        client, _fake = _connected_client(fake=_FakeAioClient(suback=codes.__getitem__))

        with pytest.raises(SubscriptionRejectedError) as exc_info:
            await client._subscribe_and_wait(["good/topic", "bad/topic"])

        assert "bad/topic" in str(exc_info.value)
        assert "good/topic" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_a_subscription_never_sent_is_not_a_rejection(self) -> None:
        """The broker never saw it, so "the broker's policy denied this"
        would be the wrong thing to say."""
        client, _fake = _connected_client(
            fake=_FakeAioClient(suback=lambda _t: MqttCodeError(4, "Could not subscribe to topic"))
        )

        with pytest.raises(SubscriptionRejectedError, match="never sent") as excinfo:
            await client._subscribe_and_wait(["some/topic"], timeout=0.2)

        assert excinfo.value.reason is CloudErrorReason.SUBSCRIPTION_NOT_SENT

    @pytest.mark.asyncio
    async def test_no_suback_is_warned_about_not_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        """Some Prime sessions deliver traffic without a visible SUBACK.
        An unconfirmed subscription is RECORDED AS UNCONFIRMED and
        warned about -- never raised (b3 onwards)."""
        client, _fake = _connected_client(fake=_FakeAioClient(suback=lambda _t: NEVER))

        await client._subscribe_and_wait(["quiet/topic"], timeout=0.02)

        assert client.last_subscribe_unconfirmed == ["quiet/topic"]
        assert client.subscribe_unconfirmed_count == 1
        assert "no SUBACK within" in caplog.text


class TestALateSubackIsStillASuback:
    """@utkjmitch (b7): EVERY reconnect logs `no SUBACK within 3.0s`, on
    the 55-minute cycle. Acting on the snapshot at the deadline would
    put him into a reconnect loop for subscriptions acknowledged a
    moment later."""

    @pytest.mark.asyncio
    async def test_a_suback_arriving_after_the_wait_clears_the_topic(self) -> None:
        release = asyncio.Event()

        async def late(_topic: str) -> list[int]:
            await release.wait()
            return [1]

        client, _fake = _connected_client(fake=_FakeAioClient(suback=late))
        client._persistent.update({"a/topic": [print], "b/topic": [print]})

        await client._subscribe_and_wait(["a/topic", "b/topic"], timeout=0.02)
        assert client.resubscribe_still_unconfirmed() == ["a/topic", "b/topic"]

        release.set()
        await asyncio.sleep(0.01)

        assert client.resubscribe_still_unconfirmed() == []

    @pytest.mark.asyncio
    async def test_one_that_never_arrives_is_still_reported(self) -> None:
        async def only_a(topic: str) -> Any:
            return [1] if topic == "a/topic" else NEVER

        client, _fake = _connected_client(fake=_FakeAioClient(suback=only_a))
        client._persistent.update({"a/topic": [print], "b/topic": [print]})

        await client._subscribe_and_wait(["a/topic", "b/topic"], timeout=0.02)

        assert client.resubscribe_still_unconfirmed() == ["b/topic"]

    @pytest.mark.asyncio
    async def test_it_asks_about_the_watchers_topics_not_the_last_subscribe(self) -> None:
        """A shadow read in the second after a reconnect replaced the
        list this checked, and the watcher then read "all acknowledged"
        for subscriptions that were not (review finding)."""
        client, _fake = _connected_client(fake=_FakeAioClient(suback=lambda _t: NEVER))
        client._persistent["watched/topic"] = [print]
        await client._subscribe_and_wait(["watched/topic"], timeout=0.02)

        _fake.suback = lambda _t: [1]
        await client._subscribe_and_wait(["read/get/accepted"], timeout=0.02)

        assert client.resubscribe_still_unconfirmed() == ["watched/topic"]

    @pytest.mark.asyncio
    async def test_a_late_rejection_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        release = asyncio.Event()

        async def late_no(_topic: str) -> list[int]:
            await release.wait()
            return [0x80]

        client, _fake = _connected_client(fake=_FakeAioClient(suback=late_no))
        await client._subscribe_and_wait(["a/topic"], timeout=0.02)

        release.set()
        await asyncio.sleep(0.01)

        assert "late SUBACK REJECTED" in caplog.text

    @pytest.mark.asyncio
    async def test_a_disconnect_stops_waiting_for_them(self) -> None:
        client, _fake = _connected_client(fake=_FakeAioClient(suback=lambda _t: NEVER))
        await client._subscribe_and_wait(["a/topic"], timeout=0.02)
        task = client._subscribe_tasks["a/topic"]

        client._connection_lost("gone")
        await asyncio.sleep(0)

        assert task.cancelled()
        assert client.resubscribe_still_unconfirmed() == []


@pytest.mark.asyncio
async def test_no_client_after_reconnect_is_a_cloud_error() -> None:
    """Was a builtin ConnectionError, past every `except CloudError`."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="x")
    client.reconnect = AsyncMock()  # type: ignore[method-assign]  # leaves no client

    with pytest.raises(CloudError) as excinfo:
        await client._subscribe_and_wait(["some/topic"], timeout=0.2)

    assert isinstance(excinfo.value, ShadowError)
    assert excinfo.value.reason is CloudErrorReason.CONNECTION_FAILED


class TestSubackReasonCodeHandling:
    """REAL FIELD CRASH (DaRealGuGu, v0.1.11a22): `int(rc) >= 0x80` on a
    paho 2.x ReasonCode OBJECT raised TypeError on paho's network thread,
    killed the client, and every later read and PUBACK timed out -- which
    was then reported to the tester as a policy-level block."""

    class _ReasonCode:
        def __init__(self, value: int, is_failure: bool) -> None:
            self.value = value
            self.is_failure = is_failure

        def __int__(self) -> int:
            raise TypeError("int() argument must be a string, a bytes-like object or a real number")

    def test_paho2_reason_code_objects_do_not_raise(self) -> None:
        assert _suback_is_failure(self._ReasonCode(0, is_failure=False)) is False
        assert _suback_is_failure(self._ReasonCode(0x80, is_failure=True)) is True

    def test_plain_ints_still_work(self) -> None:
        assert _suback_is_failure(0) is False
        assert _suback_is_failure(0x80) is True

    def test_an_unrecognised_type_is_treated_as_not_a_failure(self) -> None:
        assert _suback_is_failure(object()) is False


class TestAgainstRealPahoReasonCodes:
    """The real class, because the crash happened exactly at the
    boundary between the assumed type and the actual one. The packet
    type argument is `SUBACK >> 4`, not SUBACK."""

    def _code(self, value: int) -> Any:
        from paho.mqtt.client import SUBACK
        from paho.mqtt.reasoncodes import ReasonCode

        return ReasonCode(SUBACK >> 4, identifier=value)

    @pytest.mark.parametrize("value", [0x00, 0x01, 0x02])
    def test_granted_qos_codes_are_not_failures(self, value: int) -> None:
        assert _suback_is_failure(self._code(value)) is False

    @pytest.mark.parametrize("value", [0x80, 0x87, 0x8F, 0x9E, 0xA1])
    def test_real_failure_codes_are_detected(self, value: int) -> None:
        assert _suback_is_failure(self._code(value)) is True

    @pytest.mark.asyncio
    async def test_a_real_rejection_code_from_the_client_raises(self) -> None:
        client, _fake = _connected_client(fake=_FakeAioClient(suback=lambda _t: [self._code(0x87)]))
        with pytest.raises(SubscriptionRejectedError):
            await client._subscribe_and_wait(["t"])


# --- commands ------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_cmd_sends_expected_payload_shape() -> None:
    client, fake = _connected_client(blid="BLID1")

    assert await client.publish_cmd("irbt-prefix", "start", initiator="localApp") is True

    topic, payload = fake.published[0]
    assert topic == "irbt-prefix/things/BLID1/cmd"
    body = json.loads(payload)
    assert body["command"] == "start" and body["initiator"] == "localApp"
    assert isinstance(body["time"], int)


@pytest.mark.asyncio
async def test_publish_cmd_payload_sends_arbitrary_dict_via_cmd_topic() -> None:
    client, fake = _connected_client(blid="BLID1")
    await client.publish_cmd_payload("irbt-prefix", {"command": "start", "robot_id": "BLID1", "regions": []})

    topic, payload = fake.published[0]
    body = json.loads(payload)
    assert topic == "irbt-prefix/things/BLID1/cmd"
    assert body["robot_id"] == "BLID1" and body["regions"] == []
    assert isinstance(body["time"], int)


@pytest.mark.asyncio
async def test_publish_cmd_payload_does_not_override_existing_time_field() -> None:
    client, fake = _connected_client(blid="BLID1")
    await client.publish_cmd_payload("irbt-prefix", {"command": "start", "time": 12345})
    assert json.loads(fake.published[0][1])["time"] == 12345


class TestPublishCmdPayloadPubackConfirmation:
    """QoS 1 was set, but nothing checked the broker's PUBACK. That
    matters: rejected/report is published BY THE ROBOT, so a command the
    broker silently drops never reaches the robot to be rejected, and
    "no rejection" is no proof of delivery."""

    @pytest.mark.asyncio
    async def test_true_when_the_broker_confirms(self) -> None:
        client, _fake = _connected_client()
        assert await client.publish_cmd_payload("p", {"command": "start"}) is True

    @pytest.mark.asyncio
    async def test_false_without_puback(self) -> None:
        client, fake = _connected_client()
        fake.publish_confirmed = False
        assert await client.publish_cmd_payload("p", {"command": "start"}) is False
        assert await client.publish_cmd("p", "start") is False

    @pytest.mark.asyncio
    async def test_a_refused_publish_is_false_not_an_exception(self) -> None:
        client, fake = _connected_client()
        fake.publish_error = MqttCodeError(4, "Could not publish message")
        assert await client.publish_cmd_payload("p", {"command": "start"}) is False


class TestPublishRevivesADeadConnection:
    """@DaRealGuGu, three sessions: the FIRST send of every session got no
    PUBACK. The connection was already dead before the send -- killed by
    the interactive pause while a human read the payload -- and publish
    only checked that a client object existed."""

    def _client(self, *, connected: bool) -> PrimeMqttClient:
        client, _fake = _connected_client()
        client._connected = connected

        async def revive(**_kw: Any) -> None:
            client._connected = True

        client.reconnect = AsyncMock(side_effect=revive)  # type: ignore[method-assign]
        return client

    @pytest.mark.asyncio
    async def test_a_dead_connection_is_reconnected_before_publishing(self) -> None:
        client = self._client(connected=False)
        assert await client.publish_cmd_payload("v005-irbthbu", {"command": "start"}) is True
        client.reconnect.assert_awaited_once()
        assert client._client.published  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_a_live_connection_is_not_needlessly_reconnected(self) -> None:
        """Reconnecting a healthy connection would drop the
        subscriptions watchers depend on."""
        client = self._client(connected=True)
        await client.publish_cmd_payload("v005-irbthbu", {"command": "start"})
        client.reconnect.assert_not_awaited()


class TestSubscribeAlsoRevivesADeadConnection:
    """Subscribing to a dead connection fails SILENTLY: the watcher then
    observes nothing, and a real robot reaction is reported as "nothing
    happened"."""

    @pytest.mark.asyncio
    async def test_a_dead_connection_is_reconnected_before_subscribing(self) -> None:
        client, fake = _connected_client()
        client._connected = False

        async def revive(**_kw: Any) -> None:
            client._connected = True

        client.reconnect = AsyncMock(side_effect=revive)  # type: ignore[method-assign]

        await client._subscribe_and_wait(["some/topic"], timeout=0.05)

        client.reconnect.assert_awaited_once()
        assert fake.subscribed == ["some/topic"]

    def test_no_bare_assert_remains_in_the_module(self) -> None:
        import inspect

        from roombapy_prime import mqtt_client

        assert "assert self._client is not None" not in inspect.getsource(mqtt_client)


@pytest.mark.asyncio
async def test_a_mission_timeline_request_is_published_on_a_connection() -> None:
    client, fake = _connected_client()

    assert await client.request_mission_timeline("irbt-prefix", 7) is True
    assert json.loads(fake.published[-1][1]) == {"timelineRequestId": 7}


@pytest.mark.asyncio
async def test_publishing_before_connect_is_not_connected() -> None:
    """A caller's mistake, not the cloud's -- a translation should not
    send anyone to check their internet for it."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="x")

    with pytest.raises(ShadowError) as timeline:
        await client.request_mission_timeline("irbt-prefix", 1)
    with pytest.raises(ShadowError) as command:
        await client.publish_cmd_payload("irbt-prefix", {"command": "start"})

    assert timeline.value.reason is CloudErrorReason.NOT_CONNECTED
    assert command.value.reason is CloudErrorReason.NOT_CONNECTED


@pytest.mark.asyncio
async def test_a_publish_on_a_dead_connection_says_so() -> None:
    client, fake = _connected_client()
    client._connected = False

    with pytest.raises(ShadowError, match="no connection") as excinfo:
        await client.request_mission_timeline("irbt-prefix", 1)

    assert excinfo.value.reason is CloudErrorReason.PUBLISH_NOT_DELIVERED
    assert fake.published == []


# --- token refresh, reconnect --------------------------------------------

def test_seconds_until_token_refresh_due_applies_margin() -> None:
    token = ConnectionToken(
        client_id="x", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=time.time() + 1000, devices=[],
    )
    client = PrimeMqttClient(token=token, endpoint="e", blid="x")
    assert 695 < client.seconds_until_token_refresh_due() <= 700  # type: ignore[operator]


def test_seconds_until_token_refresh_due_never_negative() -> None:
    token = ConnectionToken(
        client_id="x", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=time.time() + 10, devices=[],
    )
    client = PrimeMqttClient(token=token, endpoint="e", blid="x")
    assert client.seconds_until_token_refresh_due() == 0.0


def test_seconds_until_token_refresh_due_unknown_expiry_is_none() -> None:
    client, _fake = _connected_client()
    assert client.seconds_until_token_refresh_due() is None


def _swap_connection(client: PrimeMqttClient, new_fake: _FakeAioClient, calls: list[Any]) -> None:
    async def fake_open(timeout: float = 10.0) -> None:
        calls.append(("connect", timeout))
        client._client = new_fake  # type: ignore[assignment]
        client._connected = True
        client._generation += 1

    async def fake_close(deliberate: bool = True) -> None:
        calls.append(("disconnect", deliberate))
        client._connected = False

    client._open = fake_open  # type: ignore[method-assign]
    client._close = fake_close  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_replace_token_swaps_token_reconnects_and_restores_subscriptions() -> None:
    client, _fake = _connected_client()
    received: list[Any] = []
    await client.subscribe("topic/a", lambda resp: received.append(resp.payload))
    await client.subscribe("topic/b", lambda resp: None)
    new_fake = _FakeAioClient()
    calls: list[Any] = []
    _swap_connection(client, new_fake, calls)
    new_token = _dummy_token("new")

    await client.replace_token(new_token, timeout=7.0)

    assert client._token is new_token
    assert calls == [("disconnect", True), ("connect", 7.0)]
    assert set(new_fake.subscribed) == {"topic/a", "topic/b"}
    client._dispatch("topic/a", b'{"ok": true}')
    assert received == [{"ok": True}]


@pytest.mark.asyncio
async def test_reconnect_keeps_the_issued_client_id_and_restores_subscriptions() -> None:
    """THE CLIENT ID IS NOT OURS TO CHOOSE. b2 rotated it on reconnect;
    @DaRealGuGu's run then failed outright -- "Connect timed out" every
    time. The id is issued by iRobot's login, and the broker expects it."""
    client, _fake = _connected_client()
    await client.subscribe("topic/a", lambda resp: None)
    new_fake = _FakeAioClient()
    calls: list[Any] = []
    _swap_connection(client, new_fake, calls)
    original = client._token

    await client.reconnect(timeout=7.0)

    assert client._token is original
    assert calls == [("disconnect", True), ("connect", 7.0)]
    assert new_fake.subscribed == ["topic/a"]


@pytest.mark.asyncio
async def test_reconnect_and_replace_token_before_connect_raise_readably() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="x")

    with pytest.raises(ShadowError) as reconnect:
        await client.reconnect()
    with pytest.raises(ShadowError):
        await client.replace_token(_dummy_token("new"))

    assert "Not connected" in str(reconnect.value)
    assert reconnect.value.reason is CloudErrorReason.NOT_CONNECTED


@pytest.mark.asyncio
async def test_the_lock_serializes_get_shadow_and_replace_token() -> None:
    """A token swap must not tear the connection down in the middle of a
    shadow read: replace_token() waits for the running get_shadow()."""
    client, _fake = _connected_client()
    calls: list[Any] = []
    _swap_connection(client, _FakeAioClient(), calls)
    order: list[str] = []

    async def slow_read() -> None:
        order.append("get start")
        with pytest.raises(ShadowError):
            await client.get_shadow(timeout=0.1)
        order.append("get end")

    read = asyncio.ensure_future(slow_read())
    await asyncio.sleep(0.01)
    order.append("replace start")
    await client.replace_token(_dummy_token("new"))
    order.append("replace end")
    await read

    assert order.index("get end") < order.index("replace end")
    assert order.index("replace start") < order.index("get end")


# --- the connection itself, against a stand-in aiomqtt client ------------

class _LiveFake(_FakeAioClient):
    """Also enters and yields messages, as aiomqtt.Client does."""

    def __init__(self, enter_error: BaseException | None = None) -> None:
        super().__init__()
        self.enter_error = enter_error
        self.queue: asyncio.Queue[Any] = asyncio.Queue()

    async def __aenter__(self) -> _LiveFake:
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.exited = True
        await self.queue.put(MqttError("Disconnected during message iteration"))

    def drop(self, cause: BaseException | None) -> None:
        error = MqttError("Disconnected during message iteration")
        error.__cause__ = cause
        self.queue.put_nowait(error)

    @property
    def messages(self) -> Any:
        fake = self

        class _Messages:
            def __aiter__(self) -> _Messages:
                return self

            async def __anext__(self) -> Any:
                item = await fake.queue.get()
                if isinstance(item, BaseException):
                    raise item
                return item

        return _Messages()


def _message(topic: str, payload: bytes) -> Any:
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload
    return msg


async def _live_client(monkeypatch: pytest.MonkeyPatch, fake: _LiveFake) -> PrimeMqttClient:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")
    monkeypatch.setattr(client, "_build_client", lambda timeout: fake)
    await client.connect(timeout=1.0)
    return client


@pytest.mark.asyncio
async def test_connect_starts_dispatching_incoming_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _LiveFake()
    client = await _live_client(monkeypatch, fake)
    received: list[Any] = []
    await client.subscribe("t/#", lambda resp: received.append(resp.payload))

    await fake.queue.put(_message("t/1", b'{"n": 1}'))
    await asyncio.sleep(0.01)

    assert client._connected is True
    assert received == [{"n": 1}]
    await client.disconnect()


@pytest.mark.asyncio
async def test_a_dropped_connection_is_reported_with_the_brokers_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LiveFake()
    client = await _live_client(monkeypatch, fake)
    client._subscribed_topics.add("a/get/accepted")
    waiting = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)
    assert not waiting.done()

    fake.drop(MqttCodeError(7, "Unexpected disconnection"))

    reason = await asyncio.wait_for(waiting, 1.0)
    assert "[code:7]" in reason
    assert client._connected is False
    assert client.last_disconnect_was_deliberate is False
    assert client._subscribed_topics == set()


@pytest.mark.asyncio
async def test_our_own_disconnect_is_marked_deliberate(monkeypatch: pytest.MonkeyPatch) -> None:
    """@ratpic83: 26 "drops" a day, each 55 minutes apart -- our own
    reconnects, which the watcher then answered with a second reconnect."""
    fake = _LiveFake()
    client = await _live_client(monkeypatch, fake)
    waiting = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)

    await client.disconnect()

    assert await asyncio.wait_for(waiting, 1.0) == "deliberate: token refresh or reconnect"
    assert client.last_disconnect_was_deliberate is True
    assert fake.exited is True


@pytest.mark.asyncio
async def test_wait_for_disconnect_can_be_awaited_again_after_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each call waits for the NEXT drop; a second wait after a reconnect
    must not return at once because the first one fired."""
    first = _LiveFake()
    client = await _live_client(monkeypatch, first)
    first_wait = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)
    first.drop(RuntimeError("first drop"))
    assert await asyncio.wait_for(first_wait, 1.0) == "first drop"

    second = _LiveFake()
    monkeypatch.setattr(client, "_build_client", lambda timeout: second)
    await client.reconnect(timeout=1.0)
    second_wait = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)
    assert not second_wait.done()

    second.drop(RuntimeError("second drop"))
    assert await asyncio.wait_for(second_wait, 1.0) == "second drop"


@pytest.mark.asyncio
async def test_a_disconnect_without_a_message_task_still_records_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LiveFake()
    client = await _live_client(monkeypatch, fake)
    assert client._pump_task is not None
    client._pump_task.cancel()
    await asyncio.sleep(0)

    await client.disconnect()

    assert client._connected is False


def _aiomqtt_style_failure(original: BaseException) -> MqttError:
    """What aiomqtt's __aenter__ raises for a socket-level failure:
    `raise MqttError(str(exc)) from None` inside the except block."""
    try:
        try:
            raise original
        except OSError as exc:
            raise MqttError(str(exc)) from None
    except MqttError as wrapped:
        return wrapped
    raise AssertionError("unreachable")


@pytest.mark.parametrize(
    ("openssl_says", "reason"),
    [
        ("unable to get local issuer certificate", CloudErrorReason.SSL_LOCAL_TRUST_STORE),
        ("certificate has expired", CloudErrorReason.SSL_CERTIFICATE_EXPIRED),
        ("some unfamiliar TLS failure", CloudErrorReason.SSL_UNVERIFIED),
    ],
    ids=["local-trust-store", "expired", "unknown"],
)
@pytest.mark.asyncio
async def test_connect_certificate_failure_is_diagnosed_as_login_diagnoses_it(
    monkeypatch: pytest.MonkeyPatch, openssl_says: str, reason: CloudErrorReason
) -> None:
    """aiomqtt drops the original error from the chain (`from None`);
    the certificate diagnosis needs it, and finds it as __context__."""
    fake = _LiveFake(enter_error=_aiomqtt_style_failure(ssl.SSLCertVerificationError(openssl_says)))

    with pytest.raises(ShadowSSLError) as excinfo:
        await _live_client(monkeypatch, fake)

    assert excinfo.value.reason is reason
    assert isinstance(excinfo.value.__cause__, ssl.SSLError)
    assert "almost always a temporary problem" not in str(excinfo.value)


@pytest.mark.parametrize(
    "original", [ConnectionRefusedError("Connection refused"), OSError("Name or service not known")]
)
@pytest.mark.asyncio
async def test_connect_without_any_connection_is_connection_failed(
    monkeypatch: pytest.MonkeyPatch, original: OSError
) -> None:
    fake = _LiveFake(enter_error=_aiomqtt_style_failure(original))

    with pytest.raises(ShadowConnectionError) as excinfo:
        await _live_client(monkeypatch, fake)

    assert excinfo.value.reason is CloudErrorReason.CONNECTION_FAILED
    assert isinstance(excinfo.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_a_refused_connack_is_connect_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """TLS worked; the broker said no. Not a network problem."""
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode

    fake = _LiveFake(enter_error=MqttConnectError(ReasonCode(PacketTypes.CONNACK, "Not authorized")))

    with pytest.raises(ShadowError, match="Connect failed") as excinfo:
        await _live_client(monkeypatch, fake)

    assert "Not authorized" in str(excinfo.value)
    assert excinfo.value.reason is CloudErrorReason.CONNECT_REFUSED


@pytest.mark.asyncio
async def test_no_connack_in_time_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _LiveFake(enter_error=MqttError("Operation timed out"))

    with pytest.raises(ShadowError, match="timed out after 1.0s") as excinfo:
        await _live_client(monkeypatch, fake)

    assert excinfo.value.reason is CloudErrorReason.TIMEOUT


@pytest.mark.asyncio
async def test_any_other_connect_failure_is_still_a_shadow_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _LiveFake(enter_error=MqttError("something else"))

    with pytest.raises(ShadowError) as excinfo:
        await _live_client(monkeypatch, fake)

    assert excinfo.value.reason is CloudErrorReason.CONNECTION_FAILED


# --- what goes on the wire -----------------------------------------------

class TestTheConnectionIsBuiltAsBefore:
    """aiomqtt hands every setting to the same paho client 0.4.x used.
    These pin the settings, since a changed one would only show in the
    field."""

    def _kwargs(self) -> dict[str, Any]:
        client = PrimeMqttClient(token=_dummy_token("issued-id"), endpoint="e.example.com", blid="B")
        with patch("roombapy_prime.mqtt_client.aiomqtt.Client") as factory:
            client._build_client(8.0)
        (args, kwargs) = factory.call_args
        return {"_args": args, **kwargs}

    def test_only_the_three_confirmed_headers_are_sent(self) -> None:
        """REVERSED in a23: a User-Agent added on an untested third-party
        claim shipped to every consumer in the release that broke Prime
        setup. The app sends exactly three headers."""
        headers = self._kwargs()["websocket_headers"]
        assert set(headers) == {
            "x-amz-customauthorizer-name",
            "x-amz-customauthorizer-signature",
            "x-irobot-auth",
        }
        assert not any(k.lower() == "user-agent" for k in headers)

    def test_websocket_tls_and_the_issued_client_id(self) -> None:
        import ssl as ssl_module

        kwargs = self._kwargs()
        assert kwargs["_args"] == ("e.example.com", 443)
        assert kwargs["transport"] == "websockets"
        assert kwargs["websocket_path"] == "/mqtt"
        assert kwargs["identifier"] == "issued-id"
        assert kwargs["tls_params"].tls_version == ssl_module.PROTOCOL_TLS_CLIENT
        assert kwargs["tls_params"].ca_certs  # certifi's bundle
        assert kwargs["timeout"] == 8.0

    def test_keepalive_is_short_enough_to_notice_a_dead_connection(self) -> None:
        """At 300 s a dead connection went unnoticed for up to 450 s,
        during which publishes looked fine and reached nobody."""
        assert self._kwargs()["keepalive"] == 60

    def test_the_apps_in_flight_window(self) -> None:
        assert self._kwargs()["max_inflight_messages"] == 1000


# --- review findings on the aiomqtt port (0.5.0b1) ------------------------

def _aiomqtt_connack_timeout() -> MqttError:
    """Exactly how aiomqtt reports a CONNACK that did not come: raised
    `from None` inside `except asyncio.TimeoutError`, so __context__ is a
    TimeoutError -- an OSError subclass."""
    try:
        try:
            raise TimeoutError
        except TimeoutError:
            raise MqttError("Operation timed out") from None
    except MqttError as wrapped:
        return wrapped
    raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_a_connack_timeout_as_aiomqtt_raises_it_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Was CONNECTION_FAILED: the context is a TimeoutError, which the
    OSError branch took first. The refresh loop recognises a timeout by
    "timed out" in the message, and logged a full traceback otherwise."""
    fake = _LiveFake(enter_error=_aiomqtt_connack_timeout())

    with pytest.raises(ShadowError) as excinfo:
        await _live_client(monkeypatch, fake)

    assert excinfo.value.reason is CloudErrorReason.TIMEOUT
    assert "timed out" in str(excinfo.value)
    assert not isinstance(excinfo.value, ShadowConnectionError)


@pytest.mark.asyncio
async def test_a_failed_connect_closes_what_it_may_have_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """A late CONNACK completes a connection nobody owns, under the
    robot's client id."""
    fake = _LiveFake(enter_error=_aiomqtt_connack_timeout())
    fake._client = MagicMock()  # the paho client aiomqtt wraps

    with pytest.raises(ShadowError):
        await _live_client(monkeypatch, fake)

    fake._client.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_a_read_that_lost_its_connection_is_subscribed_again_next_time() -> None:
    """The drop cleared `_subscribed_topics`, and the read then added its
    topics back -- so after the reconnect no read of that shadow ever
    subscribed again, and every one timed out."""
    client, fake = _connected_client()

    async def drop_during_suback(_topic: str) -> list[int]:
        client._connection_lost("dropped mid-SUBACK")
        return [1]

    fake.suback = drop_during_suback

    async def revive(**_kw: Any) -> None:
        client._connected = True

    client.reconnect = AsyncMock(side_effect=revive)  # type: ignore[method-assign]
    with pytest.raises(ShadowError):
        await client.get_shadow(timeout=0.05)
    assert client._subscribed_topics == set()

    fake.suback = lambda _t: [1]
    fake.subscribed.clear()
    fake.on_publish_react = _react_with(client, "get", "accepted", {})
    await client.get_shadow(timeout=1.0)

    assert len(fake.subscribed) == 2


@pytest.mark.asyncio
async def test_connect_starts_with_nothing_subscribed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")
    client._subscribed_topics.add("stale/get/accepted")
    monkeypatch.setattr(client, "_build_client", lambda timeout: _LiveFake())

    await client.connect(timeout=1.0)

    assert client._subscribed_topics == set()
    await client.disconnect()


@pytest.mark.asyncio
async def test_subscribes_of_a_connection_that_dropped_meanwhile_are_not_kept() -> None:
    client, _fake = _connected_client(fake=_FakeAioClient(suback=lambda _t: NEVER))

    async def drop_soon() -> None:
        await asyncio.sleep(0.005)
        client._connection_lost("gone")

    dropper = asyncio.ensure_future(drop_soon())
    await client._subscribe_and_wait(["a/topic"], timeout=0.03)
    await dropper
    await asyncio.sleep(0)

    assert client._subscribe_tasks == {}
    assert client.resubscribe_still_unconfirmed() == []


@pytest.mark.asyncio
async def test_a_cancelled_subscribe_is_left_to_its_connection() -> None:
    """A cancelled caller no longer cancels its SUBSCRIBE: aiomqtt then
    forgets it, and the SUBACK arriving later is logged as an error with
    a traceback (review finding). The connection cancels what is still
    waiting when it ends -- and nothing is left over after that."""
    fake = _FakeAioClient(suback=lambda _t: NEVER)
    client, _fake = _connected_client(fake=fake)
    started = asyncio.Event()
    original = fake.subscribe

    async def tracking(topic: str, qos: int = 0, timeout: float | None = None) -> Any:
        started.set()
        return await original(topic, qos, timeout)

    fake.subscribe = tracking  # type: ignore[method-assign]
    waiting = asyncio.ensure_future(client._subscribe_and_wait(["a/topic"], timeout=10.0))
    await started.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await asyncio.sleep(0)

    assert len(client._inflight) == 1
    assert client.resubscribe_still_unconfirmed() == []  # not a watcher's topic
    client._connection_lost("gone")
    await asyncio.sleep(0)

    me = asyncio.current_task()
    pending = [
        t for t in asyncio.all_tasks()
        if t is not me and "subscribe" in repr(t.get_coro()) and not t.done()
    ]
    assert pending == []


@pytest.mark.asyncio
async def test_disconnect_before_any_connect_leaves_no_deliberate_flag() -> None:
    """The flag survived into the first real connection, and its first
    real drop read as deliberate -- no reconnect."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")

    await client.disconnect()

    assert client._deliberate_disconnect is False


@pytest.mark.asyncio
async def test_a_subscribe_during_a_token_swap_does_not_open_a_second_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """watch_live_map re-subscribes on every disconnect, including the
    token swap's own. Its lazy reconnect ran alongside the swap: two live
    connections with one client id, which AWS IoT answers by evicting one."""
    first = _LiveFake()
    client = await _live_client(monkeypatch, first)
    built: list[_LiveFake] = []

    def build(timeout: float) -> _LiveFake:
        fake = _LiveFake()
        original_enter = fake.__aenter__

        async def slow_enter() -> _LiveFake:
            await asyncio.sleep(0.02)
            return await original_enter()

        fake.__aenter__ = slow_enter  # type: ignore[method-assign]
        built.append(fake)
        return fake

    monkeypatch.setattr(client, "_build_client", build)
    swap = asyncio.ensure_future(client.replace_token(_dummy_token("new"), timeout=1.0))
    await asyncio.sleep(0.005)  # inside the swap: disconnected, not yet connected
    await client.subscribe("t/#", print)
    await swap

    assert len(built) == 1
    await client.disconnect()


@pytest.mark.asyncio
async def test_resubscribing_with_the_same_callback_does_not_duplicate_it() -> None:
    """After N drops every message arrived N+1 times, and unsubscribe()
    removed one copy -- the topic was never released."""
    client, fake = _connected_client()
    received: list[Any] = []

    def on_message(resp: ShadowResponse) -> None:
        received.append(resp.payload)

    await client.subscribe("t", on_message)
    await client.subscribe("t", on_message)
    client._dispatch("t", b"1")
    await client.unsubscribe("t", on_message)

    assert received == [1]
    assert "t" in fake.unsubscribed


@pytest.mark.asyncio
async def test_every_waiter_hears_of_a_drop() -> None:
    """0.4.x kept one event, replaced by each wait_for_disconnect() call:
    with several watchers only the last one armed was woken."""
    client, _fake = _connected_client()
    first = asyncio.ensure_future(client.wait_for_disconnect())
    second = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)

    client._connection_lost("gone")

    assert await asyncio.wait_for(first, 1.0) == "gone"
    assert await asyncio.wait_for(second, 1.0) == "gone"
    assert client._disconnect_waiters == []


@pytest.mark.asyncio
async def test_a_failed_reconnect_is_reported_to_the_watchers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token swap consumes the planned disconnect; the watchers re-arm.
    If the new connection then fails, no drop can come -- so the failure
    is reported as one, and the watchers take over with their backoff."""
    client = await _live_client(monkeypatch, _LiveFake())

    class _SlowFailure(_LiveFake):
        async def __aenter__(self) -> _LiveFake:
            await asyncio.sleep(0.05)
            raise MqttError("Operation timed out")

    monkeypatch.setattr(client, "_build_client", lambda timeout: _SlowFailure())
    generation = client.generation
    planned = asyncio.ensure_future(client.wait_for_disconnect(generation))
    await asyncio.sleep(0.01)
    swap = asyncio.ensure_future(client.replace_token(_dummy_token("new"), timeout=1.0))

    assert await asyncio.wait_for(planned, 1.0) == "deliberate: token refresh or reconnect"
    with pytest.raises(ShadowError):
        await swap

    # Nothing is up and nothing is rebuilding: asking again answers at
    # once, however late -- the watcher then rebuilds.
    assert await asyncio.wait_for(client.wait_for_disconnect(generation), 0.1)
    assert client.disconnect_reason is not None
    assert client.disconnect_reason.startswith("reconnect failed")
    assert client.last_disconnect_was_deliberate is False
    assert not client.connected


@pytest.mark.asyncio
async def test_a_waiting_reconnect_does_not_repeat_one_that_just_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _live_client(monkeypatch, _LiveFake())
    builds: list[int] = []

    def build(timeout: float) -> _LiveFake:
        builds.append(1)
        return _LiveFake()

    monkeypatch.setattr(client, "_build_client", build)

    await asyncio.gather(client.reconnect(timeout=1.0), client.reconnect(timeout=1.0))

    assert len(builds) == 1
    await client.disconnect()


# --- second review of 0.5.0b1 ---------------------------------------------


@pytest.mark.asyncio
async def test_a_subscribe_overlapping_a_reconnect_reaches_the_new_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconnect restored the topics registered when it started; a new
    topic registers only after its SUBACK wait. The new connection never
    heard of it, and nothing reported it as unconfirmed (review finding;
    0.4.x too)."""
    first = _LiveFake()
    first.suback = lambda _t: NEVER
    client = await _live_client(monkeypatch, first)
    client.SUBACK_TIMEOUT_SECONDS = 0.1
    second = _LiveFake()
    monkeypatch.setattr(client, "_build_client", lambda timeout: second)

    subscribing = asyncio.ensure_future(client.subscribe("t/new", print))
    await asyncio.sleep(0.02)
    await client.reconnect(timeout=1.0)
    await subscribing

    assert "t/new" in second.subscribed
    await client.disconnect()


@pytest.mark.asyncio
async def test_a_cancelled_connect_closes_the_connection_it_leaves_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """aiomqtt's connect runs in a worker thread that a cancel does not
    stop: the handshake completed, and the connection pinged on with
    nobody holding it (review finding, new in 0.5)."""
    class _SlowEnter(_LiveFake):
        async def __aenter__(self) -> _LiveFake:
            await asyncio.sleep(0.05)
            return self

    fake = _SlowEnter()
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")
    monkeypatch.setattr(client, "_build_client", lambda timeout: fake)

    connecting = asyncio.ensure_future(client.connect(timeout=1.0))
    await asyncio.sleep(0.01)
    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connecting
    await asyncio.sleep(0.1)

    assert fake.exited, "the late connection must be closed"
    assert not client.connected


@pytest.mark.asyncio
async def test_a_reconnect_cancelled_after_its_close_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a ShadowError was reported, so a cancelled reconnect left the
    watchers asleep on a connection it had closed (review finding)."""
    client = await _live_client(monkeypatch, _LiveFake())
    generation = client.generation

    class _Slow(_LiveFake):
        async def __aenter__(self) -> _LiveFake:
            await asyncio.sleep(1.0)
            return self

    monkeypatch.setattr(client, "_build_client", lambda timeout: _Slow())
    waiter = asyncio.ensure_future(client.wait_for_disconnect(generation))
    rebuilding = asyncio.ensure_future(client.reconnect(timeout=2.0))
    await asyncio.sleep(0.02)
    rebuilding.cancel()
    await asyncio.wait({rebuilding})

    assert await asyncio.wait_for(waiter, 0.5)
    assert client.disconnect_reason == "reconnect cancelled"
    assert client.last_disconnect_was_deliberate is False


@pytest.mark.asyncio
async def test_disconnect_does_not_raise_a_cancel_it_did_not_receive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When aiomqtt gives up on its own DISCONNECT it cancels a future the
    message task waits on; the task then ends cancelled, and wait_for()
    raised that out of disconnect() as if the caller had been cancelled
    (review finding)."""
    fake = _LiveFake()
    client = await _live_client(monkeypatch, fake)

    async def aexit_that_cancels_the_pump(*_exc: Any) -> None:
        assert client._pump_task is not None
        client._pump_task.cancel()

    fake.__aexit__ = aexit_that_cancels_the_pump  # type: ignore[method-assign]

    await client.disconnect()  # must not raise

    assert not client.connected


@pytest.mark.asyncio
async def test_connect_on_a_connected_client_builds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds: list[int] = []
    client = await _live_client(monkeypatch, _LiveFake())

    def build(timeout: float) -> _LiveFake:
        builds.append(1)
        return _LiveFake()

    monkeypatch.setattr(client, "_build_client", build)
    await client.connect(timeout=1.0)

    assert builds == []
    await client.disconnect()


@pytest.mark.asyncio
async def test_wait_for_disconnect_answers_at_once_for_a_connection_already_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _LiveFake()
    client = await _live_client(monkeypatch, fake)
    generation = client.generation
    fake.drop(RuntimeError("gone"))
    await asyncio.sleep(0.01)

    reason = await asyncio.wait_for(client.wait_for_disconnect(generation), 0.1)

    assert reason == "gone"
    assert client.ended(generation) == ("gone", False)


def test_paho_and_aiomqtt_log_at_debug_only(caplog: pytest.LogCaptureFixture) -> None:
    """aiomqtt switches paho's logging on: "failed to receive on socket"
    at ERROR on every connection reset, and "Unexpected message ID" with
    a traceback for a SUBACK nobody waits for. 0.4.0 logged neither
    (review finding)."""
    from roombapy_prime.mqtt_client import _DebugOnlyLogger

    target = logging.getLogger("roombapy_prime.mqtt_client.paho")
    quiet = _DebugOnlyLogger(target)

    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    quiet.log(logging.ERROR, "failed to receive on socket: %s", "reset")
    quiet.warning("There are %d pending publish calls.", 11)
    assert caplog.records == []

    caplog.set_level(logging.DEBUG, logger="roombapy_prime")
    quiet.error("Unexpected message ID %d", 7)
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.DEBUG, "Unexpected message ID 7")
    ]


@pytest.mark.asyncio
async def test_the_client_hands_aiomqtt_the_quiet_logger() -> None:
    from roombapy_prime.mqtt_client import _DebugOnlyLogger

    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")
    built = client._build_client(1.0)

    assert isinstance(built._logger, _DebugOnlyLogger)


@pytest.mark.asyncio
async def test_an_abandoned_connect_leaves_paho_a_future_it_can_ask() -> None:
    """A CONNACK wait cancelled with its caller leaves aiomqtt's
    `_connected` cancelled; paho's disconnect callback then raised
    CancelledError inside paho, before the socket was closed (review
    finding)."""
    from roombapy_prime.mqtt_client import _abandon

    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")
    built = client._build_client(1.0)
    built._connected.cancel()

    _abandon(built)

    built._on_disconnect(built._client, None, None, 0)  # must not raise


@pytest.mark.asyncio
async def test_without_a_generation_the_wait_is_for_the_next_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller written for the old API loops on wait_for_disconnect()
    after a drop. Answering at once with nothing up would make that loop
    spin without ever yielding -- found re-running the review's
    reproductions."""
    fake = _LiveFake()
    client = await _live_client(monkeypatch, fake)
    fake.drop(RuntimeError("gone"))
    await asyncio.sleep(0.01)

    waiting = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.05)

    assert not waiting.done()
    waiting.cancel()
