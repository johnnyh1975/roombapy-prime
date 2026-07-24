"""Tests for the testable parts of verify_region_commands.py --
_is_safe_command_def()'s TID-detection logic and _region_types()'s
tolerance for both typed Region objects and raw dicts. The actual
purpose of the script (sending a real region command to a real
device) is by nature not automatable to test -- that's the whole
point of the staged-risk approach described in its own module
docstring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from roombapy_prime.models.mission_control import Region, RegionType
from roombapy_prime.verify_region_commands import _is_safe_command_def, _region_types


def test_is_safe_command_def_true_for_rid_zid_only():
    command = MagicMock()
    command.regions = [
        Region(region_id="1", region_type=RegionType.RID),
        Region(region_id="2", region_type=RegionType.ZID),
    ]
    assert _is_safe_command_def(command) is True


def test_is_safe_command_def_false_when_any_region_is_tid():
    """The core safety property this whole script exists to enforce:
    a single TID region anywhere in the command_def disqualifies it
    from stage 1 entirely, even if every other region is safe."""
    command = MagicMock()
    command.regions = [
        Region(region_id="1", region_type=RegionType.RID),
        Region(region_id="160", region_type=RegionType.TID),
    ]
    assert _is_safe_command_def(command) is False


def test_is_safe_command_def_true_when_no_regions_at_all():
    command = MagicMock()
    command.regions = None
    assert _is_safe_command_def(command) is True


def test_region_types_tolerates_raw_dicts_not_just_typed_objects():
    """command_defs read from a real account could contain either
    typed Region objects or raw dicts -- see RoutineCommand's own
    docstring on why both are accepted throughout this library."""
    regions = [{"type": "tid", "region_id": "160"}]
    assert _region_types(regions) == ["tid"]


def test_parse_polygon_points_valid_input():
    from roombapy_prime.verify_region_commands import _parse_polygon_points

    points = _parse_polygon_points("1.0,2.0 3.5,4.5")
    assert points == [(1.0, 2.0), (3.5, 4.5)]


def test_parse_polygon_points_malformed_returns_none_not_exception():
    """Stage 4's own CLI must fail with a clean, user-facing message,
    not a raw traceback, on malformed --polygon-points input."""
    from roombapy_prime.verify_region_commands import _parse_polygon_points

    assert _parse_polygon_points("not-a-valid-point") is None


def test_build_modified_command_actually_executes_without_crashing():
    """Directly exercises _build_modified_command() end-to-end against
    a real RoutineCommand/CommandParams instance -- this is the test
    that would have caught a real bug found in this exact function: an
    earlier version tried setting routine_modified directly on
    RoutineCommand (dataclasses.replace(original, routine_modified=True)),
    which raises TypeError at runtime since that field lives on
    CommandParams, not RoutineCommand. A syntax check alone does not
    catch this -- only an actual call does, which is why this test
    exists."""
    from roombapy_prime.models.mission_control import CommandParams, MissionCommandType, RoutineCommand
    from roombapy_prime.verify_region_commands import _build_modified_command

    original = RoutineCommand(
        command_type=MissionCommandType.CLEAN,
        asset_id="BLID123",
        regions=[Region(region_id="1", region_type=RegionType.RID)],
        params=CommandParams(suction_level=1),
    )

    modified, original_level = _build_modified_command(original, suction_level=3)

    assert original_level == 1
    assert modified.params.suction_level == 3
    assert modified.params.routine_modified is True
    # regions must be untouched -- stage 2 only changes params, nothing else.
    assert modified.regions == original.regions


def test_build_modified_command_handles_original_with_no_params_at_all():
    """The original command_def might have no top-level params object
    at all -- must build a fresh one, not crash on None.params."""
    from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
    from roombapy_prime.verify_region_commands import _build_modified_command

    original = RoutineCommand(command_type=MissionCommandType.CLEAN, asset_id="BLID123", params=None)

    modified, original_level = _build_modified_command(original, suction_level=2)

    assert original_level is None
    assert modified.params.suction_level == 2
    assert modified.params.routine_modified is True


def test_build_modified_command_handles_real_favorite_raw_dict_params():
    """REAL CRASH FOUND AND FIXED (jayjay, real device test): favorites
    are ALWAYS constructed with command_defs[].params kept as a RAW
    DICT (rest_client.py's own _favorite_from_json() does
    `params=c.get("params")` directly, by design) -- never a
    CommandParams instance the way the OTHER test above unrealistically
    assumes. This is the shape stage 2 will encounter against every
    real favorite, and the exact one that raised
    "TypeError: replace() should be called on dataclass instances" in
    the field."""
    from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
    from roombapy_prime.verify_region_commands import _build_modified_command

    original = RoutineCommand(
        command_type=MissionCommandType.START,
        asset_id="BLID123",
        regions=[{"region_id": "100", "type": "zid", "params": {"suctionLevel": 2}}],
        params={"profile": "light"},  # the real, raw-dict shape -- not CommandParams(...)
    )

    modified, original_level = _build_modified_command(original, suction_level=3)

    assert original_level is None  # "profile" dict has no suctionLevel key to begin with
    assert modified.params == {"profile": "light", "suctionLevel": 3, "routineModified": True}
    # regions must be untouched -- stage 2 only changes the top-level params.
    assert modified.regions == original.regions


def test_add_initiator_if_missing_adds_rmt_app_when_unset():
    """CONFIRMED FINDING (chairstacker, real device test): stage 1's
    own real favorite had initiator=None, meaning RoutineCommand.to_json()
    omitted the field entirely -- the original hypothesis behind this
    transport expected "initiator" to be a shared key, but the actual
    live test accidentally exercised a version without it."""
    from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
    from roombapy_prime.verify_region_commands import _add_initiator_if_missing

    original = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID", initiator=None)

    result = _add_initiator_if_missing(original)

    assert result is not None
    assert result.initiator == "rmtApp"
    assert result.to_json()["initiator"] == "rmtApp"
    # everything else must be untouched.
    assert result.command_type == original.command_type
    assert result.asset_id == original.asset_id


def test_add_initiator_if_missing_returns_none_when_already_set():
    """A command_def that already has an initiator has nothing for
    stage 1b to add -- callers should redirect to plain --send."""
    from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
    from roombapy_prime.verify_region_commands import _add_initiator_if_missing

    original = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID", initiator="cloud")

    result = _add_initiator_if_missing(original)

    assert result is None


class TestSummarizeEvents:
    """NEW (this session) -- _summarize_events(), built specifically so
    a human doesn't have to parse raw MissionTimelineEvent reprs by eye
    to judge whether region-targeting worked. Reports facts (what
    fields were present), not a verdict -- see its own docstring."""

    def test_empty_list_notes_no_events_and_references_the_known_negative_result(self):
        from roombapy_prime.verify_region_commands import _summarize_events

        result = _summarize_events([])

        assert "NO events" in result
        assert "chairstacker" in result

    def test_extracts_command_event_fields(self):
        from roombapy_prime.verify_region_commands import _summarize_events

        event = MagicMock()
        event.event_type = "cmd"
        event.command = MagicMock(command="start", initiator="rmtApp")
        event.room = None
        event.zone = None
        event.error = None

        result = _summarize_events([event])

        assert "command='start'" in result
        assert "initiator='rmtApp'" in result

    def test_extracts_room_event_fields_using_the_real_field_name_region_id(self):
        """REAL FIELD NAME CHECK: RoomEvent's actual attribute is
        region_id, not room_id -- confirmed against
        models/mission_history.py directly (session correction) rather
        than assumed."""
        from roombapy_prime.verify_region_commands import _summarize_events

        event = MagicMock()
        event.event_type = "room"
        event.command = None
        event.room = MagicMock(region_id="101", area=354, total_area=0)
        event.zone = None
        event.error = None

        result = _summarize_events([event])

        assert "region_id='101'" in result
        assert "area=354" in result
        assert "total_area=0" in result

    def test_extracts_zone_event_fields(self):
        from roombapy_prime.verify_region_commands import _summarize_events

        event = MagicMock()
        event.event_type = "zone"
        event.command = None
        event.room = None
        event.zone = MagicMock(zone_id="100", area=200, total_area=150)
        event.error = None

        result = _summarize_events([event])

        assert "zone_id='100'" in result

    def test_flags_error_event_prominently(self):
        from roombapy_prime.verify_region_commands import _summarize_events

        event = MagicMock()
        event.event_type = "error"
        event.command = None
        event.room = None
        event.zone = None
        event.error = MagicMock(value=17)

        result = _summarize_events([event])

        assert "ERROR value=17" in result


class TestConfirmShowSendWatchDisconnectAfter:
    """NEW (this session) -- disconnect_after param, so
    verify_region_commands_session.py can keep one connection alive
    across stages 1/1b/2 instead of reconnecting for each. All four
    existing standalone stage functions rely on the default (True) and
    are unaffected -- this only tests the new parameter itself."""

    @pytest.mark.asyncio
    async def test_default_still_disconnects(self):
        from roombapy_prime.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        with patch("roombapy_prime.verify_region_commands._confirm", return_value=True):
            await _confirm_show_send_watch(robot, command, report, watch_seconds=0, description="test")

        robot.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_after_false_does_not_disconnect(self):
        from roombapy_prime.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        with patch("roombapy_prime.verify_region_commands._confirm", return_value=True):
            await _confirm_show_send_watch(
                robot, command, report, watch_seconds=0, description="test", disconnect_after=False,
            )

        robot.disconnect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_captured_events(self):
        from roombapy_prime.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        fake_event = MagicMock(event_type="cmd", command=None, room=None, zone=None, error=None)

        async def fake_watch():
            yield fake_event

        robot.watch_mission_timeline = fake_watch

        with patch("roombapy_prime.verify_region_commands._confirm", return_value=True):
            events = await _confirm_show_send_watch(
                robot, command, report, watch_seconds=1, description="test", disconnect_after=False,
            )

        assert events == [fake_event]


class TestStageTwoAndThreeNowIncludeInitiator:
    """REAL GAP FOUND AND FIXED (this session, jayjay13011's own field
    report showing all three stages' actual payloads side by side):
    stage 2 and stage 3 never added "initiator", always testing the
    same "no initiator" shape as stage 1 -- never actually exercising
    the initiator+command hypothesis stage 1b was built to test."""

    @pytest.mark.asyncio
    async def test_stage_two_payload_includes_rmt_app_initiator(self):
        from roombapy_prime.models.mission_control import MissionCommandType, Region, RegionType, RoutineCommand
        from roombapy_prime.verify_region_commands import send_stage_two

        original = RoutineCommand(
            command_type=MissionCommandType.START, asset_id="BLID", initiator=None,
            regions=[Region(region_id="1", region_type=RegionType.RID)],
        )
        favorite = MagicMock(favorite_id="fav1", name="Test", command_defs=[original])
        robot = AsyncMock()
        robot.get_favorites.return_value = [favorite]
        captured = {}

        async def fake_confirm_show_send_watch(robot_arg, command, report, watch_seconds, description):
            captured["command"] = command
            return []

        fake_session_cm = MagicMock()
        fake_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("roombapy_prime.verify_region_commands._login_and_connect", new=AsyncMock(return_value=robot)), \
             patch("roombapy_prime.verify_region_commands._confirm_show_send_watch", fake_confirm_show_send_watch), \
             patch("aiohttp.ClientSession", return_value=fake_session_cm):
            await send_stage_two("u", "p", "US", "BLID", "fav1", 0, suction_level=2, watch_seconds=0)

        assert captured["command"].initiator == "rmtApp"

    @pytest.mark.asyncio
    async def test_stage_three_payload_includes_rmt_app_initiator(self):
        from roombapy_prime.verify_region_commands import send_stage_three

        robot = AsyncMock()
        captured = {}

        async def fake_confirm_show_send_watch(robot_arg, command, report, watch_seconds, description):
            captured["command"] = command
            return []

        fake_session_cm = MagicMock()
        fake_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("roombapy_prime.verify_region_commands._login_and_connect", new=AsyncMock(return_value=robot)), \
             patch("roombapy_prime.verify_region_commands._confirm_show_send_watch", fake_confirm_show_send_watch), \
             patch("aiohttp.ClientSession", return_value=fake_session_cm):
            await send_stage_three(
                "u", "p", "US", "BLID", p2map_id="MAP1", room_id="2", region_type="rid", watch_seconds=0,
            )

        assert captured["command"].initiator == "rmtApp"
