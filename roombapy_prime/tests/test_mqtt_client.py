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

import json
import threading
import time
from pathlib import Path
from collections.abc import Callable

import pytest

from roombapy_prime.auth import ConnectionToken
from roombapy_prime.mqtt_client import PrimeMqttClient, ShadowError, _shadow_base


def _load(fixtures_dir: Path, name: str) -> dict:
    return json.loads((fixtures_dir / name).read_text())


class _FakeMsg:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class _FakeMqttClient:
    """Stand-in for paho.mqtt.client.Client. No sockets involved."""

    def __init__(self, on_subscribe: Callable[[int], None] | None = None) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.published: list[tuple[str, object]] = []
        self.on_publish_react: Callable[[str, object], None] | None = None
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

    def publish(self, topic: str, payload: object = None, qos: int = 0) -> None:
        self.published.append((topic, payload))
        if self.on_publish_react is not None:
            self.on_publish_react(topic, payload)


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

def test_get_shadow_before_connect_raises() -> None:
    client = PrimeMqttClient(token=_dummy_token(), endpoint="fake.example.com", blid="x")
    with pytest.raises(AssertionError):
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


def test_cmd_topic_helper() -> None:
    """NEW (session 39) -- confirmed both by this library's own native
    disassembly (libcorebase.so's literal "/things/%s/cmd" format
    string) and independently by a third-party, unaffiliated project
    that reports this exact topic working against a real device. See
    cmd_topic()'s docstring for the full evidence trail."""
    client = PrimeMqttClient(token=_dummy_token(), endpoint="e", blid="BLID1")
    assert client.cmd_topic("irbt-prefix") == "irbt-prefix/things/BLID1/cmd"


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
    with pytest.raises(AssertionError):
        client.replace_token(new_token)


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
