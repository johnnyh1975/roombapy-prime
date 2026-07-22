"""Smoke tests for roombapy_prime.prime_robot.PrimeRobot.

These test that PrimeRobot correctly delegates to its mqtt/rest clients
(via asyncio.to_thread for the sync mqtt_client, directly for the
already-async rest_client) -- not the underlying network behaviour,
which is covered in test_mqtt_client.py / test_rest_client.py.

The watch_state()/watch_live_map() tests below drive the mocked
subscribe() callback directly (simulating "paho's background thread
just delivered a message") rather than going through any real network
or real paho client -- consistent with the rest of this file.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from roombapy_prime.models import MapUpdateMessage, PositionUpdateMessage
from roombapy_prime.mqtt_client import ShadowResponse
from roombapy_prime.prime_robot import PrimeRobot
from roombapy_prime.rest_client import PrimeRestClient


def _robot_with_mocks() -> tuple[PrimeRobot, MagicMock, MagicMock]:
    mqtt = MagicMock()
    rest = MagicMock()
    robot = PrimeRobot(
        blid="BLID123", mqtt_client=mqtt, rest_client=rest,
        irbt_topic_prefix="irbt-fake-prefix",
    )
    return robot, mqtt, rest


def _never_disconnects(mqtt: MagicMock) -> None:
    """NEW (this session, reconnect hardening). watch_state() now races
    queue.get() against self._mqtt.wait_for_disconnect() -- tests that
    only care about normal delta delivery need the disconnect side to
    simply never fire. A fresh, never-.set() asyncio.Event's wait()
    blocks forever, which is exactly that -- distinct from returning a
    plain MagicMock(), which isn't awaitable at all and would raise a
    TypeError from asyncio.ensure_future()."""
    mqtt.wait_for_disconnect = AsyncMock(side_effect=asyncio.Event().wait)


@pytest.mark.asyncio
async def test_connect_disconnect_delegate_to_mqtt_client() -> None:
    robot, mqtt, _rest = _robot_with_mocks()

    await robot.connect(timeout=5.0)
    mqtt.connect.assert_called_once_with(5.0)

    await robot.disconnect()
    mqtt.disconnect.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_state_uses_unnamed_shadow() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.get_shadow.return_value = ShadowResponse(topic="t", payload={"ok": True})

    result = await robot.get_state()

    mqtt.get_shadow.assert_called_once_with(None, 8.0)
    assert result.payload == {"ok": True}


@pytest.mark.asyncio
async def test_get_settings_uses_named_shadow() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.get_shadow.return_value = ShadowResponse(topic="t", payload={})

    await robot.get_settings()

    mqtt.get_shadow.assert_called_once_with("rw-settings", 8.0)


@pytest.mark.asyncio
async def test_get_named_shadow_accepts_any_name() -> None:
    """NEW (this session) -- the general form get_state()/get_settings()
    are thin wrappers around. Exists to investigate the three named
    shadows never queried before: rw-constatus/rw-schedule/rw-software."""
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.get_shadow.return_value = ShadowResponse(topic="t", payload={"some": "data"})

    result = await robot.get_named_shadow("rw-constatus")

    mqtt.get_shadow.assert_called_once_with("rw-constatus", 8.0)
    assert result.payload == {"some": "data"}


@pytest.mark.asyncio
async def test_get_named_shadow_respects_custom_timeout() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.get_shadow.return_value = ShadowResponse(topic="t", payload={})

    await robot.get_named_shadow("rw-schedule", timeout=15.0)

    mqtt.get_shadow.assert_called_once_with("rw-schedule", 15.0)


@pytest.mark.asyncio
async def test_set_setting_writes_named_shadow() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.update_shadow.return_value = ShadowResponse(topic="t", payload={})

    await robot.set_setting("binPause", True)

    mqtt.update_shadow.assert_called_once_with({"binPause": True}, "rw-settings", 8.0)


@pytest.mark.asyncio
async def test_trigger_echo_via_shadow_writes_rw_constatus() -> None:
    """NEW (this session, prompted by a real bug report -- the existing
    REST-based locate action doesn't actually chime the robot). Tests
    only the mechanics (writes {"echo": value} to "rw-constatus") --
    whether this actually triggers a chime on a real device is
    genuinely unconfirmed, see the method's own docstring."""
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.update_shadow.return_value = ShadowResponse(topic="t", payload={})

    await robot.trigger_echo_via_shadow()

    mqtt.update_shadow.assert_called_once_with({"echo": True}, "rw-constatus", 8.0)


@pytest.mark.asyncio
async def test_trigger_echo_via_shadow_accepts_a_custom_value() -> None:
    """The trigger value is genuinely unconfirmed -- this lets someone
    experiment with alternatives (1, a timestamp, etc.) without needing
    a new method for each guess."""
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.update_shadow.return_value = ShadowResponse(topic="t", payload={})

    await robot.trigger_echo_via_shadow(value=1, timeout=15.0)

    mqtt.update_shadow.assert_called_once_with({"echo": 1}, "rw-constatus", 15.0)


@pytest.mark.asyncio
async def test_send_mission_command_uses_classic_shadow() -> None:
    """Tests the mechanics of send_mission_command() (still routes
    through update_shadow() with the classic/unnamed shadow) --
    NOT a claim that this is the correct transport for mission
    control anymore. See that method's docstring (session 39): this
    approach is now STRONGLY SUSPECTED WRONG for basic commands,
    superseded by send_simple_command(). Kept here only to verify the
    method still does what it's documented to do, for the
    region-based use case that remains a possible fallback."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.update_shadow.return_value = ShadowResponse(topic="t", payload={})

    cmd = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID123")
    await robot.send_mission_command(cmd)

    mqtt.update_shadow.assert_called_once_with({"cmd": cmd.to_json()}, None, 8.0)


@pytest.mark.asyncio
async def test_send_simple_command_publishes_via_cmd_topic() -> None:
    """NEW (session 39) -- the corrected mission-control path. Verifies
    routing only (real topic/payload construction is tested in
    test_mqtt_client.py's test_cmd_topic_helper/
    test_publish_cmd_sends_expected_payload_shape)."""
    robot, mqtt, _rest = _robot_with_mocks()

    await robot.send_simple_command("start")

    mqtt.publish_cmd.assert_called_once_with("irbt-fake-prefix", "start", "localApp")


@pytest.mark.asyncio
async def test_send_simple_command_without_topic_prefix_raises_immediately() -> None:
    """Same missing-prefix gate as watch_live_map() -- see
    test_watch_live_map_without_topic_prefix_raises_immediately."""
    mqtt = MagicMock()
    rest = MagicMock()
    robot = PrimeRobot(blid="BLID123", mqtt_client=mqtt, rest_client=rest, irbt_topic_prefix=None)

    with pytest.raises(RuntimeError, match="irbt_topic_prefix"):
        await robot.send_simple_command("start")

    mqtt.publish_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_send_routine_command_via_cmd_topic_publishes_full_payload() -> None:
    """NEW (session 46) -- EXPERIMENTAL, UNCONFIRMED path, see the
    method's own docstring for the full hypothesis and risk caveat.
    Verifies routing only: command.to_json() gets passed through to
    publish_cmd_payload() unchanged."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    robot, mqtt, _rest = _robot_with_mocks()
    cmd = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID123", favorite_id="fav1")

    await robot.send_routine_command_via_cmd_topic(cmd)

    mqtt.publish_cmd_payload.assert_called_once_with("irbt-fake-prefix", cmd.to_json())


@pytest.mark.asyncio
async def test_send_routine_command_via_cmd_topic_without_topic_prefix_raises_immediately() -> None:
    """Same missing-prefix gate as send_simple_command()/watch_live_map()."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    mqtt = MagicMock()
    rest = MagicMock()
    robot = PrimeRobot(blid="BLID123", mqtt_client=mqtt, rest_client=rest, irbt_topic_prefix=None)
    cmd = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID123")

    with pytest.raises(RuntimeError, match="irbt_topic_prefix"):
        await robot.send_routine_command_via_cmd_topic(cmd)

    mqtt.publish_cmd_payload.assert_not_called()


@pytest.mark.asyncio
async def test_send_umi_get_request_publishes_do_args_id_payload() -> None:
    """NEW (this session) -- EXPERIMENTAL, UNCONFIRMED path, see the
    method's own docstring for the full hypothesis and risk caveat.
    Verifies the exact payload shape found as a literal string in
    libcorebase.so: {"do": "get", "args": [...], "id": ...}."""
    robot, mqtt, _rest = _robot_with_mocks()

    await robot.send_umi_get_request(["pose"], request_id=7)

    mqtt.publish_cmd_payload.assert_called_once_with(
        "irbt-fake-prefix", {"do": "get", "args": ["pose"], "id": 7}
    )


@pytest.mark.asyncio
async def test_send_umi_get_request_default_request_id() -> None:
    robot, mqtt, _rest = _robot_with_mocks()

    await robot.send_umi_get_request(["pose"])

    mqtt.publish_cmd_payload.assert_called_once_with(
        "irbt-fake-prefix", {"do": "get", "args": ["pose"], "id": 1}
    )


@pytest.mark.asyncio
async def test_send_umi_get_request_without_topic_prefix_raises_immediately() -> None:
    """Same missing-prefix gate as the other cmd_topic()-based methods."""
    mqtt = MagicMock()
    rest = MagicMock()
    robot = PrimeRobot(blid="BLID123", mqtt_client=mqtt, rest_client=rest, irbt_topic_prefix=None)

    with pytest.raises(RuntimeError, match="irbt_topic_prefix"):
        await robot.send_umi_get_request(["pose"])

    mqtt.publish_cmd_payload.assert_not_called()


@pytest.mark.asyncio
async def test_rest_backed_methods_delegate_directly() -> None:
    robot, _mqtt, rest = _robot_with_mocks()

    async def fake_get_map_metadata(p2map_id: str) -> dict:
        assert p2map_id == "map1"
        return {"id": "map1"}

    rest.get_map_metadata = fake_get_map_metadata

    result = await robot.get_map_metadata("map1")
    assert result == {"id": "map1"}


@pytest.mark.asyncio
async def test_get_live_map_stream_uses_robot_blid() -> None:
    robot, _mqtt, rest = _robot_with_mocks()
    calls = []

    async def fake_get_live_map_stream(blid: str):
        calls.append(blid)
        return "stream-init-sentinel"

    rest.get_live_map_stream = fake_get_live_map_stream

    result = await robot.get_live_map_stream()

    assert calls == ["BLID123"]
    assert result == "stream-init-sentinel"


# --- watch_state / watch_live_map (continuous dispatch) -----------------
#
# subscribe()/unsubscribe() on the mocked mqtt client are driven directly
# here -- simulating "paho's background thread just delivered a message"
# without any real network, real paho client, or real thread involved.


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    """Small polling helper -- subscribe() runs via asyncio.to_thread, so
    there's a real (if tiny) race between scheduling that thread and the
    test being able to inspect its side effects. A short poll is more
    robust than a single fixed sleep."""
    waited = 0.0
    step = 0.01
    while not predicate() and waited < timeout:
        await asyncio.sleep(step)
        waited += step
    assert predicate(), f"condition not met within {timeout}s"


@pytest.mark.asyncio
async def test_watch_state_yields_deltas_as_they_arrive() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    _never_disconnects(mqtt)
    captured: dict = {}

    def fake_subscribe(topic, callback):
        captured["topic"] = topic
        captured["callback"] = callback

    mqtt.subscribe.side_effect = fake_subscribe

    agen = robot.watch_state()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    # shadow_topic() itself is a MagicMock call here -- its actual
    # string-construction logic is covered by test_mqtt_client.py's
    # test_shadow_topic_helper against the real implementation. Here we
    # only confirm watch_state() asked for the right thing.
    mqtt.shadow_topic.assert_called_once_with("update/delta", named=None)

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"binPause": True}))
    result = await next_task

    assert result.payload == {"binPause": True}

    await agen.aclose()
    mqtt.unsubscribe.assert_called_once_with(captured["topic"], captured["callback"])


@pytest.mark.asyncio
async def test_watch_state_named_uses_named_shadow_delta_topic() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_state(named="rw-settings")
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "topic" in captured)

    mqtt.shadow_topic.assert_called_once_with("update/delta", named="rw-settings")

    next_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_task
    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_state_delivers_multiple_messages_in_order() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_state()
    first_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"n": 1}))
    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"n": 2}))

    first = await first_task
    second = await agen.__anext__()

    assert first.payload == {"n": 1}
    assert second.payload == {"n": 2}

    await agen.aclose()


# =========================================================================
# Reconnect-after-disconnect (this session, reconnect hardening -- see
# mqtt_client.py's wait_for_disconnect()/reconnect() docstrings). Previously
# a dropped connection left this generator hung on an empty queue forever,
# with no signal to the caller anything was wrong.
# =========================================================================


@pytest.mark.asyncio
async def test_watch_topic_reconnect_uses_relogin_when_available() -> None:
    """CORRECTED (this session, prompted by a real field report: an
    integration stuck permanently reconnecting-but-never-succeeding),
    then NARROWED (self-review, same session): reconnect() alone is
    same-token by design -- if a disconnect lands after the token has
    already expired, blindly reusing it would retry forever without
    ever being able to succeed. But relogging in on EVERY reconnect
    (even for an ordinary transient blip with a still-valid token)
    would trade a fast MQTT-only reconnect for a full auth round-trip
    unconditionally -- so a relogin only happens when the token is
    actually at/near expiry (seconds_until_token_refresh_due() == 0.0,
    simulated here), not on every reconnect regardless of token
    freshness."""
    from roombapy_prime.auth import CloudCredentials, ConnectionToken, LoginResult
    from roombapy_prime.prime_robot import PrimeRobot

    mqtt = MagicMock()
    rest = MagicMock()
    mqtt.seconds_until_token_refresh_due.return_value = 0.0  # token due for refresh

    fresh_token = ConnectionToken(
        client_id="c2", iot_token="fresh-token", iot_signature="s2",
        iot_authorizer_name="a", expires=999999, devices=["BLID123"],
    )
    fresh_login_result = LoginResult(
        mqtt_endpoint="mqtt.example.invalid", http_base="https://h.invalid",
        http_base_auth="https://ha.invalid",
        credentials=CloudCredentials(access_key_id="ak", secret_key="sk", session_token="st", cognito_id="c"),
        robots={"BLID123": {}}, connection_tokens=[fresh_token], raw={},
        irbt_topic_prefix="irbt-fake-prefix",
    )
    relogin = AsyncMock(return_value=fresh_login_result)

    robot = PrimeRobot(
        blid="BLID123", mqtt_client=mqtt, rest_client=rest,
        irbt_topic_prefix="irbt-fake-prefix", relogin=relogin,
    )

    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    disconnect_event = asyncio.Event()
    call_count = 0

    async def fake_wait_for_disconnect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await disconnect_event.wait()
            return "connection lost"
        await asyncio.Event().wait()

    mqtt.wait_for_disconnect = AsyncMock(side_effect=fake_wait_for_disconnect)
    mqtt.replace_token = MagicMock()
    mqtt.reconnect = MagicMock()

    agen = robot.watch_state()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    disconnect_event.set()
    await _wait_until(lambda: mqtt.replace_token.called)

    relogin.assert_awaited_once()
    mqtt.replace_token.assert_called_once()
    passed_token = mqtt.replace_token.call_args.args[0]
    assert passed_token.iot_token == "fresh-token"
    # The plain, same-token path must NOT have been used when relogin
    # was available -- that's the entire point of this fix.
    mqtt.reconnect.assert_not_called()

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"after": "relogin_reconnect"}))
    result = await next_task
    assert result.payload == {"after": "relogin_reconnect"}

    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_topic_reconnect_skips_relogin_when_token_still_valid() -> None:
    """The other half of the narrowing above: an ordinary transient
    disconnect with a still-valid token must use the fast, same-token
    reconnect() -- NOT pay for a full relogin it doesn't need."""
    from unittest.mock import AsyncMock

    robot, mqtt, _rest = _robot_with_mocks()
    robot._relogin = AsyncMock()
    mqtt.seconds_until_token_refresh_due.return_value = 300.0  # plenty of time left

    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)
    disconnect_event = asyncio.Event()
    call_count = 0

    async def fake_wait_for_disconnect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await disconnect_event.wait()
            return "connection lost"
        await asyncio.Event().wait()

    mqtt.wait_for_disconnect = AsyncMock(side_effect=fake_wait_for_disconnect)
    mqtt.reconnect = MagicMock()

    agen = robot.watch_state()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    disconnect_event.set()
    await _wait_until(lambda: mqtt.reconnect.called)

    robot._relogin.assert_not_awaited()
    mqtt.replace_token.assert_not_called()

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"after": "reconnect"}))
    await next_task
    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_state_reconnects_after_disconnect() -> None:
    """A dropped connection must not end the generator or raise to the
    caller -- it triggers mqtt.reconnect() and resumes delivering deltas
    once reconnected, transparently to the `async for` consumer."""
    robot, mqtt, _rest = _robot_with_mocks()
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    disconnect_event = asyncio.Event()
    call_count = 0

    async def fake_wait_for_disconnect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await disconnect_event.wait()
            return "connection lost"
        await asyncio.Event().wait()  # subsequent calls: healthy again, never fires

    mqtt.wait_for_disconnect = AsyncMock(side_effect=fake_wait_for_disconnect)
    mqtt.reconnect = MagicMock()  # succeeds immediately, no side_effect

    agen = robot.watch_state()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    disconnect_event.set()
    await _wait_until(lambda: mqtt.reconnect.called)
    mqtt.reconnect.assert_called_once()

    # watch_state() re-subscribes with the SAME callback closure after a
    # successful reconnect (see reconnect()'s docstring on why it must be
    # the same closure, not a fresh subscribe() call) -- firing it again
    # simulates "reconnected, and a new delta has now arrived".
    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"after": "reconnect"}))
    result = await next_task

    assert result.payload == {"after": "reconnect"}

    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_state_retries_reconnect_with_backoff_on_failure() -> None:
    """If mqtt.reconnect() itself fails, watch_state() must retry rather
    than give up -- verified here with a fake that fails twice before
    succeeding. Real (short) backoff delays, not mocked -- patching
    asyncio.sleep globally risks starving other coroutines this same
    test relies on (the callback/subscribe simulation), for a saving of
    only ~3 real seconds (1s + 2s backoff)."""
    robot, mqtt, _rest = _robot_with_mocks()
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    disconnect_event = asyncio.Event()
    call_count = 0

    async def fake_wait_for_disconnect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await disconnect_event.wait()
            return "connection lost"
        await asyncio.Event().wait()

    mqtt.wait_for_disconnect = AsyncMock(side_effect=fake_wait_for_disconnect)
    mqtt.reconnect = MagicMock(side_effect=[RuntimeError("still down"), RuntimeError("still down"), None])

    agen = robot.watch_state()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    disconnect_event.set()
    await _wait_until(lambda: mqtt.reconnect.call_count == 3, timeout=5.0)

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"after": "retry"}))
    result = await next_task
    assert result.payload == {"after": "retry"}

    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_state_outer_cancellation_does_not_leak_tasks() -> None:
    """Regression test for a real bug found while building the reconnect
    logic above: if the generator itself is cancelled (agen.aclose()/
    task.cancel()) while BOTH the queue-get and disconnect-wait race
    tasks are still pending, both must be cleaned up -- not just
    whichever one would have "lost" a normal race. Passing here mainly
    means pytest-homeassistant-custom-component's lingering-task check
    doesn't fail after this test.
    """
    robot, mqtt, _rest = _robot_with_mocks()
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_state()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    next_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_task
    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_live_map_subscribes_to_fixed_topic() -> None:
    """Corrected design (11. Juli, see PRIME_APP_GAP_ANALYSIS): the topic
    is a fixed pattern via mqtt.livemap_topic(), NOT derived from
    get_live_map_stream()'s REST response."""
    robot, mqtt, rest = _robot_with_mocks()
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)
    mqtt.livemap_topic.return_value = "irbt-fake-prefix/BLID123/livemap/update"

    async def fake_get_live_map_stream(blid: str):
        from roombapy_prime.models import LiveMapStreamInit
        return LiveMapStreamInit(mqtt_topic="unused-should-not-matter")

    rest.get_live_map_stream = fake_get_live_map_stream

    agen = robot.watch_live_map(keep_alive_interval=999.0)
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "topic" in captured)

    mqtt.livemap_topic.assert_called_once_with("irbt-fake-prefix")
    assert captured["topic"] == "irbt-fake-prefix/BLID123/livemap/update"

    captured["callback"](ShadowResponse(
        topic=captured["topic"],
        payload={"pos_update": {"cur_path": [1, 0.0, 0.0, 0.0, 0, 1783704212]}},
    ))
    result = await next_task

    assert isinstance(result, PositionUpdateMessage)
    assert result.updates[0].point == (0.0, 0.0)

    await agen.aclose()
    mqtt.unsubscribe.assert_called_once_with(captured["topic"], captured["callback"])


@pytest.mark.asyncio
async def test_watch_live_map_without_topic_prefix_raises_immediately() -> None:
    mqtt = MagicMock()
    rest = MagicMock()
    robot = PrimeRobot(blid="BLID123", mqtt_client=mqtt, rest_client=rest, irbt_topic_prefix=None)

    agen = robot.watch_live_map()
    with pytest.raises(RuntimeError, match="irbt_topic_prefix"):
        await agen.__anext__()

    mqtt.subscribe.assert_not_called()


@pytest.mark.asyncio
async def test_watch_live_map_sends_periodic_keep_alive() -> None:
    robot, mqtt, rest = _robot_with_mocks()
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)
    mqtt.livemap_topic.return_value = "irbt-fake-prefix/BLID123/livemap/update"

    keep_alive_calls = []

    async def fake_get_live_map_stream(blid: str):
        keep_alive_calls.append(blid)
        from roombapy_prime.models import LiveMapStreamInit
        return LiveMapStreamInit(mqtt_topic="ignored")

    rest.get_live_map_stream = fake_get_live_map_stream

    agen = robot.watch_live_map(keep_alive_interval=0.05)
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "topic" in captured)

    # let a couple of keep-alive intervals elapse
    await asyncio.sleep(0.2)

    next_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_task
    await agen.aclose()

    assert len(keep_alive_calls) >= 2


@pytest.mark.asyncio
async def test_watch_live_map_yields_map_update_message() -> None:
    robot, mqtt, rest = _robot_with_mocks()
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)
    mqtt.livemap_topic.return_value = "irbt-fake-prefix/BLID123/livemap/update"

    agen = robot.watch_live_map(keep_alive_interval=999.0)
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "topic" in captured)

    captured["callback"](ShadowResponse(
        topic=captured["topic"],
        payload={"map_update": {"livemap_url": "https://example.invalid/m.png"}},
    ))
    result = await next_task

    assert isinstance(result, MapUpdateMessage)
    assert result.livemap_url == "https://example.invalid/m.png"

    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_live_map_propagates_unrecognized_shape_as_error() -> None:
    """SYNTHETIC -- confirms an unrecognized message shape on a never
    live-tested channel surfaces loudly to the caller instead of being
    silently dropped (see watch_live_map()'s docstring)."""
    robot, mqtt, rest = _robot_with_mocks()
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)
    mqtt.livemap_topic.return_value = "irbt-fake-prefix/BLID123/livemap/update"

    agen = robot.watch_live_map(keep_alive_interval=999.0)
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "topic" in captured)

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"something_else": True}))

    with pytest.raises(ValueError, match="Unrecognized"):
        await next_task

    await agen.aclose()


# --- proactive token refresh ---------------------------------------------

@pytest.mark.asyncio
async def test_connect_without_relogin_never_starts_refresh_task() -> None:
    """Existing behaviour, unchanged: no relogin passed -> no task."""
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.connect = MagicMock()

    await robot.connect()

    assert robot._refresh_task is None


@pytest.mark.asyncio
async def test_connect_with_relogin_starts_refresh_task_disconnect_stops_it() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.connect = MagicMock()
    mqtt.disconnect = MagicMock()
    mqtt.seconds_until_token_refresh_due.return_value = None  # never actually fires

    async def fake_relogin():
        raise AssertionError("should never be called -- refresh never due in this test")

    robot._relogin = fake_relogin

    await robot.connect()
    task = robot._refresh_task
    assert task is not None

    await robot.disconnect()

    assert robot._refresh_task is None
    assert task.done()
    # the loop may have finished naturally (seconds_until_token_refresh_due
    # returned None right away) or been cancelled by disconnect() -- both
    # are fine; what matters is it didn't raise anything else
    if not task.cancelled():
        assert task.exception() is None


@pytest.mark.asyncio
async def test_refresh_loop_relogins_and_replaces_token_then_stops() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    # first check: refresh due immediately (0s wait); second check: no
    # longer schedulable -- loop must do exactly one refresh, then return.
    mqtt.seconds_until_token_refresh_due.side_effect = [0.0, None]

    relogin_call_count = 0

    async def fake_relogin():
        nonlocal relogin_call_count
        relogin_call_count += 1
        login_result = MagicMock()
        login_result.token_for_blid.return_value = "new-token-sentinel"
        return login_result

    robot._relogin = fake_relogin

    await robot._refresh_loop()

    assert relogin_call_count == 1
    mqtt.replace_token.assert_called_once_with("new-token-sentinel")


@pytest.mark.asyncio
async def test_refresh_loop_never_refreshes_when_expiry_unknown() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.seconds_until_token_refresh_due.return_value = None

    async def fake_relogin():
        raise AssertionError("must not be called when expiry is unknown")

    robot._relogin = fake_relogin

    await robot._refresh_loop()  # must return immediately, not hang or call relogin

    mqtt.replace_token.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_loop_retries_after_a_failed_refresh_instead_of_dying() -> None:
    """HARDENED (this session, prompted by a real field report: an
    integration stuck permanently reconnecting, surviving even
    multiple full application restarts). Previously, ANY exception
    from relogin()/replace_token() here would propagate out of this
    fire-and-forget background task and kill it silently -- no further
    proactive refresh would EVER happen again for this PrimeRobot's
    lifetime, no log, nothing. This test asserts a failed attempt is
    retried, not fatal to the loop."""
    import roombapy_prime.prime_robot as prime_robot_module

    robot, mqtt, _rest = _robot_with_mocks()
    # First check: refresh due immediately. Second check (after the
    # retry sleep): also due immediately, so the retried attempt fires
    # right away too. Third check: no longer schedulable -- loop must
    # stop after exactly one failure + one successful retry.
    mqtt.seconds_until_token_refresh_due.side_effect = [0.0, 0.0, None]

    relogin_call_count = 0

    async def fake_relogin():
        nonlocal relogin_call_count
        relogin_call_count += 1
        if relogin_call_count == 1:
            raise ConnectionError("transient network blip")
        login_result = MagicMock()
        login_result.token_for_blid.return_value = "new-token-sentinel"
        return login_result

    robot._relogin = fake_relogin
    robot._REFRESH_RETRY_SECONDS = 0.0  # don't actually slow the test down

    sleep_calls: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)  # yield control without really waiting

    with patch.object(prime_robot_module.asyncio, "sleep", fake_sleep):
        await robot._refresh_loop()

    assert relogin_call_count == 2, "the loop must retry after the first failure, not give up"
    mqtt.replace_token.assert_called_once_with("new-token-sentinel")


# --- backpressure ---------------------------------------------------------

@pytest.mark.asyncio
async def test_put_with_backpressure_drops_oldest_when_full(caplog) -> None:
    """Unit-level test of the backpressure helper itself, rather than
    driving it through the full generator+thread-bridge machinery --
    that path already has flaky timing (asyncio.Queue's internal
    waiter wakeup isn't synchronous with call_soon_threadsafe, making
    "push 3 before consuming any" hard to assert deterministically
    through the public watch_*() interface). The generator's use of
    this helper is already exercised by the "delivers every message"
    tests above; this isolates the drop-oldest + logging behaviour."""
    import logging

    from roombapy_prime.prime_robot import _put_with_backpressure

    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    queue.put_nowait("a")
    queue.put_nowait("b")

    with caplog.at_level(logging.WARNING):
        _put_with_backpressure(queue, "c", "some/topic")

    assert queue.qsize() == 2
    assert queue.get_nowait() == "b"  # "a" (oldest) was dropped
    assert queue.get_nowait() == "c"
    assert "full" in caplog.text
    assert "some/topic" in caplog.text


@pytest.mark.asyncio
async def test_put_with_backpressure_no_drop_when_not_full() -> None:
    from roombapy_prime.prime_robot import _put_with_backpressure

    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    queue.put_nowait("a")

    _put_with_backpressure(queue, "b", "some/topic")

    assert queue.qsize() == 2
    assert queue.get_nowait() == "a"
    assert queue.get_nowait() == "b"


@pytest.mark.asyncio
async def test_put_with_backpressure_dropping_an_exception_logs_error_level(caplog) -> None:
    """NEU -- verlorener Fehler wird als ERROR geloggt, nicht WARNING,
    damit er nicht in der Masse gewoehnlicher Drops untergeht (siehe
    _put_with_backpressure()'s Docstring)."""
    import logging

    from roombapy_prime.prime_robot import _put_with_backpressure

    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(ValueError("boom"))

    with caplog.at_level(logging.WARNING):
        _put_with_backpressure(queue, "normal message", "some/topic")

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_records) == 1
    assert "an ERROR was dropped" in error_records[0].message
    assert queue.get_nowait() == "normal message"


# =========================================================================
# Systematic review finding (thirteenth session): almost all thin
# REST passthrough wrappers on PrimeRobot had no test at all (coverage
# report showed 81% for prime_robot.py, almost entirely unused wrapper
# lines). Table-driven instead of 20 individual tests -- create_autospec
# also automatically checks that the call signatures match the real
# PrimeRestClient class (would have caught the earlier missing-wrapper
# finding even before any manual review).
# =========================================================================


def _robot_with_autospec_rest() -> tuple[PrimeRobot, MagicMock]:
    mqtt = MagicMock()
    rest = create_autospec(PrimeRestClient, instance=True)
    robot = PrimeRobot(
        blid="BLID123", mqtt_client=mqtt, rest_client=rest,
        irbt_topic_prefix="irbt-fake-prefix",
    )
    return robot, rest


@pytest.mark.asyncio
async def test_get_active_map_versions_delegates() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.get_active_map_versions.return_value = [{"id": "map1"}]

    result = await robot.get_active_map_versions()

    rest.get_active_map_versions.assert_awaited_once_with("BLID123")
    assert result == [{"id": "map1"}]


@pytest.mark.asyncio
async def test_set_map_name_delegates() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.set_map_name.return_value = {"ok": True}

    result = await robot.set_map_name("map1", "Ground Floor")

    rest.set_map_name.assert_awaited_once_with("map1", "Ground Floor")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_delete_map_delegates() -> None:
    """NEW (thirteenth session) -- the wrapper itself was the review finding."""
    robot, rest = _robot_with_autospec_rest()
    rest.delete_map.return_value = {"deleted": True}

    result = await robot.delete_map("map1")

    rest.delete_map.assert_awaited_once_with("map1")
    assert result == {"deleted": True}


@pytest.mark.asyncio
async def test_get_map_geojson_link_delegates() -> None:
    """NEW (thirteenth session)."""
    robot, rest = _robot_with_autospec_rest()
    rest.get_map_geojson_link.return_value = {"link": "https://example.invalid/x"}

    result = await robot.get_map_geojson_link("map1", "3")

    rest.get_map_geojson_link.assert_awaited_once_with("map1", "3")
    assert result == {"link": "https://example.invalid/x"}


@pytest.mark.asyncio
async def test_download_map_bundle_delegates() -> None:
    """NEW (thirteenth session)."""
    robot, rest = _robot_with_autospec_rest()
    rest.download_map_bundle.return_value = b"fake-bytes"

    result = await robot.download_map_bundle("https://example.invalid/bundle.tar.gz")

    rest.download_map_bundle.assert_awaited_once_with("https://example.invalid/bundle.tar.gz")
    assert result == b"fake-bytes"


@pytest.mark.asyncio
async def test_edit_map_and_edit_map_v2_delegate() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.edit_map.return_value = {"v1": True}
    rest.edit_map_v2.return_value = {"v2": True}
    command = MagicMock()

    assert await robot.edit_map("map1", command) == {"v1": True}
    rest.edit_map.assert_awaited_once_with("map1", command)

    assert await robot.edit_map_v2("map1", command) == {"v2": True}
    rest.edit_map_v2.assert_awaited_once_with("map1", command)


@pytest.mark.asyncio
async def test_favorites_crud_delegate() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.get_favorites.return_value = []
    rest.create_favorite.return_value = {"created": True}
    rest.update_favorite.return_value = {"updated": True}
    rest.delete_favorite.return_value = {"deleted": True}
    favorite = MagicMock()

    assert await robot.get_favorites() == []
    rest.get_favorites.assert_awaited_once_with()

    assert await robot.create_favorite(favorite) == {"created": True}
    rest.create_favorite.assert_awaited_once_with(favorite)

    assert await robot.update_favorite("fav1", favorite) == {"updated": True}
    rest.update_favorite.assert_awaited_once_with("fav1", favorite)

    assert await robot.delete_favorite("fav1") == {"deleted": True}
    rest.delete_favorite.assert_awaited_once_with("fav1")


@pytest.mark.asyncio
async def test_order_favorite_delegates_with_kwargs() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.order_favorite.return_value = {"ok": True}

    result = await robot.order_favorite("fav1", insert_before="fav0")

    rest.order_favorite.assert_awaited_once_with(
        "fav1", insert_at=None, insert_before="fav0", insert_after=None
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_get_mission_history_delegates_with_kwargs() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.get_mission_history.return_value = {"missions": []}

    result = await robot.get_mission_history("BLID123", max_reports=5)

    rest.get_mission_history.assert_awaited_once_with(
        "BLID123",
        max_reports=5,
        max_age=None,
        filter_type=None,
        exclusive_start_timestamp=None,
        supported_done_codes=None,
    )
    assert result == {"missions": []}


@pytest.mark.asyncio
async def test_schedules_crud_delegate() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.get_schedules.return_value = {"schedules": []}
    rest.create_schedules.return_value = {"created": True}
    rest.update_schedules.return_value = {"updated": True}
    rest.delete_schedule.return_value = {"deleted": True}

    assert await robot.get_schedules("hh1") == {"schedules": []}
    rest.get_schedules.assert_awaited_once_with("hh1")

    assert await robot.create_schedules("hh1", []) == {"created": True}
    rest.create_schedules.assert_awaited_once_with("hh1", [])

    assert await robot.update_schedules("hh1", "sched1", []) == {"updated": True}
    rest.update_schedules.assert_awaited_once_with("hh1", "sched1", [])

    assert await robot.delete_schedule("hh1", "sched1") == {"deleted": True}
    rest.delete_schedule.assert_awaited_once_with("hh1", "sched1")


@pytest.mark.asyncio
async def test_household_and_settings_delegate() -> None:
    robot, rest = _robot_with_autospec_rest()
    rest.get_user_households.return_value = {"households": []}
    rest.get_dnd_settings.return_value = {"enabled": False}
    rest.set_dnd_settings.return_value = {"ok": True}
    rest.get_cleaning_profiles.return_value = {"profiles": []}
    rest.get_default_routines.return_value = {"routines": []}

    assert await robot.get_user_households() == {"households": []}
    rest.get_user_households.assert_awaited_once_with()

    assert await robot.get_dnd_settings("hh1") == {"enabled": False}
    rest.get_dnd_settings.assert_awaited_once_with("hh1")

    assert await robot.set_dnd_settings("hh1", {"enabled": True}) == {"ok": True}
    rest.set_dnd_settings.assert_awaited_once_with("hh1", {"enabled": True})

    assert await robot.get_cleaning_profiles("BLID123", "map1") == {"profiles": []}
    rest.get_cleaning_profiles.assert_awaited_once_with("BLID123", "map1")

    assert await robot.get_default_routines("map1") == {"routines": []}
    rest.get_default_routines.assert_awaited_once_with("map1")


@pytest.mark.asyncio
async def test_robot_parts_and_serial_number_delegate() -> None:
    """NEW (session 15) -- confirmed from base_roomba_config.json."""
    robot, rest = _robot_with_autospec_rest()
    rest.get_robot_parts.return_value = {"parts": []}
    rest.reset_robot_parts.return_value = {"ok": True}
    rest.get_serial_number_data.return_value = {"serial": "abc123"}

    assert await robot.get_robot_parts() == {"parts": []}
    rest.get_robot_parts.assert_awaited_once_with("BLID123")

    assert await robot.reset_robot_parts() == {"ok": True}
    rest.reset_robot_parts.assert_awaited_once_with("BLID123")

    assert await robot.get_serial_number_data() == {"serial": "abc123"}
    rest.get_serial_number_data.assert_awaited_once_with("BLID123")


@pytest.mark.asyncio
async def test_echo_time_estimates_reset_notifications_delegate() -> None:
    """NEW (session 16) -- confirmed from base_roomba_config.json."""
    robot, rest = _robot_with_autospec_rest()
    rest.poll_echo_value.return_value = {"ok": True}
    rest.get_time_estimates.return_value = {"minutes": 30}
    rest.reset_robot.return_value = {"reset": True}
    rest.get_notifications.return_value = {"events": []}

    assert await robot.poll_echo_value() == {"ok": True}
    rest.poll_echo_value.assert_awaited_once_with("BLID123")

    assert await robot.get_time_estimates({"assetId": "BLID123"}) == {"minutes": 30}
    rest.get_time_estimates.assert_awaited_once_with({"assetId": "BLID123"})

    assert await robot.reset_robot() == {"reset": True}
    rest.reset_robot.assert_awaited_once_with("BLID123")

    assert await robot.get_notifications() == {"events": []}
    rest.get_notifications.assert_awaited_once_with("BLID123", "2.2.4")


# =========================================================================
# watch_mission_timeline() (this session) -- EXPLORATORY, see its own
# docstring for exact confidence level. Mirrors watch_state()'s own test
# structure, since both now share _watch_topic() as their common core.
# =========================================================================


@pytest.mark.asyncio
async def test_watch_mission_timeline_subscribes_to_correct_topic() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    robot._irbt_topic_prefix = "irbt-prefix"
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_mission_timeline()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    mqtt.mission_timeline_topic.assert_called_once_with("irbt-prefix", report=True)

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"phase": "run"}))
    result = await next_task

    assert result.payload == {"phase": "run"}

    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_mission_timeline_without_irbt_prefix_raises() -> None:
    """Same guard as send_simple_command()/watch_live_map() -- this
    topic is built from irbt_topic_prefix, which isn't always
    available (see LoginResult's own docstring)."""
    robot, _mqtt, _rest = _robot_with_mocks()
    robot._irbt_topic_prefix = None

    with pytest.raises(ValueError, match="irbt_topic_prefix"):
        await robot.watch_mission_timeline().__anext__()


@pytest.mark.asyncio
async def test_watch_mission_timeline_reconnects_after_disconnect() -> None:
    """Confirms the shared _watch_topic() reconnect-hardening (see
    watch_state()'s own equivalent test) also applies to this newer
    method, not just the original one it was extracted from."""
    robot, mqtt, _rest = _robot_with_mocks()
    robot._irbt_topic_prefix = "irbt-prefix"
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    disconnect_event = asyncio.Event()
    call_count = 0

    async def fake_wait_for_disconnect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await disconnect_event.wait()
            return "connection lost"
        await asyncio.Event().wait()

    mqtt.wait_for_disconnect = AsyncMock(side_effect=fake_wait_for_disconnect)
    mqtt.reconnect = MagicMock()

    agen = robot.watch_mission_timeline()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    disconnect_event.set()
    await _wait_until(lambda: mqtt.reconnect.called)
    mqtt.reconnect.assert_called_once()

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"phase": "run"}))
    result = await next_task

    assert result.payload == {"phase": "run"}

    await agen.aclose()


# =========================================================================
# watch_rejected_commands() (this session) -- same confidence level and
# reasoning as watch_mission_timeline(), see its own docstring. Directly
# complements the already-live-confirmed send_simple_command().
# =========================================================================


@pytest.mark.asyncio
async def test_watch_rejected_commands_subscribes_to_correct_topic() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    robot._irbt_topic_prefix = "irbt-prefix"
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_rejected_commands()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    mqtt.rejected_report_topic.assert_called_once_with("irbt-prefix")

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"reason": "busy"}))
    result = await next_task

    assert result.payload == {"reason": "busy"}

    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_rejected_commands_without_irbt_prefix_raises() -> None:
    robot, _mqtt, _rest = _robot_with_mocks()
    robot._irbt_topic_prefix = None

    with pytest.raises(ValueError, match="irbt_topic_prefix"):
        await robot.watch_rejected_commands().__anext__()


@pytest.mark.asyncio
async def test_watch_raw_topic_subscribes_to_exact_given_topic() -> None:
    """NEW (this session) -- unlike watch_state()/watch_mission_timeline(),
    this method builds no topic itself -- confirms it subscribes to
    exactly what the caller passes, unmodified."""
    robot, mqtt, _rest = _robot_with_mocks()
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_raw_topic("irbt-prefix/things/BLID123/#")
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    assert captured["topic"] == "irbt-prefix/things/BLID123/#"

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"anything": True}))
    result = await next_task
    assert result.payload == {"anything": True}

    await agen.aclose()
    mqtt.unsubscribe.assert_called_once_with(captured["topic"], captured["callback"])


@pytest.mark.asyncio
async def test_watch_named_shadows_updates_subscribes_to_plus_wildcard() -> None:
    """CONFIRMED SAFE (this session): a single-level ("+") wildcard on
    the shadow-name segment of update/accepted, distinct from the
    multi-level ("#") wildcard already removed elsewhere
    (--watch-aws-tree) after a real connection disruption. Each
    yielded response's own .topic reveals which named shadow it came
    from -- a wildcard subscription resolves to the real topic in the
    actual message, not the wildcard pattern itself."""
    robot, mqtt, _rest = _robot_with_mocks()
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_named_shadows_updates()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    assert captured["topic"] == "$aws/things/BLID123/shadow/name/+/update/accepted"

    resolved_topic = "$aws/things/BLID123/shadow/name/ro-currentstate/update/accepted"
    captured["callback"](ShadowResponse(topic=resolved_topic, payload={"state": {"batPct": 72}}))
    result = await next_task
    assert result.topic == resolved_topic
    assert result.payload == {"state": {"batPct": 72}}

    await agen.aclose()


@pytest.mark.asyncio
async def test_watch_state_aclose_propagates_to_inner_watch_topic_generator() -> None:
    """Regression test for a real bug found this session, while
    extracting _watch_topic() as shared code behind watch_state() AND
    watch_mission_timeline(): a bare `async for x in inner_gen(): yield
    x` does NOT guarantee inner_gen's .aclose() runs when the OUTER
    generator (watch_state() here) is closed -- unsubscribe() in
    _watch_topic()'s own `finally` block silently never fired on
    agen.aclose(), only on natural exhaustion. Fixed via
    contextlib.aclosing() wrapping the inner generator; this test
    exists so a future refactor can't quietly reintroduce the same gap."""
    robot, mqtt, _rest = _robot_with_mocks()
    _never_disconnects(mqtt)
    captured: dict = {}
    mqtt.subscribe.side_effect = lambda topic, cb: captured.update(topic=topic, callback=cb)

    agen = robot.watch_state()
    next_task = asyncio.ensure_future(agen.__anext__())
    await _wait_until(lambda: "callback" in captured)

    captured["callback"](ShadowResponse(topic=captured["topic"], payload={"n": 1}))
    await next_task

    await agen.aclose()

    mqtt.unsubscribe.assert_called_once_with(captured["topic"], captured["callback"])
