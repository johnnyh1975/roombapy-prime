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
from unittest.mock import MagicMock, create_autospec

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
async def test_set_setting_writes_named_shadow() -> None:
    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.update_shadow.return_value = ShadowResponse(topic="t", payload={})

    await robot.set_setting("binPause", True)

    mqtt.update_shadow.assert_called_once_with({"binPause": True}, "rw-settings", 8.0)


@pytest.mark.asyncio
async def test_send_mission_command_uses_classic_shadow() -> None:
    """BESTAETIGT (15. Sitzung) -- siehe models.py's Missionssteuerungs-
    Abschnitt und send_mission_command()'s Docstring. Definitiv bestaetigt
    durch die tatsaechliche APK-Konfigurationsdatei
    (res/raw/base_roomba_config.json): commandId "Control"/
    "AssetControlCommand" hat namedShadow="" -- klassischer Shadow, nicht
    "rw-settings" (das ist fuer Settings-Kommandos reserviert, ebenfalls
    in derselben Datei bestaetigt)."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    robot, mqtt, _rest = _robot_with_mocks()
    mqtt.update_shadow.return_value = ShadowResponse(topic="t", payload={})

    cmd = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID123")
    await robot.send_mission_command(cmd)

    mqtt.update_shadow.assert_called_once_with({"cmd": cmd.to_json()}, None, 8.0)


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
    assert "voll" in caplog.text
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
    assert "FEHLER" in error_records[0].message
    assert queue.get_nowait() == "normal message"


# =========================================================================
# Systematischer Review-Fund (dreizehnte Sitzung): fast alle duennen
# REST-Passthrough-Wrapper auf PrimeRobot hatten ueberhaupt keinen Test
# (Coverage-Report zeigte 81% fuer prime_robot.py, fast ausschliesslich
# unbenutzte Wrapper-Zeilen). Tabellengetrieben statt 20 Einzeltests --
# create_autospec prueft dabei automatisch, dass die Aufrufsignaturen
# zur echten PrimeRestClient-Klasse passen (haette z.B. den vorherigen
# fehlenden-Wrapper-Fund noch vor jedem manuellen Review aufgedeckt).
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
    """NEU (dreizehnte Sitzung) -- der Wrapper selbst war der Review-Fund."""
    robot, rest = _robot_with_autospec_rest()
    rest.delete_map.return_value = {"deleted": True}

    result = await robot.delete_map("map1")

    rest.delete_map.assert_awaited_once_with("map1")
    assert result == {"deleted": True}


@pytest.mark.asyncio
async def test_get_map_geojson_link_delegates() -> None:
    """NEU (dreizehnte Sitzung)."""
    robot, rest = _robot_with_autospec_rest()
    rest.get_map_geojson_link.return_value = {"link": "https://example.invalid/x"}

    result = await robot.get_map_geojson_link("map1", "3")

    rest.get_map_geojson_link.assert_awaited_once_with("map1", "3")
    assert result == {"link": "https://example.invalid/x"}


@pytest.mark.asyncio
async def test_download_map_bundle_delegates() -> None:
    """NEU (dreizehnte Sitzung)."""
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
    """NEU (15. Sitzung) -- bestaetigt aus base_roomba_config.json."""
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
    """NEU (16. Sitzung) -- bestaetigt aus base_roomba_config.json."""
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
    rest.get_notifications.assert_awaited_once_with("BLID123", "1.0")
