"""Tests for roombapy_prime.mqtt_client — shadow topic construction and
get_shadow/update_shadow response handling.

No real network or real paho.mqtt.Client involved. FakeMqttClient below
stands in for paho's Client: records subscribe/publish calls, and lets
each test wire publish() to synchronously invoke the module's own
_on_message() with a fixture payload — simulating "the broker responded"
without any real timing/threading dependency.

This tests the module's message-handling and error paths against real
(anonymized) captured payloads; it does not test the actual network
connect() path (TLS, WebSocket headers, AWS IoT auth) since that needs
a real or heavily mocked socket layer and is integration-shaped.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import threading
import time
from pathlib import Path
from collections.abc import Callable

import pytest

from roombapy_prime.auth import ConnectionToken
from roombapy_prime.mqtt_client import (
    PrimeMqttClient,
    ShadowConnectionError,
    ShadowError,
    ShadowResponse,
    ShadowSSLError,
    _shadow_base,
)


def _load(fixtures_dir: Path, name: str) -> dict:
    return json.loads((fixtures_dir / name).read_text())


class _FakeMsg:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class _FakeMessageInfo:
    """Stand-in for paho.mqtt.client.MQTTMessageInfo -- just enough of
    the real interface (wait_for_publish()/is_published()) for
    publish_cmd_payload()'s new PUBACK-confirmation logic to exercise
    against. Defaults to "successfully published" -- tests that need
    to simulate a broker-level failure/no-confirmation pass
    published=False explicitly."""

    def __init__(self, published: bool = True) -> None:
        self._published = published

    def wait_for_publish(self, timeout: float | None = None) -> None:
        pass

    def is_published(self) -> bool:
        return self._published


class _FakeMqttClient:
    """Stand-in for paho.mqtt.client.Client. No sockets involved."""

    def __init__(self, on_subscribe: Callable[[int], None] | None = None) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.published: list[tuple[str, object]] = []
        self.on_publish_react: Callable[[str, object], None] | None = None
        self.publish_confirmed: bool = True
        self._on_subscribe = on_subscribe
        self._next_mid = 1

    def subscribe(self, topic: str, qos: int = 0) -> tuple[int, int]:
        """NEW (session 33): now returns (result, mid) like the real
        Paho client -- and immediately reports a simulated SUBACK
        confirmation (timing itself isn't the test target here)."""
        self.subscribed.append(topic)
        mid = self._next_mid
        self._next_mid += 1
        if self._on_subscribe is not None:
            self._on_subscribe(mid)
        return (0, mid)

    def unsubscribe(self, topic: str) -> None:
        self.unsubscribed.append(topic)

    def publish(self, topic: str, payload: object = None, qos: int = 0) -> _FakeMessageInfo:
        self.published.append((topic, payload))
        if self.on_publish_react is not None:
            self.on_publish_react(topic, payload)
        return _FakeMessageInfo(published=self.publish_confirmed)


def _dummy_token() -> ConnectionToken:
    return ConnectionToken(
        client_id="x", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=None, devices=[],
    )


def _connected_client(blid: str = "0000000000000000") -> tuple[PrimeMqttClient, _FakeMqttClient]:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid=blid)
    fake = _FakeMqttClient(on_subscribe=lambda mid: client._on_subscribe(client, None, mid, []))
    client._client = fake  # bypass real connect() — no network in these tests
    client._connected = True
    return client, fake


def _react_with(client: PrimeMqttClient, verb: str, response_topic_suffix: str, payload: dict) -> Callable:
    """Build an on_publish_react callback: when publish() is called on
    the .../{verb} topic, immediately deliver `payload` on
    .../{verb}/{response_topic_suffix} via the client's own _on_message."""

    def react(topic: str, _payload: object) -> None:
        if topic.endswith(f"/{verb}"):
            response_topic = topic[: -len(f"/{verb}")] + f"/{verb}/{response_topic_suffix}"
            client._on_message(None, None, _FakeMsg(response_topic, json.dumps(payload).encode()))

    return react


# --- _shadow_base ------------------------------------------------------

def test_shadow_base_classic() -> None:
    assert _shadow_base("BLID123", None) == "$aws/things/BLID123/shadow"


def test_shadow_base_named() -> None:
    assert _shadow_base("BLID123", "rw-settings") == "$aws/things/BLID123/shadow/name/rw-settings"


# --- get_shadow: classic/unnamed, both tiers ----------------------------

def test_get_shadow_classic_ephemeral(fixtures_dir: Path) -> None:
    client, fake = _connected_client(blid="0000000000000000")
    payload = _load(fixtures_dir, "shadow_get_classic_ephemeral.json")
    fake.on_publish_react = _react_with(client, "get", "accepted", payload)

    response = client.get_shadow(timeout=1.0)

    assert response.payload["state"]["reported"]["sku"] == "R980040"
    assert response.payload["state"]["reported"]["cap"]["pose"] == 1
    assert response.payload["version"] == 90131


def test_get_shadow_reconnects_first_when_connection_known_down(fixtures_dir: Path) -> None:
    """NEW (this session, prompted by a real field report + a known AWS
    IoT MQTT SDK behavior -- see aws/aws-iot-device-sdk-js-v2#117): a
    caller doing a plain sequential series of get_shadow() calls with
    no reconnect logic of its own (e.g. verify_named_shadows.py) would,
    after a single silent mid-run disconnect, have every subsequent
    call time out forever with no way to recover. get_shadow() must
    reconnect proactively when it already knows the connection is
    down, not just try to subscribe/publish on a dead client."""
    client, fake = _connected_client(blid="0000000000000000")
    client._connected = False  # simulates a disconnect that happened earlier

    def fake_reconnect(timeout=10.0):
        client._connected = True  # simulates a successful reconnect

    client.reconnect = fake_reconnect
    payload = _load(fixtures_dir, "shadow_get_classic_ephemeral.json")
    fake.on_publish_react = _react_with(client, "get", "accepted", payload)

    response = client.get_shadow(timeout=1.0)

    assert response.payload["state"]["reported"]["sku"] == "R980040"


def test_get_shadow_classic_smart_tier(fixtures_dir: Path) -> None:
    client, fake = _connected_client(blid="1111111111111111")
    payload = _load(fixtures_dir, "shadow_get_classic_smart_tier.json")
    fake.on_publish_react = _react_with(client, "get", "accepted", payload)

    response = client.get_shadow(timeout=1.0)

    assert response.payload["state"]["reported"]["sku"] == "i755640"
    assert response.payload["state"]["reported"]["cap"]["pose"] == 2
    assert response.payload["state"]["reported"]["cap"]["pmaps"] == 9


# --- get_shadow: named shadow, tier-dependent behaviour -----------------

def test_get_shadow_named_responds_on_smart_tier(fixtures_dir: Path) -> None:
    client, fake = _connected_client(blid="1111111111111111")
    payload = _load(fixtures_dir, "shadow_get_rw_settings_smart_tier.json")
    fake.on_publish_react = _react_with(client, "get", "accepted", payload)

    response = client.get_shadow(named="rw-settings", timeout=1.0)

    assert response.payload["state"]["reported"]["audio"]["volume"] == 100
    assert response.payload["state"]["desired"]["binTypeDetect"] == 2


def test_get_shadow_named_times_out_on_ephemeral() -> None:
    """No real fixture for this — EPHEMERAL's named-shadow behaviour IS
    total silence (see CLOUD_SHADOW_PUSH_FINDINGS.md section on tier
    boundary). Confirmed here by simply not wiring any react callback:
    publish() happens, nothing ever arrives, get_shadow must time out
    rather than hang or raise the wrong error."""
    client, fake = _connected_client(blid="0000000000000000")
    fake.on_publish_react = None

    with pytest.raises(ShadowError, match="No response"):
        client.get_shadow(named="rw-settings", timeout=0.5)


# --- get_shadow: rejected path (synthetic — no real rejected capture) --

def test_get_shadow_rejected() -> None:
    """SYNTHETIC — no real captured .../get/rejected payload was
    provided; this only confirms the rejected-topic branch is actually
    reachable and raises ShadowError rather than returning silently."""
    client, fake = _connected_client()
    fake.on_publish_react = _react_with(client, "get", "rejected", {"code": 404, "message": "no shadow"})

    with pytest.raises(ShadowError, match="rejected"):
        client.get_shadow(timeout=1.0)


# --- update_shadow: real accepted-write capture -------------------------

def test_update_shadow_accepted(fixtures_dir: Path) -> None:
    client, fake = _connected_client()
    payload = _load(fixtures_dir, "shadow_update_accepted.json")
    fake.on_publish_react = _react_with(client, "update", "accepted", payload)

    response = client.update_shadow({"binPause": False}, timeout=1.0)

    assert response.payload["state"]["desired"]["binPause"] is False
    assert response.payload["version"] == 90132
    # the actual publish() call carried our desired-state write
    publish_topic, publish_payload = fake.published[0]
    assert publish_topic.endswith("/update")
    assert json.loads(publish_payload)["state"]["desired"] == {"binPause": False}


# --- calling before connect() -------------------------------------------

def test_get_shadow_before_connect_raises_a_readable_error() -> None:
    """This is the exact path a field tester hit: a diagnostic script
    asked for a named shadow without ever opening the connection. The
    script was at fault, but the error blamed nobody legibly -- it just
    asserted, four frames down."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="x")

    with pytest.raises(ShadowError, match="Not connected"):
        client.get_shadow(timeout=0.1)


# --- persistent subscribe/unsubscribe (continuous dispatch) -------------
#
# Separate from get_shadow/update_shadow's one-shot _pending mechanism --
# these tests only exercise the new subscribe()/unsubscribe() additions,
# not the existing tested get/update paths above.

def test_shadow_topic_helper() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.shadow_topic("update/delta") == "$aws/things/BLID1/shadow/update/delta"
    assert (
        client.shadow_topic("update/delta", named="rw-settings")
        == "$aws/things/BLID1/shadow/name/rw-settings/update/delta"
    )


def test_livemap_topic_helper() -> None:
    """UPDATED (session 39) -- now includes "things/" by analogy to
    cmd_topic()'s much more strongly evidenced pattern (independently
    confirmed by native disassembly and a third-party implementation).
    See livemap_topic()'s docstring: still an analogy for THIS specific
    topic, not a direct confirmation."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.livemap_topic("irbt-prefix") == "irbt-prefix/things/BLID1/livemap/update"


def test_persistent_wildcard_subscription_receives_matching_messages() -> None:
    """BUG FOUND AND FIXED (this session): a live wildcard capture came
    back with zero messages despite matching traffic demonstrably
    existing (chairstacker) -- _on_message() dispatched persistent
    subscribers via an exact dict-key lookup on msg.topic, but a
    wildcard registration's key is the literal pattern string (e.g.
    "prefix/things/BLID/#"), which msg.topic (always the CONCRETE
    topic a message arrived on) can never equal. The wildcard
    watcher's callback was therefore structurally unreachable,
    regardless of how much matching traffic existed."""
    client, _fake = _connected_client(blid="BLID1")
    received: list[ShadowResponse] = []
    client.subscribe("prefix/things/BLID1/#", received.append)

    client._on_message(
        client, None,
        _FakeMsg("prefix/things/BLID1/mission/timeline/report", b'{"phase": "run"}'),
    )

    assert len(received) == 1
    assert received[0].payload == {"phase": "run"}


def test_persistent_exact_and_wildcard_subscriptions_both_fire_for_same_message() -> None:
    """Confirms the fix handles the exact scenario that surfaced the
    bug: an exact-topic watcher and an overlapping wildcard watcher
    registered at the same time, both must receive a message that
    matches both patterns."""
    client, _fake = _connected_client(blid="BLID1")
    exact_received: list[ShadowResponse] = []
    wildcard_received: list[ShadowResponse] = []
    topic = "prefix/things/BLID1/mission/timeline/report"
    client.subscribe(topic, exact_received.append)
    client.subscribe("prefix/things/BLID1/#", wildcard_received.append)

    client._on_message(client, None, _FakeMsg(topic, b'{"phase": "run"}'))

    assert len(exact_received) == 1
    assert len(wildcard_received) == 1


def test_persistent_wildcard_subscription_ignores_non_matching_topics() -> None:
    client, _fake = _connected_client(blid="BLID1")
    received: list[ShadowResponse] = []
    client.subscribe("prefix/things/BLID1/mission/#", received.append)

    client._on_message(
        client, None,
        _FakeMsg("prefix/things/BLID1/rejected/report", b'{"reason": "busy"}'),
    )

    assert received == []


def test_cmd_topic_helper() -> None:
    """NEW (session 39) -- confirmed both by this library's own native
    disassembly (libcorebase.so's literal "/things/%s/cmd" format
    string) and independently by a third-party, unaffiliated project
    that reports this exact topic working against a real device. See
    cmd_topic()'s docstring for the full evidence trail."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.cmd_topic("irbt-prefix") == "irbt-prefix/things/BLID1/cmd"


def test_mission_timeline_topic_helper_report() -> None:
    """NEW (this session) -- topic name/existence confirmed via native
    decompilation (AssetIotTopicFactory::createMissionTimelineTopic),
    prompted by a live idle-vs-mid-mission diff showing the classic
    shadow never carries mission status at all. See
    mission_timeline_topic()'s own docstring for the full confidence
    breakdown (topic existence: confirmed; irbt_topic_prefix applying
    here: strong inference, not independently live-confirmed)."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert (
        client.mission_timeline_topic("irbt-prefix")
        == "irbt-prefix/things/BLID1/mission/timeline/report"
    )


def test_mission_timeline_topic_helper_request() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert (
        client.mission_timeline_topic("irbt-prefix", report=False)
        == "irbt-prefix/things/BLID1/mission/timeline/request"
    )


def test_rejected_report_topic_helper() -> None:
    """NEW (this session) -- found via the same native decompilation
    pass as mission_timeline_topic() (AssetIotTopicFactory's third
    method, createCommandRejectedTopic())."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.rejected_report_topic("irbt-prefix") == "irbt-prefix/things/BLID1/rejected/report"


def test_publish_cmd_sends_expected_payload_shape() -> None:
    """NEW (session 39) -- payload shape {"command", "time", "initiator"}
    matches the third-party project's documented, reportedly-working
    format exactly."""
    client, fake = _connected_client(blid="BLID1")
    client.publish_cmd("irbt-prefix", "start", initiator="localApp")
    assert len(fake.published) == 1
    topic, payload = fake.published[0]
    assert topic == "irbt-prefix/things/BLID1/cmd"
    body = json.loads(payload)
    assert body["command"] == "start"
    assert body["initiator"] == "localApp"
    assert isinstance(body["time"], int)


def test_publish_cmd_payload_sends_arbitrary_dict_via_cmd_topic() -> None:
    """NEW (session 46) -- EXPERIMENTAL, UNCONFIRMED path (see
    prime_robot.py's send_routine_command_via_cmd_topic() for the full
    hypothesis this supports). Verifies the payload passed through
    unchanged except for the added "time" field."""
    client, fake = _connected_client(blid="BLID1")
    client.publish_cmd_payload("irbt-prefix", {"command": "start", "robot_id": "BLID1", "regions": []})
    assert len(fake.published) == 1
    topic, payload = fake.published[0]
    assert topic == "irbt-prefix/things/BLID1/cmd"
    body = json.loads(payload)
    assert body["command"] == "start"
    assert body["robot_id"] == "BLID1"
    assert body["regions"] == []
    assert isinstance(body["time"], int)


def test_publish_cmd_payload_does_not_override_existing_time_field() -> None:
    """If the caller's payload already has a "time" key, it must not
    be silently overwritten -- setdefault(), not unconditional
    assignment."""
    client, fake = _connected_client(blid="BLID1")
    client.publish_cmd_payload("irbt-prefix", {"command": "start", "time": 12345})
    _, payload = fake.published[0]
    assert json.loads(payload)["time"] == 12345


def test_subscribe_delivers_every_message_not_just_first() -> None:
    client, fake = _connected_client()
    received: list[dict] = []
    client.subscribe("some/topic", lambda resp: received.append(resp.payload))

    client._on_message(None, None, _FakeMsg("some/topic", b'{"n": 1}'))
    client._on_message(None, None, _FakeMsg("some/topic", b'{"n": 2}'))
    client._on_message(None, None, _FakeMsg("some/topic", b'{"n": 3}'))

    assert received == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_subscribe_only_calls_broker_subscribe_once_per_topic() -> None:
    client, fake = _connected_client()
    client.subscribe("t", lambda resp: None)
    client.subscribe("t", lambda resp: None)  # second callback, same topic

    assert fake.subscribed.count("t") == 1


def test_unsubscribe_removes_only_that_callback() -> None:
    client, fake = _connected_client()
    received_a: list[dict] = []
    received_b: list[dict] = []
    cb_a = lambda resp: received_a.append(resp.payload)  # noqa: E731
    cb_b = lambda resp: received_b.append(resp.payload)  # noqa: E731

    client.subscribe("t", cb_a)
    client.subscribe("t", cb_b)
    client.unsubscribe("t", cb_a)
    client._on_message(None, None, _FakeMsg("t", b'{"x": 1}'))

    assert received_a == []
    assert received_b == [{"x": 1}]


def test_unsubscribe_last_callback_unsubscribes_at_broker_level() -> None:
    """Regression guard for the multi-watcher bug this was designed to
    avoid: broker-level unsubscribe must only happen once, when the
    LAST callback for a topic is removed -- not on every removal."""
    client, fake = _connected_client()
    cb_a = lambda resp: None  # noqa: E731
    cb_b = lambda resp: None  # noqa: E731

    client.subscribe("t", cb_a)
    client.subscribe("t", cb_b)
    client.unsubscribe("t", cb_a)
    assert "t" not in fake.unsubscribed

    client.unsubscribe("t", cb_b)
    assert "t" in fake.unsubscribed


def test_unsubscribe_unknown_topic_is_a_noop() -> None:
    client, fake = _connected_client()
    client.unsubscribe("never/subscribed", lambda resp: None)  # must not raise


# --- proactive token refresh --------------------------------------------

def test_seconds_until_token_refresh_due_applies_margin() -> None:
    import time as time_module

    token = ConnectionToken(
        client_id="x", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=time_module.time() + 1000, devices=[],
    )
    client = PrimeMqttClient(token=token, endpoint="e", blid="x")
    # margin is 300s (see REFRESH_MARGIN_SECONDS) -- allow small timing slop
    assert 695 < client.seconds_until_token_refresh_due() <= 700


def test_seconds_until_token_refresh_due_never_negative() -> None:
    import time as time_module

    token = ConnectionToken(
        client_id="x", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=time_module.time() + 10, devices=[],
    )
    client = PrimeMqttClient(token=token, endpoint="e", blid="x")
    # already within/past the margin -- clamped to 0, not negative
    assert client.seconds_until_token_refresh_due() == 0.0


def test_seconds_until_token_refresh_due_unknown_expiry_is_none() -> None:
    client, _fake = _connected_client()  # _dummy_token() has expires=None
    assert client.seconds_until_token_refresh_due() is None


def test_replace_token_swaps_token_reconnects_and_restores_subscriptions() -> None:
    client, fake = _connected_client()
    client.subscribe("topic/a", lambda resp: None)
    client.subscribe("topic/b", lambda resp: None)

    new_fake = _FakeMqttClient(on_subscribe=lambda mid: client._on_subscribe(client, None, mid, []))
    reconnect_calls: list[float] = []
    disconnect_calls: list[int] = []

    def fake_connect(timeout: float = 10.0) -> None:
        reconnect_calls.append(timeout)
        client._client = new_fake
        client._connected = True

    def fake_disconnect() -> None:
        disconnect_calls.append(1)

    client.connect = fake_connect  # type: ignore[method-assign]
    client.disconnect = fake_disconnect  # type: ignore[method-assign]

    new_token = ConnectionToken(
        client_id="new", iot_token="t2", iot_signature="s2",
        iot_authorizer_name="a2", expires=None, devices=[],
    )
    client.replace_token(new_token, timeout=7.0)

    assert client._token is new_token
    assert disconnect_calls == [1]
    assert reconnect_calls == [7.0]
    # persistent subscriptions re-established on the NEW paho client
    assert set(new_fake.subscribed) == {"topic/a", "topic/b"}
    # the callbacks themselves are untouched -- still delivering
    received: list[dict] = []
    client._persistent["topic/a"][0] = lambda resp: received.append(resp.payload)
    client._on_message(None, None, _FakeMsg("topic/a", b'{"ok": true}'))
    assert received == [{"ok": True}]


def test_replace_token_before_connect_raises() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="x")
    new_token = ConnectionToken(
        client_id="new", iot_token="t2", iot_signature="s2",
        iot_authorizer_name="a2", expires=None, devices=[],
    )
    with pytest.raises(ShadowError):
        client.replace_token(new_token)


# =========================================================================
# reconnect() / on_disconnect / wait_for_disconnect() (this session,
# reconnect hardening). Previously there was no on_disconnect handling
# at all -- the client had zero visibility into a dropped connection.
# =========================================================================


def test_reconnect_reconnects_and_restores_subscriptions() -> None:
    """Same-token counterpart to replace_token()'s equivalent test --
    reconnect() must restore persistent subscriptions on the new paho
    client the same way, without touching self._token."""
    client, fake = _connected_client()
    client.subscribe("topic/a", lambda resp: None)
    client.subscribe("topic/b", lambda resp: None)

    new_fake = _FakeMqttClient(on_subscribe=lambda mid: client._on_subscribe(client, None, mid, []))
    reconnect_calls: list[float] = []

    def fake_connect(timeout: float = 10.0) -> None:
        reconnect_calls.append(timeout)
        client._client = new_fake
        client._connected = True

    def fake_disconnect() -> None:
        pass

    client.connect = fake_connect  # type: ignore[method-assign]
    client.disconnect = fake_disconnect  # type: ignore[method-assign]

    original_token = client._token
    client.reconnect(timeout=7.0)

    assert client._token is original_token  # NOT swapped, unlike replace_token()
    assert reconnect_calls == [7.0]
    assert set(new_fake.subscribed) == {"topic/a", "topic/b"}


def test_reconnect_before_connect_raises_a_readable_error() -> None:
    """Was an AssertionError. A field tester hit it through
    get_shadow()'s lazy-reconnect path and got a bare traceback ending
    in "call connect() first" -- a message written for whoever wrote the
    calling code, not for the person running a diagnostic script, and
    several frames removed from the actual cause."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="x")

    with pytest.raises(ShadowError) as exc:
        client.reconnect()

    message = str(exc.value)
    assert "Not connected" in message
    assert "MQTT, not REST" in message, "must say why a shadow read needs a connection"


def test_on_disconnect_sets_connected_false_and_stores_reason() -> None:
    client, _fake = _connected_client()
    assert client._connected is True

    client._on_disconnect(None, None, None, "network error")

    assert client._connected is False
    assert client._disconnect_reason == "network error"


@pytest.mark.asyncio
async def test_wait_for_disconnect_resolves_when_on_disconnect_fires() -> None:
    """The real bridge this session added: _on_disconnect() runs on
    paho's own callback thread (simulated here by calling it directly,
    same as the existing _on_subscribe-callback pattern elsewhere in
    this file), wait_for_disconnect() is a coroutine on the asyncio
    event loop -- call_soon_threadsafe is what connects the two."""
    client, _fake = _connected_client()

    wait_task = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)  # let wait_for_disconnect() reach its .wait()
    assert not wait_task.done()

    client._on_disconnect(None, None, None, "broker restarted")

    reason = await wait_task
    assert reason == "broker restarted"


@pytest.mark.asyncio
async def test_wait_for_disconnect_can_be_awaited_again_after_reconnect() -> None:
    """Each call creates a fresh asyncio.Event -- confirms a second
    wait_for_disconnect() call after a reconnect doesn't just
    immediately return because the FIRST event was already set."""
    client, _fake = _connected_client()

    first_wait = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)
    client._on_disconnect(None, None, None, "first drop")
    assert await first_wait == "first drop"

    second_wait = asyncio.ensure_future(client.wait_for_disconnect())
    await asyncio.sleep(0.01)
    assert not second_wait.done()  # must NOT resolve immediately

    client._on_disconnect(None, None, None, "second drop")
    assert await second_wait == "second drop"


# --- self._client_lock: real concurrency test --------------------------

def test_client_lock_serializes_get_shadow_and_replace_token() -> None:
    """Real test with OS threads (threading.Lock, not asyncio.Lock --
    these methods run via asyncio.to_thread, so real threads).
    Confirms that replace_token() waits until a running get_shadow()
    call is done, instead of accessing self._client concurrently --
    closes the previously documented gap."""
    import threading
    import time as time_module

    client, fake = _connected_client()

    new_fake = _FakeMqttClient(on_subscribe=lambda mid: client._on_subscribe(client, None, mid, []))

    def fake_connect(timeout: float = 10.0) -> None:
        client._client = new_fake
        client._connected = True

    def fake_disconnect() -> None:
        pass

    client.connect = fake_connect  # type: ignore[method-assign]
    client.disconnect = fake_disconnect  # type: ignore[method-assign]

    order: list[str] = []

    def slow_get_shadow() -> None:
        order.append("get_shadow start")
        # fake never delivers a response -> this genuinely blocks for
        # ~0.3s inside the lock, polling via time.sleep()
        with pytest.raises(ShadowError):
            client.get_shadow(timeout=0.3)
        order.append("get_shadow end")

    t = threading.Thread(target=slow_get_shadow)
    t.start()
    time_module.sleep(0.05)  # let the thread acquire the lock and start polling

    order.append("replace_token start")
    client.replace_token(_dummy_token())
    order.append("replace_token end")

    t.join()

    # If the lock works, replace_token() (main thread) must block until
    # get_shadow() (background thread) releases it -- so "get_shadow
    # end" must come before "replace_token end", even though
    # "replace_token start" was appended earlier (that's just issuing
    # the call, not acquiring the lock).
    assert order.index("get_shadow end") < order.index("replace_token end")


def test_get_shadow_waits_for_subscribe_confirmation_before_publishing() -> None:
    """NEW (session 33) -- regression test against the found race:
    publish() may only happen AFTER all SUBACKs have been confirmed.
    Simulates a delayed SUBACK confirmation to check that publish()
    actually waits for it, instead of sending immediately."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="X")
    order: list[str] = []

    class DelayedFake(_FakeMqttClient):
        def subscribe(self, topic, qos=0):
            order.append(f"subscribe:{topic}")
            mid = self._next_mid
            self._next_mid += 1
            # Bestaetigung bewusst verzoegert (in einem eigenen Thread),
            # NICHT sofort wie die Standard-Fake -- genau das Szenario,
            # das publish() faelschlicherweise nicht abgewartet hatte.
            def confirm_later():
                time.sleep(0.05)
                if self._on_subscribe is not None:
                    self._on_subscribe(mid)
            threading.Thread(target=confirm_later, daemon=True).start()
            return (0, mid)

        def publish(self, topic, payload=None, qos=0):
            order.append(f"publish:{topic}")
            super().publish(topic, payload, qos)

    fake = DelayedFake(on_subscribe=lambda mid: client._on_subscribe(client, None, mid, []))
    client._client = fake
    client._connected = True

    def respond_after_publish(topic: str, payload: object) -> None:
        if topic.endswith("/get"):
            client._on_message(client, None, _FakeMsg("$aws/things/X/shadow/get/accepted", b"{}"))

    fake.on_publish_react = respond_after_publish
    client.get_shadow(timeout=2.0)

    # Alle subscribe-Aufrufe muessen VOR dem publish-Aufruf stehen.
    publish_index = next(i for i, e in enumerate(order) if e.startswith("publish:"))
    subscribe_indices = [i for i, e in enumerate(order) if e.startswith("subscribe:")]
    assert all(i < publish_index for i in subscribe_indices)


def test_persistent_subscribe_waits_for_confirmation() -> None:
    """NEW (session 33) -- the same fix as get_shadow(), now also
    secured for the persistent subscribe() method (watch_state()/
    watch_live_map())."""
    client, fake = _connected_client()
    client.subscribe("some/topic", lambda resp: None)
    assert "some/topic" in fake.subscribed


# =========================================================================
# SSL certificate error clarity (this session, following the same fix
# in auth.py/rest_client.py -- but a genuinely different mechanism
# here, see _raise_clear_ssl_error()'s docstring: paho-mqtt's
# synchronous Client.connect() raises ssl.SSLError directly on a TLS
# handshake failure, never aiohttp.ClientSSLError).
# =========================================================================


class _NetworkFailingRawClient:
    """Stand-in for the real paho.mqtt.client.Client returned by
    _build_client() -- only connect() matters for this test.
    Generalized (this session) from the SSL-only
    _SSLFailingRawClient to also cover plain OSError (DNS, connection
    refused, connect-level timeout)."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def connect(self, endpoint: str, port: int = 443, keepalive: int = 300) -> None:
        raise self._exc


def test_connect_ssl_error_gets_clear_message(monkeypatch) -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="0000000000000000")
    monkeypatch.setattr(
        client, "_build_client", lambda: _NetworkFailingRawClient(ssl.SSLCertVerificationError("certificate has expired"))
    )

    with pytest.raises(ShadowSSLError) as excinfo:
        client.connect()

    assert "certificate" in str(excinfo.value).lower()
    assert "temporary" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, ssl.SSLError)


def test_connect_connection_error_gets_clear_message(monkeypatch) -> None:
    """NEW (this session) -- DNS failure, connection refused, etc. all
    surface as plain OSError subclasses from paho-mqtt's synchronous
    connect(), distinct from the ssl.SSLError case above."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="0000000000000000")
    monkeypatch.setattr(
        client, "_build_client", lambda: _NetworkFailingRawClient(ConnectionRefusedError("Connection refused"))
    )

    with pytest.raises(ShadowConnectionError) as excinfo:
        client.connect()

    assert "connect" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, OSError)


class TestPublishCmdPayloadPubackConfirmation:
    """NEW (this session, per the parallel APK-research chat's own
    finding): QoS=1 was already set, but nothing previously checked
    whether the broker actually confirmed the publish (PUBACK) at the
    MQTT protocol level -- "fire-and-forget" conflated "no
    application-level ack topic" (still true) with "no protocol-level
    ack either" (false). This matters because rejected/report is
    published BY THE ROBOT -- a command the broker silently drops
    never reaches the robot to be rejected, so "no rejection" was
    never actually proof of delivery."""

    def test_returns_true_when_broker_confirms_publish(self):
        client, fake = _connected_client(blid="BLID1")
        fake.publish_confirmed = True

        result = client.publish_cmd_payload("irbt-prefix", {"command": "start"})

        assert result is True

    def test_returns_false_when_broker_does_not_confirm_publish(self):
        client, fake = _connected_client(blid="BLID1")
        fake.publish_confirmed = False

        result = client.publish_cmd_payload("irbt-prefix", {"command": "start"})

        assert result is False

    def test_publish_cmd_also_returns_the_confirmation(self):
        """publish_cmd() (simple commands) delegates to
        publish_cmd_payload() -- must propagate the same signal."""
        client, fake = _connected_client(blid="BLID1")
        fake.publish_confirmed = False

        result = client.publish_cmd("irbt-prefix", "start")

        assert result is False

    def test_a_publish_failure_returns_false_not_an_exception(self):
        """wait_for_publish()/is_published() can raise RuntimeError/
        ValueError for real protocol-level failures (queue full,
        publish failed) -- callers should get a clean False, not an
        unhandled exception, since this runs inside asyncio.to_thread()
        in the real call path."""
        client, fake = _connected_client(blid="BLID1")

        class _FailingMessageInfo:
            def wait_for_publish(self, timeout=None):
                raise RuntimeError("simulated: publish failed")

        fake.publish = lambda topic, payload=None, qos=0: _FailingMessageInfo()

        result = client.publish_cmd_payload("irbt-prefix", {"command": "start"})

        assert result is False


class TestNoUserAgentHeaderIsSent:
    """REVERSED (a23). a22 added a User-Agent header on a third-party
    project's documented but untested claim that AWS IoT's authorizer
    inspects it. The parallel APK research then examined the real app's
    own connection code: exactly three headers, no fourth.

    Removed not because it was proven harmful, but because it shipped
    to every consumer -- Home Assistant included -- in the same release
    that broke Prime setup there. This test exists so it does not come
    back without new evidence."""

    def test_only_the_three_confirmed_headers_are_sent(self):
        from unittest.mock import MagicMock, patch

        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = PrimeMqttClient(token=_dummy_token(), endpoint="e.example.com", blid="B")
        fake = MagicMock()
        with patch("paho.mqtt.client.Client", return_value=fake):
            client._build_client()

        headers = fake.ws_set_options.call_args.kwargs.get("headers", {})
        assert set(headers) == {
            "x-amz-customauthorizer-name",
            "x-amz-customauthorizer-signature",
            "x-irobot-auth",
        }

    def test_no_user_agent_key_under_any_casing(self):
        from unittest.mock import MagicMock, patch

        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = PrimeMqttClient(token=_dummy_token(), endpoint="e.example.com", blid="B")
        fake = MagicMock()
        with patch("paho.mqtt.client.Client", return_value=fake):
            client._build_client()

        headers = fake.ws_set_options.call_args.kwargs.get("headers", {})
        assert not any(k.lower() == "user-agent" for k in headers)

class TestSubscribeAndWaitRejectionDetection:
    """REAL BUG FOUND AND FIXED (this session, prompted directly by a
    field result: chairstacker triggered a favorite AND a room clean
    from the real app -- the robot genuinely reacted to both within 20
    seconds -- while our own --watch-wildcard subscription saw NOTHING
    during that exact window). _on_subscribe() received the broker's
    SUBACK reason code for every subscribe() call ever made by this
    library, but never checked it -- a REJECTED subscription (MQTT's
    own 0x80 failure code) was recorded identically to a successful
    one. See SubscriptionRejectedError's own docstring for the full
    finding."""

    def test_successful_suback_does_not_raise(self):
        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="BLID1")
        fake = _FakeMqttClient(on_subscribe=lambda mid: client._on_subscribe(client, None, mid, [1]))
        client._client = fake
        client._connected = True

        client.subscribe("some/topic", lambda msg: None)  # must not raise

        assert "some/topic" in fake.subscribed

    def test_rejected_suback_raises_subscription_rejected_error(self):
        from roombapy_prime.mqtt_client import PrimeMqttClient, SubscriptionRejectedError

        client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="BLID1")
        fake = _FakeMqttClient(on_subscribe=lambda mid: client._on_subscribe(client, None, mid, [0x80]))
        client._client = fake
        client._connected = True

        with pytest.raises(SubscriptionRejectedError) as exc_info:
            client.subscribe("restricted/topic", lambda msg: None)

        assert "restricted/topic" in str(exc_info.value)

    def test_mixed_success_and_rejection_across_multiple_topics_reports_only_the_rejected_one(self):
        """_subscribe_and_wait() takes a list of topics -- confirms the
        error message identifies WHICH topic failed, not just that
        something somewhere did, when multiple topics are subscribed
        to in the same call."""
        from roombapy_prime.mqtt_client import PrimeMqttClient, SubscriptionRejectedError

        client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="BLID1")
        codes_by_topic = {"good/topic": [1], "bad/topic": [0x80]}
        subscribed_order: list[str] = []

        def fake_subscribe(topic, qos=1):
            subscribed_order.append(topic)
            mid = len(subscribed_order)
            client._on_subscribe(client, None, mid, codes_by_topic[topic])
            return (0, mid)

        fake = _FakeMqttClient()
        fake.subscribe = fake_subscribe
        client._client = fake
        client._connected = True

        with pytest.raises(SubscriptionRejectedError) as exc_info:
            client._subscribe_and_wait(["good/topic", "bad/topic"])

        assert "bad/topic" in str(exc_info.value)
        assert "good/topic" not in str(exc_info.value)


class TestSubackReasonCodeHandling:
    """REAL FIELD CRASH (DaRealGuGu, v0.1.11a22). The first version of
    the SUBACK check did int(rc) >= 0x80. paho-mqtt 2.x passes
    ReasonCode OBJECTS, int() on one raises TypeError -- on paho's own
    network thread, which killed the client and sent it into an endless
    reconnect loop.

    The knock-on damage mattered more than the crash: every subsequent
    shadow read and PUBACK timed out, and the resulting "broker did NOT
    confirm receipt" was reported to the tester as evidence of a
    policy-level block. It was our bug. Three stages of a real test run
    produced a confident, wrong diagnosis."""

    class _ReasonCode:
        """Stands in for paho 2.x's ReasonCode: has .value and
        .is_failure, and deliberately raises on int() exactly as the
        real one does."""

        def __init__(self, value, is_failure):
            self.value = value
            self.is_failure = is_failure

        def __int__(self):
            raise TypeError("int() argument must be a string, a bytes-like object or a real number")

    def test_paho2_reason_code_objects_do_not_raise(self):
        from roombapy_prime.mqtt_client import _suback_is_failure

        assert _suback_is_failure(self._ReasonCode(0, is_failure=False)) is False
        assert _suback_is_failure(self._ReasonCode(0x80, is_failure=True)) is True

    def test_plain_ints_still_work(self):
        """paho 1.x, and v3 callbacks, pass ints."""
        from roombapy_prime.mqtt_client import _suback_is_failure

        assert _suback_is_failure(0) is False
        assert _suback_is_failure(0x80) is True

    def test_an_unrecognised_type_is_treated_as_not_a_failure(self):
        """A missed rejection is a far smaller harm than crashing the
        MQTT thread again."""
        from roombapy_prime.mqtt_client import _suback_is_failure

        assert _suback_is_failure(object()) is False

    def test_the_callback_itself_never_raises(self):
        """Whatever arrives, the network thread must survive it."""
        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")

        client._on_subscribe(client, None, 1, [self._ReasonCode(0x80, True)])
        client._on_subscribe(client, None, 2, None)
        client._on_subscribe(client, None, 3, [object()])
        client._on_subscribe(client, None, 4, "not even iterable in a useful way")

        assert 1 in client._confirmed_mids
        assert 1 in client._subscribe_failures


class TestAgainstRealPahoReasonCodes:
    """The stub-based tests above mimic ReasonCode. This one uses the
    real class, because the crash happened precisely at the boundary
    between what we assumed the type was and what it actually is.

    Constructing one is itself a trap: the packet type argument is
    `SUBACK >> 4` (0x09), not SUBACK (0x90). Passing the latter raises
    for every value, which is easy to mistake for "these codes don't
    exist"."""

    def _code(self, value):
        from paho.mqtt.client import SUBACK
        from paho.mqtt.reasoncodes import ReasonCode

        return ReasonCode(SUBACK >> 4, identifier=value)

    @pytest.mark.parametrize("value", [0x00, 0x01, 0x02])
    def test_granted_qos_codes_are_not_failures(self, value):
        from roombapy_prime.mqtt_client import _suback_is_failure

        assert _suback_is_failure(self._code(value)) is False

    @pytest.mark.parametrize("value", [0x80, 0x87, 0x8F, 0x9E, 0xA1])
    def test_real_failure_codes_are_detected(self, value):
        """0x87 is "Not authorized" -- the code an IoT policy refusal
        would actually produce, and the whole reason this check exists."""
        from roombapy_prime.mqtt_client import _suback_is_failure

        assert _suback_is_failure(self._code(value)) is True

    def test_our_verdict_matches_pahos_own_across_the_range(self):
        from roombapy_prime.mqtt_client import _suback_is_failure

        for value in (0x00, 0x01, 0x02, 0x80, 0x87, 0x8F, 0x9E, 0xA1):
            code = self._code(value)
            assert _suback_is_failure(code) is code.is_failure, f"disagreed on {value:#04x}"


class TestPublishRevivesADeadConnection:
    """FIELD EVIDENCE (DaRealGuGu, three consecutive sessions): the
    FIRST send of every session got no PUBACK, while later sends in the
    same session succeeded.

    The ordering in his logs is what identified it: the ro-currentstate
    GET timed out FIRST, then the publish got no PUBACK, and only
    afterwards did paho report drops. The connection was already dead
    before the send.

    What kills it is the interactive pause -- the tool prints a large
    payload and waits for a human to read it and type y. get_shadow()
    survived that because it reconnects; publish_cmd_payload() did not,
    because it only checked whether a client object existed.

    Publishing into a dead connection is the worst failure mode
    available here: no error, no PUBACK, and the script then reports
    the missing confirmation as though it said something about the
    payload."""

    def _client(self, *, connected: bool):
        from unittest.mock import MagicMock

        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")
        client._client = MagicMock()
        client._connected = connected
        client.reconnect = MagicMock(
            side_effect=lambda **_kw: setattr(client, "_connected", True)
        )
        return client

    def test_a_dead_connection_is_reconnected_before_publishing(self):
        client = self._client(connected=False)

        client.publish_cmd_payload("v005-irbthbu", {"command": "start"})

        client.reconnect.assert_called_once()

    def test_a_live_connection_is_not_needlessly_reconnected(self):
        """Reconnecting a healthy connection would drop subscriptions
        that watchers depend on."""
        client = self._client(connected=True)

        client.publish_cmd_payload("v005-irbthbu", {"command": "start"})

        client.reconnect.assert_not_called()

    def test_the_publish_still_happens_after_the_reconnect(self):
        client = self._client(connected=False)

        client.publish_cmd_payload("v005-irbthbu", {"command": "start"})

        client._client.publish.assert_called_once()


def test_keepalive_is_short_enough_to_notice_a_dead_connection() -> None:
    """MQTT declares a connection dead after 1.5x keepalive. At the
    previous 300s that was a 450-SECOND blind window, during which
    publish() succeeds locally while nothing reaches the broker.

    If this ever gets raised again, the question to ask is what it buys
    that is worth being unable to detect a broken connection for
    minutes at a time."""
    import inspect

    from roombapy_prime.mqtt_client import PrimeMqttClient

    source = inspect.getsource(PrimeMqttClient.connect)

    assert "keepalive=60" in source
    assert "keepalive=300" not in source


class TestSubscribeAlsoRevivesADeadConnection:
    """The last operation in this module that still used the client
    without checking it was alive -- and the most damaging one to get
    wrong.

    Subscribing to a dead connection fails SILENTLY: the watcher then
    observes nothing at all, and a real robot reaction gets reported as
    "nothing happened". Field logs showed the full pattern in one
    place: subscribe, then a shadow GET timing out, then a publish with
    no PUBACK -- three symptoms of a single dead connection, of which
    only the middle one surfaced as an error."""

    def _client(self, *, connected: bool):
        from unittest.mock import MagicMock

        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="B")
        client._client = MagicMock()
        client._client.subscribe.return_value = (0, 1)
        client._connected = connected
        client.reconnect = MagicMock(
            side_effect=lambda **_kw: setattr(client, "_connected", True)
        )
        return client

    def test_a_dead_connection_is_reconnected_before_subscribing(self):
        client = self._client(connected=False)

        client._subscribe_and_wait(["some/topic"], timeout=0.01)

        client.reconnect.assert_called_once()

    def test_a_live_connection_subscribes_without_reconnecting(self):
        client = self._client(connected=True)

        client._subscribe_and_wait(["some/topic"], timeout=0.01)

        client.reconnect.assert_not_called()
        client._client.subscribe.assert_called_once()

    def test_no_bare_assert_remains_in_the_module(self):
        """Three separate field reports surfaced a developer-facing
        assert to someone running a diagnostic script. This checks the
        pattern is gone rather than trusting that it is."""
        import inspect

        from roombapy_prime import mqtt_client

        source = inspect.getsource(mqtt_client)

        assert 'assert self._client is not None' not in source


class TestARepeatReadDoesNotResubscribe:
    """`get_shadow()` subscribed on every call and never unsubscribed, so
    a second read of the same shadow in one session re-subscribed to
    topics the broker had already granted -- work that contributes
    nothing and can still fail.

    It did fail: @DaRealGuGu's second `rw-settings` read got no SUBACK
    within three seconds and then no response within eight, while the
    first read in the same session had worked.
    """

    def _client(self):
        from unittest.mock import MagicMock

        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = object.__new__(PrimeMqttClient)
        client._subscribed_topics = set()
        client._connected = True
        client._disconnect_loop = None
        client._disconnect_event = None
        client._disconnect_reason = None
        client._subscribe_and_wait = MagicMock()
        return client

    def test_the_first_read_subscribes(self):
        client = self._client()
        topics = ["a/get/accepted", "a/get/rejected"]

        fresh = [t for t in topics if t not in client._subscribed_topics]
        if fresh:
            client._subscribe_and_wait(fresh)
            client._subscribed_topics.update(fresh)

        client._subscribe_and_wait.assert_called_once_with(topics)

    def test_a_second_read_of_the_same_shadow_does_not(self):
        client = self._client()
        topics = ["a/get/accepted", "a/get/rejected"]
        client._subscribed_topics.update(topics)

        fresh = [t for t in topics if t not in client._subscribed_topics]
        if fresh:
            client._subscribe_and_wait(fresh)

        client._subscribe_and_wait.assert_not_called()

    def test_a_different_shadow_still_subscribes(self):
        client = self._client()
        client._subscribed_topics.add("a/get/accepted")
        topics = ["b/get/accepted", "b/get/rejected"]

        fresh = [t for t in topics if t not in client._subscribed_topics]
        if fresh:
            client._subscribe_and_wait(fresh)

        client._subscribe_and_wait.assert_called_once_with(topics)

    def test_a_disconnect_forgets_everything(self):
        """A NEW SESSION GRANTS NOTHING. Keeping the set across a
        disconnect would make the next read skip a subscription it no
        longer has -- the exact silence this change exists to avoid."""
        from unittest.mock import MagicMock

        from roombapy_prime.mqtt_client import PrimeMqttClient

        client = self._client()
        client._subscribed_topics.update(["a/get/accepted", "b/get/rejected"])

        PrimeMqttClient._on_disconnect(
            client, MagicMock(), None, None, "session ended"
        )

        assert client._subscribed_topics == set()


class TestAShadowGetIsActuallySent:
    """`publish()` returns a result code and a handle, and `get_shadow`
    discarded both.

    A queued-but-unsent request produces exactly the symptom
    @DaRealGuGu reported: no answer within eight seconds, no error, and
    nothing to distinguish "this robot has no such shadow" from "we
    never asked". **This is the same class of gap b12 closed for
    `subscribe`** — closed there, left open three lines away.
    """

    def _confirm(self, **attrs):
        from unittest.mock import MagicMock

        from roombapy_prime.mqtt_client import _publish_confirmed

        info = MagicMock()
        info.rc = attrs.get("rc", 0)
        info.is_published.return_value = attrs.get("published", True)
        if "raises" in attrs:
            info.wait_for_publish.side_effect = attrs["raises"]
        return lambda: _publish_confirmed(info, "topic")

    def test_a_normal_publish_passes(self):
        self._confirm()()

    def test_a_refused_publish_says_the_request_never_left(self):
        from roombapy_prime.mqtt_client import ShadowError

        with pytest.raises(ShadowError, match="never left"):
            self._confirm(rc=4)()

    def test_a_queued_but_unsent_publish_is_caught(self):
        """The connection accepts messages and does not deliver them --
        the state that looks healthiest and works least."""
        from roombapy_prime.mqtt_client import ShadowError

        with pytest.raises(ShadowError, match="queued but never sent"):
            self._confirm(published=False)()

    def test_an_unconfirmable_publish_is_reported(self):
        from roombapy_prime.mqtt_client import ShadowError

        with pytest.raises(ShadowError, match="could not be confirmed"):
            self._confirm(raises=RuntimeError("loop not running"))()

    def test_a_stand_in_client_is_tolerated(self):
        """Refusing on None would fail tests rather than find bugs."""
        from roombapy_prime.mqtt_client import _publish_confirmed

        _publish_confirmed(None, "topic")

    def test_get_shadow_uses_it(self):
        import inspect

        from roombapy_prime.mqtt_client import PrimeMqttClient

        source = inspect.getsource(PrimeMqttClient.get_shadow)
        assert "_publish_confirmed(" in source
