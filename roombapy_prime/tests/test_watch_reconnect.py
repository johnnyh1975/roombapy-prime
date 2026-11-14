"""The watchers and the MQTT client together, across drops, reconnects,
token swaps and closes.

WHY THIS FILE EXISTS. test_prime_robot.py drives the watchers against a
mocked client whose wait_for_disconnect() returns when the test says so.
That tests the watcher's branches, and none of the interplay: when a
drop is noticed, who rebuilds, what the other watchers see, what a
close does. The review of 0.5.0b1 found every one of its watcher bugs
there, and the whole suite passed with all of them present.

So here the real PrimeMqttClient and the real PrimeRobot run over a fake
transport: `_Broker` builds one `_Conn` per connect, and a test drops the
live one, delays or fails connects, withholds SUBACKs, and counts what
was built. No network, no real sleeps beyond a few milliseconds.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiomqtt import MqttError

from roombapy_prime.auth import ConnectionToken
from roombapy_prime.mqtt_client import PrimeMqttClient, ShadowError, _DeploymentCallback
from roombapy_prime.prime_robot import PrimeRobot, _first_of

NEVER = object()
BLID = "BLID"


class _Broker:
    """Builds one _Conn per connect; the last one is the live one."""

    def __init__(self) -> None:
        self.conns: list[_Conn] = []
        self.connect_fail = 0
        self.connect_delay = 0.0
        self.suback: Callable[[str], Any] = lambda _topic: [1]
        self.on_publish: Callable[[_Conn, str, Any], None] | None = None
        #: AWS IoT's rule: a second connection with the same client id
        #: drops the older one. Every connection here has the same id.
        self.evict_same_id = False

    @property
    def live(self) -> _Conn:
        return self.conns[-1]

    def build(self, timeout: float) -> _Conn:
        conn = _Conn(self)
        self.conns.append(conn)
        return conn

    def open_connections(self) -> list[_Conn]:
        return [c for c in self.conns if c.entered and not c.exited and not c.dropped]


class _Conn:
    def __init__(self, broker: _Broker) -> None:
        self.broker = broker
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.subscribed: list[str] = []
        self.published: list[tuple[str, Any]] = []
        self.unsubscribed: list[str] = []
        self.entered = False
        self.entered_at: float | None = None
        self.exited = False
        self.dropped = False

    async def __aenter__(self) -> _Conn:
        if self.broker.connect_delay:
            await asyncio.sleep(self.broker.connect_delay)
        if self.broker.connect_fail > 0:
            self.broker.connect_fail -= 1
            raise MqttError("fake connect failure")
        self.entered = True
        self.entered_at = time.monotonic()
        if self.broker.evict_same_id:
            for other in self.broker.open_connections():
                if other is not self:
                    other.drop("Normal disconnection")
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.exited = True
        await self.queue.put(MqttError("Disconnected during message iteration"))

    def drop(self, cause: str = "broker gone") -> None:
        self.dropped = True
        error = MqttError("Disconnected during message iteration")
        error.__cause__ = RuntimeError(cause)
        self.queue.put_nowait(error)

    def deliver(self, topic: str, payload: bytes) -> None:
        message = MagicMock()
        message.topic = topic
        message.payload = payload
        self.queue.put_nowait(message)

    async def subscribe(self, topic: str, qos: int = 0, timeout: float | None = None) -> Any:
        self.subscribed.append(topic)
        answer = self.broker.suback(topic)
        if answer is NEVER:
            await asyncio.Event().wait()
        return answer

    async def unsubscribe(self, topic: str, timeout: float | None = None) -> None:
        self.unsubscribed.append(topic)

    async def publish(
        self, topic: str, payload: Any = None, qos: int = 0, timeout: float | None = None
    ) -> None:
        self.published.append((topic, payload))
        if self.broker.on_publish is not None:
            self.broker.on_publish(self, topic, payload)

    @property
    def messages(self) -> Any:
        conn = self

        class _Messages:
            def __aiter__(self) -> _Messages:
                return self

            async def __anext__(self) -> Any:
                item = await conn.queue.get()
                if isinstance(item, BaseException):
                    raise item
                return item

        return _Messages()


def _token(expires: int | None = None) -> ConnectionToken:
    return ConnectionToken(
        client_id="cid", iot_token="t", iot_signature="s",
        iot_authorizer_name="a", expires=expires, devices=[],
    )


async def _setup(
    relogin: Any = None, expires: int | None = None, blid: str = BLID
) -> tuple[PrimeRobot, PrimeMqttClient, _Broker]:
    broker = _Broker()
    client = PrimeMqttClient(token=_token(expires), endpoint="e", blid=blid)
    client._build_client = broker.build  # type: ignore[method-assign,assignment]
    client.SUBACK_TIMEOUT_SECONDS = 0.05
    robot = PrimeRobot(
        blid=blid, mqtt_client=client, rest_client=AsyncMock(),
        relogin=relogin, irbt_topic_prefix="irbt",
    )
    robot._SUBACK_RECHECK_SECONDS = 0.01
    robot._FIRST_RECONNECT_BACKOFF = 0.01
    await robot.connect()
    return robot, client, broker


async def _until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


class _Watch:
    """A consumer of one watch_*() generator, collecting payloads."""

    def __init__(self, agen: Any, hold: asyncio.Event | None = None) -> None:
        self.got: list[Any] = []
        self._agen = agen
        self._hold = hold
        self.task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        async for item in self._agen:
            self.got.append(getattr(item, "payload", item))
            if self._hold is not None:
                await self._hold.wait()

    async def stop(self) -> None:
        self.task.cancel()
        await asyncio.wait({self.task})
        await self._agen.aclose()


def _state_topic(client: PrimeMqttClient) -> str:
    return client.shadow_topic("update/delta")


def _timeline_topic(client: PrimeMqttClient) -> str:
    return client.mission_timeline_topic("irbt")


async def _subscribed(broker: _Broker, *topics: str) -> None:
    await _until(lambda: all(t in broker.live.subscribed for t in topics))


# --- one drop, several watchers ------------------------------------------


@pytest.mark.asyncio
async def test_one_drop_is_one_reconnect_one_warning_and_everyone_resumes(caplog) -> None:
    """REAL FIELD BUG (DaRealGuGu), and its 0.5.0b1 sequel. Every watcher
    had its own reconnect loop over ONE shared client: a reconnect by one
    tore down the connection, which the other saw as a drop and rebuilt.
    Since every watcher hears of a drop, each one also logged and counted
    it -- one blip, three WARNING lines and a false "3 disconnections in
    five minutes" (review finding)."""
    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    robot, client, broker = await _setup()
    state = _Watch(robot.watch_state())
    timeline = _Watch(robot.watch_mission_timeline())
    named = _Watch(robot.watch_named_shadows_updates())
    await _subscribed(broker, _state_topic(client), _timeline_topic(client))

    broker.live.drop("blip")
    await _until(lambda: len(broker.conns) == 2 and client.connected)
    await _until(lambda: "watch resumed" in caplog.text)
    await asyncio.sleep(0.05)
    broker.live.deliver(_state_topic(client), b'{"a": 1}')
    broker.live.deliver(_timeline_topic(client), b'{"b": 2}')
    await _until(lambda: state.got == [{"a": 1}] and timeline.got == [{"b": 2}])

    assert len(broker.conns) == 2
    assert caplog.text.count("MQTT connection dropped") == 1
    assert caplog.text.count("watch resumed") == 1
    assert "disconnections in five minutes" not in caplog.text
    for watch in (state, timeline, named):
        await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_three_real_drops_in_five_minutes_are_named(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    robot, client, broker = await _setup()
    watches = [_Watch(robot.watch_state()), _Watch(robot.watch_mission_timeline())]
    await _subscribed(broker, _state_topic(client), _timeline_topic(client))

    for n in range(3):
        broker.live.drop(f"drop {n}")
        await _until(lambda n=n: len(broker.conns) == n + 2 and client.connected)
        await _until(lambda n=n: caplog.text.count("watch resumed") == n + 1)

    assert caplog.text.count("MQTT connection dropped") == 3
    assert caplog.text.count("disconnections in five minutes") == 1
    for watch in watches:
        await watch.stop()
    await robot.disconnect()


# --- drops a watcher was not waiting for ---------------------------------


@pytest.mark.asyncio
async def test_a_drop_while_the_consumer_holds_a_message_is_not_lost() -> None:
    """The generator is suspended at `yield` while its consumer works. A
    drop then woke nobody, and the watcher went back to waiting on a dead
    connection -- for good (review finding; 0.4.x too)."""
    robot, client, broker = await _setup()
    hold = asyncio.Event()
    watch = _Watch(robot.watch_state(), hold=hold)
    await _subscribed(broker, _state_topic(client))

    broker.live.deliver(_state_topic(client), b'{"n": 1}')
    await _until(lambda: watch.got == [{"n": 1}])
    broker.live.drop("while busy")
    await _until(lambda: not client.connected)
    hold.set()

    await _until(lambda: len(broker.conns) == 2 and client.connected)
    broker.live.deliver(_state_topic(client), b'{"n": 2}')
    await _until(lambda: watch.got == [{"n": 1}, {"n": 2}])
    await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_a_drop_right_after_a_reconnect_is_not_lost(caplog) -> None:
    """The eviction pattern: the new connection drops again within the
    second the watcher spends checking its SUBACKs. Nobody was waiting,
    the log said "watch resumed", and nothing was delivered again
    (review finding; 0.4.x too)."""
    caplog.set_level(logging.INFO, logger="roombapy_prime")
    robot, client, broker = await _setup()
    robot._SUBACK_RECHECK_SECONDS = 0.2
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))

    broker.live.drop("evicted #1")
    await _until(lambda: len(broker.conns) == 2 and client.connected)
    broker.live.drop("evicted #2")  # inside the re-check

    await _until(lambda: len(broker.conns) == 3 and client.connected, timeout=3.0)
    await asyncio.sleep(0.3)
    broker.live.deliver(_state_topic(client), b'{"n": 3}')
    await _until(lambda: watch.got == [{"n": 3}])
    await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_a_message_and_a_drop_together_lose_neither() -> None:
    """Both arrive before the watcher looks: it yields the message, and
    the drop used to be thrown away with the losing task (review
    finding)."""
    robot, client, broker = await _setup()
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))
    await asyncio.sleep(0.01)

    broker.live.deliver(_state_topic(client), b'{"n": 1}')
    broker.live.drop("right behind it")

    await _until(lambda: len(broker.conns) == 2 and client.connected)
    await asyncio.sleep(0.05)
    broker.live.deliver(_state_topic(client), b'{"n": 2}')
    await _until(lambda: watch.got == [{"n": 1}, {"n": 2}])
    await watch.stop()
    await robot.disconnect()


# --- SUBACKs that never come ---------------------------------------------


@pytest.mark.asyncio
async def test_unacknowledged_subscriptions_are_retried_a_bounded_number_of_times(caplog) -> None:
    """@utkjmitch's sessions show no SUBACK at all. The retry that 0.5.0b1
    brought to life (0.4.x never ran it) reconnected forever, each time
    tearing down a connection that was delivering, and the watcher
    delivered nothing while it looped (review finding)."""
    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    robot, client, broker = await _setup()
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))
    broker.suback = lambda _topic: NEVER

    broker.live.drop("blip")
    await _until(lambda: "resuming anyway" in caplog.text, timeout=3.0)
    await asyncio.sleep(0.1)

    # One reconnect for the drop, then at most _MAX_UNCONFIRMED_RETRIES.
    assert len(broker.conns) == 2 + robot._MAX_UNCONFIRMED_RETRIES
    broker.live.deliver(_state_topic(client), b'{"n": 1}')
    await _until(lambda: watch.got == [{"n": 1}])
    await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_a_late_suback_ends_the_retries() -> None:
    robot, client, broker = await _setup()
    robot._SUBACK_RECHECK_SECONDS = 0.05
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))
    release = asyncio.Event()

    async def late(_topic: str) -> list[int]:
        await release.wait()
        return [1]

    broker.suback = lambda _topic: NEVER if not release.is_set() else [1]
    original = broker.build

    def build_with_late_suback(timeout: float) -> _Conn:
        conn = original(timeout)

        async def subscribe(topic: str, qos: int = 0, timeout: float | None = None) -> Any:
            conn.subscribed.append(topic)
            return await late(topic)

        conn.subscribe = subscribe  # type: ignore[method-assign]
        return conn

    broker.build = build_with_late_suback  # type: ignore[method-assign]
    client._build_client = broker.build  # type: ignore[method-assign,assignment]
    broker.live.drop("blip")
    await _until(lambda: len(broker.conns) == 2 and client.connected)
    release.set()
    await asyncio.sleep(0.2)

    assert len(broker.conns) == 2
    await watch.stop()
    await robot.disconnect()


# --- who logs in ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_due_token_is_renewed_by_one_login_not_one_per_watcher() -> None:
    """With a token due, every watcher logged in and swapped it, each
    swap tearing down the connection the last one built -- four watchers,
    four logins (review finding). A lockout risk."""
    logins: list[int] = []

    async def relogin() -> Any:
        logins.append(1)
        await asyncio.sleep(0.02)  # a login takes a while; the others notice meanwhile
        result = MagicMock()
        result.token_for_blid.return_value = _token(expires=int(time.time()) + 3600)
        return result

    robot, client, broker = await _setup(relogin=relogin, expires=int(time.time()) + 60)
    if robot._refresh_task is not None:  # this test is about the watchers
        robot._refresh_task.cancel()
        await asyncio.wait({robot._refresh_task})
        robot._refresh_task = None
    watches = [
        _Watch(robot.watch_state()),
        _Watch(robot.watch_mission_timeline()),
        _Watch(robot.watch_named_shadows_updates()),
        _Watch(robot.watch_rejected_commands()),
    ]
    await _subscribed(broker, _state_topic(client), _timeline_topic(client))

    broker.live.drop("blip")
    await _until(lambda: client.connected and len(broker.conns) >= 2)
    await asyncio.sleep(0.1)

    assert logins == [1]
    assert len(broker.conns) == 2
    for watch in watches:
        await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_a_token_refresh_is_no_drop_and_every_watcher_keeps_delivering(caplog) -> None:
    """@ratpic83's 26 "drops" a day were our own token refresh. It is not
    logged, not reconnected a second time, and the watchers carry on on
    the new connection."""
    caplog.set_level(logging.INFO, logger="roombapy_prime")
    robot, client, broker = await _setup()
    watches = [_Watch(robot.watch_state()), _Watch(robot.watch_mission_timeline())]
    await _subscribed(broker, _state_topic(client), _timeline_topic(client))

    await client.replace_token(_token())
    await asyncio.sleep(0.1)
    broker.live.deliver(_state_topic(client), b'{"a": 1}')
    broker.live.deliver(_timeline_topic(client), b'{"b": 2}')
    await _until(lambda: watches[0].got == [{"a": 1}] and watches[1].got == [{"b": 2}])

    assert len(broker.conns) == 2
    assert "MQTT connection dropped" not in caplog.text
    assert "watch resumed" not in caplog.text
    for watch in watches:
        await watch.stop()
    await robot.disconnect()


# --- a connection someone else rebuilt -----------------------------------


@pytest.mark.asyncio
async def test_a_watcher_backing_off_adopts_a_connection_a_read_rebuilt() -> None:
    """A watcher in its backoff woke after a shadow read had reconnected
    lazily, and tore that connection down again; the read then failed
    (review finding)."""
    robot, client, broker = await _setup()
    robot._FIRST_RECONNECT_BACKOFF = 0.3
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))

    broker.connect_fail = 1
    broker.live.drop("blip")
    await _until(lambda: len(broker.conns) == 2)  # the watcher's failed attempt

    def answer(conn: _Conn, topic: str, _payload: Any) -> None:
        if topic.endswith("/get"):
            conn.deliver(topic + "/accepted", b'{"state": {}}')

    broker.on_publish = answer
    response = await client.get_shadow(timeout=1.0)  # rebuilds lazily
    assert response.topic.endswith("/get/accepted")
    await asyncio.sleep(0.4)  # past the watcher's backoff

    assert len(broker.conns) == 3
    assert client.connected
    broker.live.deliver(_state_topic(client), b'{"n": 1}')
    await _until(lambda: watch.got == [{"n": 1}])
    await watch.stop()
    await robot.disconnect()


# --- closing -------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_is_not_undone_by_the_watchers() -> None:
    """Home Assistant calls robot.disconnect() on stop and on unload,
    before its tasks are cancelled. watch_live_map() rebuilt the
    connection at once, and a watcher in its backoff did so a moment
    later -- a connection nobody owned, under the robot's client id
    (review finding; 0.4.x too)."""
    robot, client, broker = await _setup()
    watches = [
        _Watch(robot.watch_state()),
        _Watch(robot.watch_live_map(keep_alive_interval=999.0)),
    ]
    await _subscribed(broker, _state_topic(client), client.livemap_topic("irbt"))

    await robot.disconnect()
    await asyncio.sleep(0.2)

    assert len(broker.conns) == 1
    assert broker.open_connections() == []
    assert all(not w.task.done() for w in watches), "watchers wait, they do not end"
    for watch in watches:
        await watch.stop()


@pytest.mark.asyncio
async def test_a_closed_client_raises_instead_of_reconnecting() -> None:
    robot, client, broker = await _setup()
    await robot.disconnect()

    with pytest.raises(ShadowError, match="closed"):
        await client.get_shadow(timeout=0.1)
    assert len(broker.conns) == 1


@pytest.mark.asyncio
async def test_connect_after_disconnect_brings_the_watchers_back() -> None:
    robot, client, broker = await _setup()
    watch = _Watch(robot.watch_state())
    live = _Watch(robot.watch_live_map(keep_alive_interval=999.0))
    await _subscribed(broker, _state_topic(client), client.livemap_topic("irbt"))

    await robot.disconnect()
    await robot.connect()
    await _subscribed(broker, _state_topic(client), client.livemap_topic("irbt"))
    await asyncio.sleep(0.05)
    broker.live.deliver(_state_topic(client), b'{"n": 1}')
    broker.live.deliver(
        client.livemap_topic("irbt"),
        b'{"map_update": {"livemap_url": "https://example.invalid/x.png"}}',
    )

    await _until(lambda: watch.got == [{"n": 1}] and len(live.got) == 1)
    assert len(broker.conns) == 2
    for w in (watch, live):
        await w.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_disconnect_during_a_reconnect_leaves_nothing_open() -> None:
    """disconnect() took no lock: a reconnect finishing after it returned
    kept a live connection pinging (review finding; 0.4.x too)."""
    robot, client, broker = await _setup()
    broker.connect_delay = 0.1
    rebuilding = asyncio.ensure_future(client.reconnect())
    await asyncio.sleep(0.02)

    await robot.disconnect()
    await asyncio.wait({rebuilding})

    assert broker.open_connections() == []
    assert not client.connected


@pytest.mark.asyncio
async def test_connect_twice_starts_one_refresh_loop_and_one_connection() -> None:
    relogin = AsyncMock()
    robot, client, broker = await _setup(relogin=relogin, expires=int(time.time()) + 3600)
    first = robot._refresh_task

    await robot.connect()

    assert robot._refresh_task is first
    assert len(broker.conns) == 1
    await robot.disconnect()


# --- the live map ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_live_map_is_restored_by_the_reconnect_and_keeps_yielding() -> None:
    """After the first reconnect the live map used to be permanently
    dead: no subscription, an empty queue, and a keep-alive still
    reporting success over REST (@chairstacker). The reconnect restores
    it now like every other persistent topic -- and the generator
    survives the drop."""
    robot, client, broker = await _setup()
    topic = client.livemap_topic("irbt")
    live = _Watch(robot.watch_live_map(keep_alive_interval=999.0))
    await _subscribed(broker, topic)

    broker.live.drop("blip")
    await _until(lambda: len(broker.conns) == 2 and client.connected)
    await _subscribed(broker, topic)
    broker.live.deliver(topic, b'{"map_update": {"livemap_url": "https://example.invalid/a.png"}}')

    await _until(lambda: len(live.got) == 1)
    assert live.got[0].livemap_url == "https://example.invalid/a.png"
    await live.stop()
    await robot.disconnect()


# --- cancellation ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cancel_during_the_losers_clean_up_is_not_swallowed() -> None:
    """The loser was awaited under suppress(BaseException): a cancel of
    the watcher landing there was taken for the loser's, and the watcher
    kept running (review finding; 0.4.x too)."""
    winner = asyncio.ensure_future(asyncio.sleep(0))

    async def slow_to_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
            raise

    loser = asyncio.ensure_future(slow_to_cancel())
    await asyncio.sleep(0)
    outer = asyncio.ensure_future(_first_of(winner, loser))
    await asyncio.sleep(0.01)  # winner done, outer now awaits the loser

    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer


@pytest.mark.asyncio
async def test_a_watcher_cancelled_mid_reconnect_hands_over_to_the_others() -> None:
    robot, client, broker = await _setup()
    state = _Watch(robot.watch_state())
    timeline = _Watch(robot.watch_mission_timeline())
    await _subscribed(broker, _state_topic(client), _timeline_topic(client))
    broker.connect_delay = 0.1

    broker.live.drop("blip")
    await _until(lambda: len(broker.conns) == 2)  # someone is rebuilding
    await state.stop()
    broker.connect_delay = 0.0

    await _until(lambda: client.connected, timeout=3.0)
    await asyncio.sleep(0.05)
    broker.live.deliver(_timeline_topic(client), b'{"n": 1}')
    await _until(lambda: timeline.got == [{"n": 1}])
    await timeline.stop()
    await robot.disconnect()


# --- third review: connect and disconnect racing, cancels, backoff ---------


@pytest.mark.asyncio
async def test_a_connect_queued_ahead_of_a_disconnect_does_not_undo_it() -> None:
    """connect() waited for a rebuild, disconnect() queued behind it. The
    connect then opened a second connection over the live one, and the
    disconnect closed only the new one -- the old one ran on, and the
    watchers rebuilt what had been closed (review finding)."""
    robot, client, broker = await _setup()
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))
    broker.connect_delay = 0.05

    rebuilding = asyncio.ensure_future(client.reconnect())
    await asyncio.sleep(0.01)
    connecting = asyncio.ensure_future(client.connect())
    await asyncio.sleep(0.01)
    await client.disconnect()
    await asyncio.wait({rebuilding, connecting})
    await asyncio.sleep(0.2)

    assert client.closed
    assert broker.open_connections() == []
    await watch.stop()


@pytest.mark.asyncio
async def test_a_disconnect_during_robot_connect_leaves_no_refresh_loop() -> None:
    """The refresh loop started after a disconnect() that had found none
    to stop: a real login every token lifetime, for a closed client
    (review finding)."""
    relogin = AsyncMock()
    robot, client, broker = await _setup(relogin=relogin, expires=int(time.time()) + 3600)
    await robot.disconnect()
    broker.connect_delay = 0.1

    connecting = asyncio.ensure_future(robot.connect())
    await asyncio.sleep(0.01)
    await robot.disconnect()
    await asyncio.wait({connecting})

    assert client.closed
    assert robot._refresh_task is None
    assert broker.open_connections() == []


@pytest.mark.asyncio
async def test_a_cancelled_rebuild_is_closed_before_disconnect_returns() -> None:
    """Home Assistant's unload: the live-map entity goes first and
    cancels its watcher in the middle of a rebuild, then
    robot.disconnect(). The handshake came up after disconnect() had
    returned (review finding)."""
    robot, client, broker = await _setup()
    live = _Watch(robot.watch_live_map(keep_alive_interval=999.0))
    await _subscribed(broker, client.livemap_topic("irbt"))
    broker.connect_delay = 0.2
    broker.live.drop("x")
    await _until(lambda: len(broker.conns) == 2)  # the rebuild's handshake
    await asyncio.sleep(0.02)

    await live.stop()
    await robot.disconnect()
    returned = time.monotonic()

    assert broker.open_connections() == []
    await asyncio.sleep(0.3)
    assert broker.open_connections() == []
    came_up_later = [
        c for c in broker.conns if c.entered_at is not None and c.entered_at > returned
    ]
    assert came_up_later == []


@pytest.mark.asyncio
async def test_no_two_handshakes_with_one_client_id_at_once() -> None:
    robot, client, broker = await _setup()
    in_flight: list[int] = []
    most = [0]
    original = _Conn.__aenter__

    async def counting(self: _Conn) -> _Conn:
        in_flight.append(1)
        most[0] = max(most[0], len(in_flight))
        try:
            return await original(self)
        finally:
            in_flight.pop()

    broker.connect_delay = 0.1
    _Conn.__aenter__ = counting  # type: ignore[method-assign]
    try:
        first = asyncio.ensure_future(client.reconnect())
        await asyncio.sleep(0.03)
        first.cancel()
        await asyncio.wait({first})
        await client.reconnect()
    finally:
        _Conn.__aenter__ = original  # type: ignore[method-assign]

    assert most[0] == 1
    assert len(broker.open_connections()) == 1
    await robot.disconnect()


@pytest.mark.asyncio
async def test_a_failed_connect_leaves_a_closed_client_closed() -> None:
    """Its watchers woke and rebuilt the connection in the background,
    after the caller had been told the connect failed (review finding)."""
    robot, client, broker = await _setup()
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))
    await robot.disconnect()

    broker.connect_fail = 1
    with pytest.raises(ShadowError):
        await client.connect()
    await asyncio.sleep(0.2)

    assert client.closed
    assert broker.open_connections() == []
    await watch.stop()


@pytest.mark.asyncio
async def test_a_backoff_ends_when_a_read_rebuilds_the_connection() -> None:
    """The watcher holding the lock slept its whole backoff while a
    shadow read had already rebuilt the connection, and the other
    watchers sat behind it (review finding)."""
    robot, client, broker = await _setup()
    robot._FIRST_RECONNECT_BACKOFF = 5.0
    state = _Watch(robot.watch_state())
    timeline = _Watch(robot.watch_mission_timeline())
    await _subscribed(broker, _state_topic(client), _timeline_topic(client))

    broker.connect_fail = 1
    broker.live.drop("blip")
    await _until(lambda: len(broker.conns) == 2)  # the failed attempt

    def answer(conn: _Conn, topic: str, _payload: Any) -> None:
        if topic.endswith("/get"):
            conn.deliver(topic + "/accepted", b'{"state": {}}')

    broker.on_publish = answer
    await client.get_shadow(timeout=1.0)
    broker.live.deliver(_timeline_topic(client), b'{"n": 1}')

    await _until(lambda: timeline.got == [{"n": 1}], timeout=1.0)
    for watch in (state, timeline):
        await watch.stop()
    await robot.disconnect()



# --- where the time went, and no robot id in the log (0.8.2) -------------

_LONG_BLID = "0123456789ABCDEF"


@pytest.mark.asyncio
async def test_the_resume_line_says_where_the_time_went(caplog) -> None:
    """@glowpartyy: one resume took 40.6 s with no warning in between, and
    nothing said where the time went. The line names each stage, and
    `reconnect_log` keeps it, durations and counts only."""
    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    robot, client, broker = await _setup()
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))

    broker.connect_delay = 0.05
    broker.live.drop("blip")
    await _until(lambda: "watch resumed" in caplog.text)

    line = next(r.getMessage() for r in caplog.records if "watch resumed" in r.getMessage())
    assert re.search(
        r"after \d+\.\ds \(waiting for another reconnect \d+\.\ds, login 0\.0s, "
        r"connecting \d+\.\ds, 0 failed attempt\(s\) with 0\.0s backoff, "
        r"subscriptions \d+\.\ds\)",
        line,
    ), line
    [entry] = robot.reconnect_log
    assert entry["rebuilt_by"] == "this_watcher"
    assert entry["connecting_s"] >= 0.05
    assert entry["failed_attempts"] == 0 and entry["login_s"] == 0.0
    assert entry["total_s"] >= entry["connecting_s"] + entry["subscriptions_s"] - 0.001
    assert set(entry) == {
        "at", "reason", "rebuilt_by", "total_s", "waiting_s", "login_s",
        "connecting_s", "failed_attempts", "backoff_s", "subscriptions_s",
    }
    await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_failed_attempts_and_their_backoff_are_counted(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    robot, client, broker = await _setup()
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))

    broker.connect_fail = 2
    broker.live.drop("blip")
    await _until(lambda: "watch resumed" in caplog.text)

    [entry] = robot.reconnect_log
    assert entry["failed_attempts"] == 2
    assert entry["backoff_s"] > 0.0
    await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_a_login_on_the_way_is_its_own_stage(caplog) -> None:
    """A token that is due means a full login before the reconnect -- the
    other silent stretch a 40-second resume could hide."""
    caplog.set_level(logging.WARNING, logger="roombapy_prime")

    async def relogin() -> Any:
        await asyncio.sleep(0.05)
        result = MagicMock()
        result.token_for_blid.return_value = _token(expires=int(time.time()) + 3600)
        return result

    robot, client, broker = await _setup(relogin=relogin, expires=int(time.time()) + 60)
    if robot._refresh_task is not None:
        robot._refresh_task.cancel()
        await asyncio.wait({robot._refresh_task})
        robot._refresh_task = None
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))

    broker.live.drop("blip")
    await _until(lambda: bool(robot.reconnect_log))
    assert robot.reconnect_log[0]["login_s"] >= 0.05
    await watch.stop()
    await robot.disconnect()


@pytest.mark.asyncio
async def test_no_robot_id_in_the_reconnect_lines(caplog) -> None:
    """The topics are `$aws/things/<blid>/...`, and these lines are the
    ones people paste into issues. Four characters still tell two robots
    of one account apart."""
    caplog.set_level(logging.DEBUG, logger="roombapy_prime")
    robot, client, broker = await _setup(blid=_LONG_BLID)
    watch = _Watch(robot.watch_state())
    await _subscribed(broker, _state_topic(client))

    broker.live.drop("blip")
    await _until(lambda: "watch resumed" in caplog.text)
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("MQTT connection dropped" in line for line in lines)
    assert not any(_LONG_BLID in line for line in lines), lines
    assert any("…CDEF" in line for line in lines)
    await watch.stop()
    await robot.disconnect()


def test_the_log_keeps_the_last_twenty_and_hands_out_copies() -> None:
    from roombapy_prime.prime_robot import _ResumeTiming

    robot = PrimeRobot(blid=BLID, mqtt_client=MagicMock(), rest_client=AsyncMock())
    for _ in range(25):
        robot._resumed("t", _ResumeTiming(time.monotonic()), "r", rebuilt_by="this_watcher")
    assert len(robot.reconnect_log) == PrimeRobot.RECONNECT_LOG_SIZE == 20
    robot.reconnect_log[0]["reason"] = "changed"
    assert robot.reconnect_log[0]["reason"] == "r"


# --- two robots, one account (0.9.0, #91) ---------------------------------

_BLID_A = "0123456789ABCDEF"
_BLID_B = "FEDCBA9876543210"


def _deployment_login(deployments: dict[str, str]) -> Any:
    from roombapy_prime.auth import CloudCredentials, LoginResult, RobotLoginEntry
    return LoginResult(
        mqtt_endpoint="e", http_base="https://base.example.invalid",
        http_base_auth="https://auth.example.invalid",
        credentials=CloudCredentials(access_key_id="A", secret_key="S", session_token="x", cognito_id="synthetic"),
        robots={blid: RobotLoginEntry(sku="W155020", svc_deployment_id=depl) for blid, depl in deployments.items()},
        connection_tokens=[ConnectionToken(client_id="cid", iot_token="t", iot_signature="s",
            iot_authorizer_name="a", expires=None, devices=list(deployments))],
        raw={}, irbt_topic_prefix="v007-irbthbu",
        deployments={d: {"irbtTopics": f"{d}-irbthbu"} for d in ("v007", "v011")},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["proactive", "after_drop"])
async def test_a_new_deployment_reaches_commands_and_running_watchers(monkeypatch, recovery) -> None:
    from roombapy_prime.account import CloudAccount
    broker = _Broker()
    monkeypatch.setattr(PrimeMqttClient, "_build_client", lambda self, timeout: broker.build(timeout))
    account = CloudAccount(MagicMock(), "user@example.com", "synthetic", "DE",
                           _deployment_login({_BLID_A: "v007", _BLID_B: "v007"}))
    first = await account.prime_robot(_BLID_A, auto_refresh=True)
    second = await account.prime_robot(_BLID_B, auto_refresh=True)
    await first.connect()
    await second.connect()
    first._FIRST_RECONNECT_BACKOFF = second._FIRST_RECONNECT_BACKOFF = 0.01
    old = f"v007-irbthbu/things/{_BLID_A}/mission/timeline/report"
    new = f"v011-irbthbu/things/{_BLID_A}/mission/timeline/report"
    other = f"v007-irbthbu/things/{_BLID_B}/mission/timeline/report"
    watcher = _Watch(first.watch_mission_timeline())
    companion = _Watch(second.watch_mission_timeline())
    client = first._mqtt.connection
    try:
        await _subscribed(broker, old, other)
        from dataclasses import replace
        renewed = _deployment_login({_BLID_A: "v011", _BLID_B: "v007"})
        reauthenticate = AsyncMock(return_value=renewed)
        monkeypatch.setattr(account, "_login", reauthenticate)
        expired = replace(account._login_result.primary_token(), expires=int(time.time()) - 1)
        account._login_result = replace(account._login_result, connection_tokens=[expired])
        client._token = expired
        if recovery == "proactive":
            await first._refresh_loop()
        else:
            broker.live.drop()
        await _until(lambda: len(broker.conns) == 2 and client.connected)
        # The original bug can still return True while sending to the
        # obsolete namespace. A PUBACK is not a routing correctness check.
        assert await first.send_simple_command("start") is True
        assert broker.live.published[-1][0] == f"v011-irbthbu/things/{_BLID_A}/cmd"
        await _subscribed(broker, new, other)
        assert old not in broker.live.subscribed
        assert first._irbt_topic_prefix == "v011-irbthbu"
        assert first.svc_deployment_id == "v011"
        assert second._irbt_topic_prefix == "v007-irbthbu"
        broker.live.deliver(new, b'{"source":"new"}')
        broker.live.deliver(other, b'{"source":"other"}')
        await _until(lambda: len(watcher.got) == len(companion.got) == 1)
        reauthenticate.assert_awaited_once()
        assert account._login_result is renewed
        assert len(broker.conns) == 2
    finally:
        await watcher.stop()
        await companion.stop()
        assert new not in client._persistent
        await first.disconnect()
        await second.disconnect()


@pytest.mark.asyncio
async def test_shared_refresh_updates_a_companion_that_does_not_refresh_itself(monkeypatch) -> None:
    from roombapy_prime.account import CloudAccount
    broker = _Broker()
    monkeypatch.setattr(PrimeMqttClient, "_build_client", lambda self, timeout: broker.build(timeout))
    account = CloudAccount(MagicMock(), "user@example.com", "synthetic", "DE",
                           _deployment_login({_BLID_A: "v007", _BLID_B: "v007"}))
    first = await account.prime_robot(_BLID_A)
    second = await account.prime_robot(_BLID_B)
    await first.connect()
    await second.connect()
    try:
        first._relogin = AsyncMock(return_value=_deployment_login({_BLID_A: "v007", _BLID_B: "v011"}))
        first._mqtt.connection._token = _token(expires=int(time.time()) - 1)
        await first._refresh_loop()
        assert second._irbt_topic_prefix == "v011-irbthbu"
        assert second.svc_deployment_id == "v011"
        assert await second.send_simple_command("start") is True
        assert broker.live.published[-1][0] == f"v011-irbthbu/things/{_BLID_B}/cmd"
        assert len(broker.conns) == 2
    finally:
        await first.disconnect()
        await second.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", ["same", "missing_prefix", "missing_robot"])
async def test_refresh_with_unchanged_or_missing_metadata_keeps_the_known_route(monkeypatch, metadata) -> None:
    from dataclasses import replace
    from roombapy_prime.account import CloudAccount
    from roombapy_prime.auth import RobotLoginEntry
    broker = _Broker()
    monkeypatch.setattr(PrimeMqttClient, "_build_client", lambda self, timeout: broker.build(timeout))
    before = _deployment_login({_BLID_A: "v007"})
    after = before
    if metadata == "missing_prefix":
        after = replace(before, irbt_topic_prefix=None, deployments={},
                        robots={_BLID_A: RobotLoginEntry(sku="W155020")})
    elif metadata == "missing_robot":
        after = replace(before, robots={})
    account = CloudAccount(MagicMock(), "user@example.com", "synthetic", "DE", before)
    robot = await account.prime_robot(_BLID_A)
    await robot.connect()
    try:
        robot._relogin = AsyncMock(return_value=after)
        robot._mqtt.connection._token = _token(expires=int(time.time()) - 1)
        await robot._refresh_loop()
        assert robot._irbt_topic_prefix == "v007-irbthbu"
        assert robot.svc_deployment_id == ("v007" if metadata == "same" else None)
        assert await robot.send_simple_command("start") is True
        assert len(broker.live.published) == 1
        assert len(broker.conns) == 2
    finally:
        await robot.disconnect()


def _two_robot_account(broker: _Broker, monkeypatch: Any, expires: int | None = None) -> Any:
    """A CloudAccount whose one login covers two Prime robots with one
    token, as @Baby-scarf's does, over the fake broker."""
    from roombapy_prime.account import CloudAccount
    from roombapy_prime.auth import CloudCredentials, LoginResult, RobotLoginEntry

    monkeypatch.setattr(PrimeMqttClient, "_build_client", lambda self, timeout: broker.build(timeout))
    monkeypatch.setattr(PrimeMqttClient, "SUBACK_TIMEOUT_SECONDS", 0.05)
    token = ConnectionToken(
        client_id="cid", iot_token="t", iot_signature="s", iot_authorizer_name="a",
        expires=expires, devices=[_BLID_A, _BLID_B],
    )
    login_result = LoginResult(
        mqtt_endpoint="e", http_base="https://base.example.invalid",
        http_base_auth="https://auth.example.invalid",
        credentials=CloudCredentials(
            access_key_id="A", secret_key="S", session_token="x", cognito_id="us-east-1:0",
        ),
        robots={_BLID_A: RobotLoginEntry(sku="W155020"), _BLID_B: RobotLoginEntry(sku="W155020")},
        connection_tokens=[token], raw={}, irbt_topic_prefix="irbt",
    )
    return CloudAccount(MagicMock(), "user", "secret", "DE", login_result)


async def _two_robots(broker: _Broker, monkeypatch: Any, **kw: Any) -> tuple[Any, PrimeRobot, PrimeRobot]:
    account = _two_robot_account(broker, monkeypatch, **kw)
    first = await account.prime_robot(_BLID_A)
    second = await account.prime_robot(_BLID_B)
    for robot in (first, second):
        robot._SUBACK_RECHECK_SECONDS = 0.01
        robot._FIRST_RECONNECT_BACKOFF = 0.01
    await first.connect()
    await second.connect()
    return account, first, second


@pytest.mark.asyncio
async def test_two_robots_of_one_account_share_one_connection(monkeypatch, caplog) -> None:
    """@Baby-scarf, @mattdmrc (#91): one login, one token, one client id
    for both robots. A connection per robot made the broker drop the
    older one every time the other connected: 120 drops in five minutes,
    on both robots, for hours."""
    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    broker = _Broker()
    broker.evict_same_id = True
    _account, first, second = await _two_robots(broker, monkeypatch)
    watches = [_Watch(first.watch_state()), _Watch(second.watch_state())]
    topic_a = f"$aws/things/{_BLID_A}/shadow/update/delta"
    topic_b = f"$aws/things/{_BLID_B}/shadow/update/delta"
    await _subscribed(broker, topic_a, topic_b)
    await asyncio.sleep(0.2)

    broker.live.deliver(topic_a, b'{"a": 1}')
    broker.live.deliver(topic_b, b'{"b": 2}')
    await _until(lambda: watches[0].got == [{"a": 1}] and watches[1].got == [{"b": 2}])
    assert len(broker.conns) == 1
    assert "MQTT connection dropped" not in caplog.text
    for watch in watches:
        await watch.stop()
    await first.disconnect()
    await second.disconnect()


@pytest.mark.asyncio
async def test_a_drop_of_the_shared_connection_is_rebuilt_once(monkeypatch, caplog) -> None:
    """One coordinator and one warning for both robots, not one each."""
    caplog.set_level(logging.WARNING, logger="roombapy_prime")
    broker = _Broker()
    _account, first, second = await _two_robots(broker, monkeypatch)
    watches = [_Watch(first.watch_state()), _Watch(second.watch_state())]
    topic_a = f"$aws/things/{_BLID_A}/shadow/update/delta"
    topic_b = f"$aws/things/{_BLID_B}/shadow/update/delta"
    await _subscribed(broker, topic_a, topic_b)

    broker.live.drop("blip")
    await _until(lambda: len(broker.conns) == 2 and first._mqtt.connected)
    await _until(lambda: "watch resumed" in caplog.text)
    await asyncio.sleep(0.05)
    broker.live.deliver(topic_a, b'{"a": 1}')
    broker.live.deliver(topic_b, b'{"b": 2}')
    await _until(lambda: watches[0].got == [{"a": 1}] and watches[1].got == [{"b": 2}])

    assert len(broker.conns) == 2
    assert caplog.text.count("MQTT connection dropped") == 1
    for watch in watches:
        await watch.stop()
    await first.disconnect()
    await second.disconnect()


@pytest.mark.asyncio
async def test_the_connection_closes_with_the_last_robot(monkeypatch) -> None:
    """Unloading one robot in Home Assistant must not cut off the other."""
    broker = _Broker()
    _account, first, second = await _two_robots(broker, monkeypatch)
    watch = _Watch(second.watch_state())
    topic_b = f"$aws/things/{_BLID_B}/shadow/update/delta"
    await _subscribed(broker, topic_b)

    await first.disconnect()
    assert broker.open_connections() == [broker.live]
    assert first._mqtt.closed and not second._mqtt.closed
    broker.live.deliver(topic_b, b'{"b": 2}')
    await _until(lambda: watch.got == [{"b": 2}])

    await watch.stop()
    await second.disconnect()
    assert broker.open_connections() == []

    await first.connect()
    assert first._mqtt.connected and len(broker.conns) == 2
    await first.disconnect()


@pytest.mark.asyncio
async def test_both_robots_renew_the_token_with_one_login_and_one_reconnect(monkeypatch) -> None:
    """Each robot's refresh loop wakes for the same token. The first
    renews it under the connection's lock; the second finds it renewed."""
    broker = _Broker()
    account = _two_robot_account(broker, monkeypatch, expires=int(time.time()) + 300)
    logins: list[int] = []
    fresh = ConnectionToken(
        client_id="cid", iot_token="t2", iot_signature="s", iot_authorizer_name="a",
        expires=int(time.time()) + 3600, devices=[_BLID_A, _BLID_B],
    )

    async def relogin(_blid: str) -> Any:
        logins.append(1)
        await asyncio.sleep(0.02)
        result = MagicMock()
        result.token_for_blid.return_value = fresh
        return result

    monkeypatch.setattr(account, "_prime_relogin", relogin)
    first = await account.prime_robot(_BLID_A, auto_refresh=True)
    second = await account.prime_robot(_BLID_B, auto_refresh=True)
    await first.connect()
    await second.connect()
    await _until(lambda: len(broker.conns) == 2 and first._mqtt.connected)
    await asyncio.sleep(0.1)

    assert logins == [1]
    assert len(broker.conns) == 2
    await first.disconnect()
    await second.disconnect()


async def _deployment_review_setup(monkeypatch, count=1):
    from dataclasses import replace
    from roombapy_prime.account import CloudAccount
    broker = _Broker()
    monkeypatch.setattr(PrimeMqttClient, '_build_client', lambda self, timeout: broker.build(timeout))
    before = _deployment_login(dict.fromkeys([_BLID_A, _BLID_B][:count], 'v007'))
    account = CloudAccount(MagicMock(), 'user@example.com', 'synthetic', 'DE', before)
    robots = [await account.prime_robot(b) for b in before.robots]
    for r in robots:
        await r.connect()
    after = replace(_deployment_login(dict.fromkeys(before.robots, 'v011')),
                    connection_tokens=[replace(before.primary_token(), iot_token='new')])
    return broker, robots, before, after

@pytest.mark.asyncio
async def test_cancel_before_reconnect_lock_must_not_commit_routes(monkeypatch):
    broker, robots, before, after = await _deployment_review_setup(monkeypatch)
    robot = robots[0]
    client = robot._mqtt.connection
    token = client._token
    await client._reconnect_lock.acquire()
    pending = asyncio.create_task(robot._replace_login(after))
    try:
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert client._token is token
        assert robot._irbt_topic_prefix == 'v007-irbthbu'
        assert len(broker.conns) == 1
    finally:
        client._reconnect_lock.release()
        await robot.disconnect()

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "invalid_prefix"])
async def test_resolver_failure_must_not_partially_update_companions(monkeypatch, failure):
    broker, robots, before, after = await _deployment_review_setup(monkeypatch, 2)
    from roombapy_prime.auth import LoginResult
    original = LoginResult.irbt_topic_prefix_for
    count = 0
    def fail_second(self, blid):
        nonlocal count
        count += 1
        if count == 2:
            if failure == 'invalid_prefix':
                return {'not': 'a prefix'}
            raise ValueError('synthetic metadata resolution failure')
        return original(self, blid)
    monkeypatch.setattr(LoginResult, 'irbt_topic_prefix_for', fail_second)
    client = robots[0]._mqtt.connection
    token = client._token
    try:
        with pytest.raises((ValueError, TypeError)):
            await robots[0]._replace_login(after)
        assert client._token is token
        assert [r._irbt_topic_prefix for r in robots] == ['v007-irbthbu'] * 2
        assert len(broker.conns) == 1
    finally:
        for r in robots:
            await r.disconnect()

@pytest.mark.asyncio
async def test_failed_connect_after_commit_can_restore_without_command_replay(monkeypatch):
    broker, robots, before, after = await _deployment_review_setup(monkeypatch)
    robot = robots[0]
    client = robot._mqtt.connection
    old = f'v007-irbthbu/things/{_BLID_A}/mission/timeline/report'
    new = old.replace('v007', 'v011')
    got = []
    callback = _DeploymentCallback(lambda response: got.append(response.payload))
    await client.subscribe(old, callback)
    try:
        broker.connect_fail = 1
        with pytest.raises(ShadowError):
            await robot._replace_login(after)
        assert not client.connected
        assert robot._irbt_topic_prefix == 'v011-irbthbu'
        assert new in client._persistent and old not in client._persistent
        assert not any(c.published for c in broker.conns)
        assert await robot.send_simple_command('start') is True
        assert len(broker.conns) == 3
        assert sum(len(c.published) for c in broker.conns) == 1
        assert broker.live.published[0][0] == f'v011-irbthbu/things/{_BLID_A}/cmd'
        assert new in broker.live.subscribed and old not in broker.live.subscribed
        broker.live.deliver(new, b'{"new":true}')
        await _until(lambda: len(got) == 1)
        await client.unsubscribe(old, callback)
        assert new not in client._persistent
    finally:
        await robot.disconnect()

@pytest.mark.asyncio
async def test_single_robot_migration_survives_an_ordinary_drop_without_extra_login(monkeypatch):
    broker, robots, before, after = await _deployment_review_setup(monkeypatch)
    robot = robots[0]
    old = f'v007-irbthbu/things/{_BLID_A}/mission/timeline/report'
    new = old.replace('v007', 'v011')
    watcher = _Watch(robot.watch_mission_timeline())
    relogin = AsyncMock(return_value=after)
    robot._relogin = relogin
    robot._FIRST_RECONNECT_BACKOFF = .01
    try:
        await _subscribed(broker, old)
        await robot._replace_login(after)
        await _subscribed(broker, new)
        broker.live.drop()
        await _until(lambda: len(broker.conns) == 3 and robot._mqtt.connected)
        await _subscribed(broker, new)
        assert old not in broker.live.subscribed
        relogin.assert_not_awaited()
        assert await robot.send_simple_command('start') is True
        assert sum(len(c.published) for c in broker.conns) == 1
        broker.live.deliver(new, b'{"new":true}')
        await _until(lambda: len(watcher.got) == 1)
    finally:
        await watcher.stop()
        await robot.disconnect()


@pytest.mark.asyncio
async def test_two_simultaneous_refreshes_migrate_both_routes_once(monkeypatch):
    broker, robots, before, after = await _deployment_review_setup(monkeypatch, 2)
    renewed = AsyncMock(return_value=after)
    client = robots[0]._mqtt.connection
    for robot in robots:
        robot._relogin = renewed
    client._token = _token(expires=int(time.time()) - 1)
    try:
        await asyncio.gather(*(robot._refresh_loop() for robot in robots))
        renewed.assert_awaited_once()
        assert len(broker.conns) == 2
        assert not any(conn.published for conn in broker.conns)
        for robot in robots:
            assert robot._irbt_topic_prefix == 'v011-irbthbu'
            assert await robot.send_simple_command('start') is True
        assert len(broker.live.published) == 2
        assert {topic for topic, _payload in broker.live.published} == {
            f'v011-irbthbu/things/{blid}/cmd' for blid in (_BLID_A, _BLID_B)
        }
        assert len(broker.conns) == 2
    finally:
        for robot in robots:
            await robot.disconnect()


@pytest.mark.asyncio
async def test_renewal_does_not_change_the_other_token_connection(monkeypatch):
    from dataclasses import replace
    from roombapy_prime.account import CloudAccount
    broker = _Broker()
    monkeypatch.setattr(PrimeMqttClient, '_build_client', lambda self, timeout: broker.build(timeout))
    before = _deployment_login({_BLID_A: 'v007', _BLID_B: 'v007'})
    tokens = [replace(before.primary_token(), client_id=f'cid-{i}', devices=[blid])
              for i, blid in enumerate((_BLID_A, _BLID_B))]
    before = replace(before, connection_tokens=tokens)
    after = replace(_deployment_login({_BLID_A: 'v011', _BLID_B: 'v011'}),
                    connection_tokens=tokens)
    account = CloudAccount(MagicMock(), 'user@example.com', 'synthetic', 'DE', before)
    first = await account.prime_robot(_BLID_A)
    second = await account.prime_robot(_BLID_B)
    await first.connect()
    await second.connect()
    first_client, second_client = first._mqtt.connection, second._mqtt.connection
    untouched = second_client._client
    try:
        assert first_client is not second_client
        await first._replace_login(after)
        assert first._irbt_topic_prefix == 'v011-irbthbu'
        assert second._irbt_topic_prefix == 'v007-irbthbu'
        assert second_client._client is untouched
        assert second_client.generation == 1
        assert len(broker.conns) == 3
        assert not any(conn.published for conn in broker.conns)
        await second._replace_login(after)
        assert second._irbt_topic_prefix == 'v011-irbthbu'
        assert first_client.generation == second_client.generation == 2
        assert len(broker.conns) == 4
    finally:
        await first.disconnect()
        await second.disconnect()


@pytest.mark.asyncio
async def test_cancel_after_close_retains_the_new_routes_for_later_recovery(monkeypatch):
    broker, robots, before, after = await _deployment_review_setup(monkeypatch)
    robot = robots[0]
    client = robot._mqtt.connection
    old = f'v007-irbthbu/things/{_BLID_A}/mission/timeline/report'
    new = old.replace('v007', 'v011')
    callback = _DeploymentCallback(lambda response: None)
    await client.subscribe(old, callback)
    broker.connect_delay = .05
    pending = asyncio.create_task(robot._replace_login(after))
    try:
        await _until(lambda: len(broker.conns) == 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not client.connected
        assert robot._irbt_topic_prefix == 'v011-irbthbu'
        assert new in client._persistent and old not in client._persistent
        broker.connect_delay = 0
        assert await robot.send_simple_command('start') is True
        assert sum(len(conn.published) for conn in broker.conns) == 1
        assert broker.live.published[0][0] == f'v011-irbthbu/things/{_BLID_A}/cmd'
        assert new in broker.live.subscribed
        await client.unsubscribe(old, callback)
        assert new not in client._persistent
    finally:
        await robot.disconnect()


@pytest.mark.asyncio
async def test_a_publish_in_flight_at_migration_is_not_replayed(monkeypatch):
    broker, robots, before, after = await _deployment_review_setup(monkeypatch)
    robot = robots[0]
    entered, finish = asyncio.Event(), asyncio.Event()
    original = _Conn.publish
    old_connection = broker.live

    async def lose_old_puback(conn, topic, payload=None, qos=0, timeout=None):
        if conn is old_connection:
            conn.published.append((topic, payload))
            entered.set()
            await finish.wait()
            raise MqttError('synthetic lost PUBACK during migration')
        return await original(conn, topic, payload, qos, timeout)

    monkeypatch.setattr(_Conn, 'publish', lose_old_puback)
    pending = asyncio.create_task(robot.send_simple_command('start'))
    try:
        await entered.wait()
        await robot._replace_login(after)
        finish.set()
        assert await pending is False
        assert len(broker.conns) == 2
        assert sum(len(conn.published) for conn in broker.conns) == 1
        assert old_connection.published[0][0] == f'v007-irbthbu/things/{_BLID_A}/cmd'
        assert broker.live.published == []
        assert robot._irbt_topic_prefix == 'v011-irbthbu'
    finally:
        finish.set()
        await pending
        await robot.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("renewed_deployment", ["v007", "v011"])
async def test_constructing_a_second_handle_does_not_retarget_a_live_subscription(monkeypatch, renewed_deployment):
    from roombapy_prime.account import CloudAccount
    broker = _Broker()
    monkeypatch.setattr(PrimeMqttClient, '_build_client', lambda self, timeout: broker.build(timeout))
    before = _deployment_login({_BLID_A: 'v007'})
    after = _deployment_login({_BLID_A: 'v011'})
    account = CloudAccount(MagicMock(), 'user@example.com', 'synthetic', 'DE', before)
    first = await account.prime_robot(_BLID_A)
    await first.connect()
    client = first._mqtt.connection
    old = f'v007-irbthbu/things/{_BLID_A}/mission/timeline/report'
    new = old.replace('v007', 'v011')
    got_first, got_second = [], []
    callback_a = _DeploymentCallback(lambda response: got_first.append(response.payload))
    callback_b = _DeploymentCallback(lambda response: got_second.append(response.payload))
    await client.subscribe(old, callback_a)
    account._login_result = after
    second = await account.prime_robot(_BLID_A)
    await second.connect()
    try:
        # Construction is not a token swap and must not silently move a
        # callback away from its still-active broker subscription.
        assert old in client._persistent
        assert len(broker.conns) == 1
        await second._mqtt.subscribe(new, callback_b)
        broker.live.deliver(old, b'{"before":true}')
        broker.live.deliver(new, b'{"before":true}')
        await _until(lambda: len(got_first) == len(got_second) == 1)
        await first._replace_login(_deployment_login({_BLID_A: renewed_deployment}))
        target = old if renewed_deployment == 'v007' else new
        other = new if target == old else old
        assert len(broker.conns) == 2
        assert other not in client._persistent
        assert len(client._persistent[target]) == 2
        broker.live.deliver(target, b'{"after":true}')
        await _until(lambda: len(got_first) == len(got_second) == 2)
        await client.unsubscribe(old, callback_a)
        assert client._persistent[target] == [callback_b]
        await client.unsubscribe(new, callback_b)
        assert target not in client._persistent
    finally:
        await first.disconnect()
        await second.disconnect()
