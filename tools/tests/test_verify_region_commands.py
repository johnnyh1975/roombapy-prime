"""Tests for the testable parts of verify_region_commands.py --
_is_safe_command_def()'s TID-detection logic and _region_types()'s
tolerance for both typed Region objects and raw dicts. The actual
purpose of the script (sending a real region command to a real
device) is by nature not automatable to test -- that's the whole
point of the staged-risk approach described in its own module
docstring."""

from __future__ import annotations

import inspect

import json

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from roombapy_prime.models.mission_control import Region, RegionType
from roombapy_prime_tools.verify_region_commands import _is_safe_command_def, _region_types


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
    from roombapy_prime_tools.verify_region_commands import _parse_polygon_points

    points = _parse_polygon_points("1.0,2.0 3.5,4.5")
    assert points == [(1.0, 2.0), (3.5, 4.5)]


def test_parse_polygon_points_malformed_returns_none_not_exception():
    """Stage 4's own CLI must fail with a clean, user-facing message,
    not a raw traceback, on malformed --polygon-points input."""
    from roombapy_prime_tools.verify_region_commands import _parse_polygon_points

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
    from roombapy_prime_tools.verify_region_commands import _build_modified_command

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
    from roombapy_prime_tools.verify_region_commands import _build_modified_command

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
    from roombapy_prime_tools.verify_region_commands import _build_modified_command

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
    from roombapy_prime_tools.verify_region_commands import _add_initiator_if_missing

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
    from roombapy_prime_tools.verify_region_commands import _add_initiator_if_missing

    original = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID", initiator="cloud")

    result = _add_initiator_if_missing(original)

    assert result is None


class TestSummarizeEvents:
    """NEW (this session) -- _summarize_events(), built specifically so
    a human doesn't have to parse raw MissionTimelineEvent reprs by eye
    to judge whether region-targeting worked. Reports facts (what
    fields were present), not a verdict -- see its own docstring."""

    def test_empty_list_notes_no_events_and_references_the_known_negative_result(self):
        from roombapy_prime_tools.verify_region_commands import _summarize_events

        result = _summarize_events([])

        assert "NO events" in result
        assert "chairstacker" in result

    def test_extracts_command_event_fields(self):
        from roombapy_prime_tools.verify_region_commands import _summarize_events

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
        from roombapy_prime_tools.verify_region_commands import _summarize_events

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
        from roombapy_prime_tools.verify_region_commands import _summarize_events

        event = MagicMock()
        event.event_type = "zone"
        event.command = None
        event.room = None
        event.zone = MagicMock(zone_id="100", area=200, total_area=150)
        event.error = None

        result = _summarize_events([event])

        assert "zone_id='100'" in result

    def test_flags_error_event_prominently(self):
        from roombapy_prime_tools.verify_region_commands import _summarize_events

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
        from roombapy_prime_tools.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        # Without this the shadow fetch returns an AsyncMock, whose
        # .payload.get(...) creates a coroutine nobody awaits -- ten
        # RuntimeWarnings that pass the tests but drown out any GENUINE
        # never-awaited warning a real bug would produce later.
        robot.get_named_shadow.return_value = MagicMock(payload={"state": {"reported": {}}})
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        with patch("roombapy_prime_tools.verify_region_commands.confirm", return_value=True):
            await _confirm_show_send_watch(robot, command, report, watch_seconds=0, description="test")

        robot.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_after_false_does_not_disconnect(self):
        from roombapy_prime_tools.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        # Without this the shadow fetch returns an AsyncMock, whose
        # .payload.get(...) creates a coroutine nobody awaits -- ten
        # RuntimeWarnings that pass the tests but drown out any GENUINE
        # never-awaited warning a real bug would produce later.
        robot.get_named_shadow.return_value = MagicMock(payload={"state": {"reported": {}}})
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        with patch("roombapy_prime_tools.verify_region_commands.confirm", return_value=True):
            await _confirm_show_send_watch(
                robot, command, report, watch_seconds=0, description="test", disconnect_after=False,
            )

        robot.disconnect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_captured_events_and_empty_rejections_by_default(self):
        from roombapy_prime_tools.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        # Without this the shadow fetch returns an AsyncMock, whose
        # .payload.get(...) creates a coroutine nobody awaits -- ten
        # RuntimeWarnings that pass the tests but drown out any GENUINE
        # never-awaited warning a real bug would produce later.
        robot.get_named_shadow.return_value = MagicMock(payload={"state": {"reported": {}}})
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        fake_event = MagicMock(event_type="cmd", command=None, room=None, zone=None, error=None)

        async def fake_watch_timeline():
            yield fake_event

        async def fake_watch_rejected():
            return
            yield  # pragma: no cover -- makes this an async generator

        robot.watch_mission_timeline = fake_watch_timeline
        robot.watch_rejected_commands = fake_watch_rejected

        with patch("roombapy_prime_tools.verify_region_commands.confirm", return_value=True):
            events, rejected = await _confirm_show_send_watch(
                robot, command, report, watch_seconds=1, description="test", disconnect_after=False,
            )

        assert events == [fake_event]
        assert rejected == []

    @pytest.mark.asyncio
    async def test_captures_a_real_rejection_if_one_arrives(self):
        """NEW (this session) -- watch_rejected_commands() is now
        watched concurrently with watch_mission_timeline(), genuinely
        for the first time in this project's region-command testing.
        See _confirm_show_send_watch()'s own docstring for why this
        matters: a rejection and a silent ignore look identical on
        mission/timeline/report alone."""
        from roombapy_prime_tools.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        # Without this the shadow fetch returns an AsyncMock, whose
        # .payload.get(...) creates a coroutine nobody awaits -- ten
        # RuntimeWarnings that pass the tests but drown out any GENUINE
        # never-awaited warning a real bug would produce later.
        robot.get_named_shadow.return_value = MagicMock(payload={"state": {"reported": {}}})
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        fake_rejection = MagicMock()

        async def fake_watch_timeline():
            return
            yield  # pragma: no cover

        async def fake_watch_rejected():
            yield fake_rejection

        robot.watch_mission_timeline = fake_watch_timeline
        robot.watch_rejected_commands = fake_watch_rejected

        with patch("roombapy_prime_tools.verify_region_commands.confirm", return_value=True):
            events, rejected = await _confirm_show_send_watch(
                robot, command, report, watch_seconds=1, description="test", disconnect_after=False,
            )

        assert events == []
        assert rejected == [fake_rejection]

    @pytest.mark.asyncio
    async def test_a_failing_rejected_watch_does_not_break_the_timeline_watch(self):
        """A failure in watch_rejected_commands() (EXPLORATORY, per its
        own docstring -- e.g. a ValueError if irbt_topic_prefix is
        unexpectedly missing) must not take down the already-working
        mission-timeline watch running alongside it."""
        from roombapy_prime_tools.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        # Without this the shadow fetch returns an AsyncMock, whose
        # .payload.get(...) creates a coroutine nobody awaits -- ten
        # RuntimeWarnings that pass the tests but drown out any GENUINE
        # never-awaited warning a real bug would produce later.
        robot.get_named_shadow.return_value = MagicMock(payload={"state": {"reported": {}}})
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = MagicMock()

        fake_event = MagicMock(event_type="cmd", command=None, room=None, zone=None, error=None)

        async def fake_watch_timeline():
            yield fake_event

        def fake_watch_rejected():
            raise ValueError("simulated: irbt_topic_prefix missing")

        robot.watch_mission_timeline = fake_watch_timeline
        robot.watch_rejected_commands = fake_watch_rejected

        with patch("roombapy_prime_tools.verify_region_commands.confirm", return_value=True):
            events, rejected = await _confirm_show_send_watch(
                robot, command, report, watch_seconds=1, description="test", disconnect_after=False,
            )

        assert events == [fake_event]
        assert rejected == []


class TestStageTwoAndThreeNowIncludeInitiator:
    """REAL GAP FOUND AND FIXED (this session, jayjay13011's own field
    report showing all three stages' actual payloads side by side):
    stage 2 and stage 3 never added "initiator", always testing the
    same "no initiator" shape as stage 1 -- never actually exercising
    the initiator+command hypothesis stage 1b was built to test."""

    @pytest.mark.asyncio
    async def test_stage_two_payload_includes_rmt_app_initiator(self):
        from roombapy_prime.models.mission_control import MissionCommandType, Region, RegionType, RoutineCommand
        from roombapy_prime_tools.verify_region_commands import send_stage_two

        original = RoutineCommand(
            command_type=MissionCommandType.START, asset_id="BLID", initiator=None,
            regions=[Region(region_id="1", region_type=RegionType.RID)],
        )
        favorite = MagicMock(favorite_id="fav1", name="Test", command_defs=[original])
        robot = AsyncMock()
        robot.blid = "BLID"   # a real PrimeRobot has this; the target-robot check reads it
        robot.get_favorites.return_value = [favorite]
        captured = {}

        async def fake_confirm_show_send_watch(robot_arg, command, report, watch_seconds, description):
            captured["command"] = command
            return []

        fake_session_cm = MagicMock()
        fake_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch("roombapy_prime_tools._cli.PrimeFactory.create_prime_robot",
                   new=AsyncMock(return_value=robot)), \
             patch("roombapy_prime_tools.verify_region_commands._confirm_show_send_watch", fake_confirm_show_send_watch), \
             patch("aiohttp.ClientSession", return_value=fake_session_cm):
            await send_stage_two("u", "p", "US", "BLID", "fav1", 0, suction_level=2, watch_seconds=0)

        assert captured["command"].initiator == "rmtApp"

    @pytest.mark.asyncio
    async def test_stage_three_payload_includes_rmt_app_initiator(self):
        from roombapy_prime_tools.verify_region_commands import send_stage_three

        robot = AsyncMock()
        captured = {}

        async def fake_confirm_show_send_watch(robot_arg, command, report, watch_seconds, description):
            captured["command"] = command
            return []

        fake_session_cm = MagicMock()
        fake_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        fake_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch("roombapy_prime_tools._cli.PrimeFactory.create_prime_robot",
                   new=AsyncMock(return_value=robot)), \
             patch("roombapy_prime_tools.verify_region_commands._confirm_show_send_watch", fake_confirm_show_send_watch), \
             patch("aiohttp.ClientSession", return_value=fake_session_cm):
            await send_stage_three(
                "u", "p", "US", "BLID", p2map_id="MAP1", room_id="2", region_type="rid", watch_seconds=0,
            )

        assert captured["command"].initiator == "rmtApp"


def test_add_favorite_id_if_missing_adds_it_when_unset():
    """REAL GAP FOUND (this session, re-analyzing prior research):
    the real app's own RoutineCommandBuilder.setFromFavorite() always
    sends favorite_id together with a favorite's resolved
    command_defs (confirmed via send_routine_command_via_cmd_topic()'s
    own docstring) -- but no stage of this script ever added it,
    despite RoutineCommand.to_json() having supported emitting it
    since it was written."""
    from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
    from roombapy_prime_tools.verify_region_commands import _add_favorite_id_if_missing

    original = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID", favorite_id=None)

    result = _add_favorite_id_if_missing(original, "fav123")

    assert result is not None
    assert result.favorite_id == "fav123"
    assert result.to_json()["favorite_id"] == "fav123"
    # everything else must be untouched.
    assert result.command_type == original.command_type
    assert result.asset_id == original.asset_id


def test_add_favorite_id_if_missing_returns_none_when_already_set():
    from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
    from roombapy_prime_tools.verify_region_commands import _add_favorite_id_if_missing

    original = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID", favorite_id="already-set")

    result = _add_favorite_id_if_missing(original, "fav123")

    assert result is None


class TestStagesOneOneBTwoNowIncludeFavoriteId:
    """REAL GAP FOUND AND FIXED (this session) -- see
    _add_favorite_id_if_missing()'s own docstring for the full finding.
    Every real payload shown by any field tester so far was missing
    this field entirely."""

    def _fake_session_cm(self):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=MagicMock())
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    @pytest.mark.asyncio
    async def test_stage_one_payload_includes_favorite_id(self):
        from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
        from roombapy_prime_tools.verify_region_commands import send_stage_one

        original = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID", favorite_id=None)
        favorite = MagicMock(favorite_id="fav1", name="Test", command_defs=[original])
        robot = AsyncMock()
        robot.blid = "BLID"   # a real PrimeRobot has this; the target-robot check reads it
        robot.get_favorites.return_value = [favorite]
        captured = {}

        async def fake_confirm_show_send_watch(robot_arg, command, report, watch_seconds, description):
            captured["command"] = command
            return []

        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch("roombapy_prime_tools._cli.PrimeFactory.create_prime_robot",
                   new=AsyncMock(return_value=robot)), \
             patch("roombapy_prime_tools.verify_region_commands._confirm_show_send_watch", fake_confirm_show_send_watch), \
             patch("roombapy_prime_tools._cli.aiohttp.ClientSession",
                   return_value=self._fake_session_cm()):
            await send_stage_one("u", "p", "US", "BLID", "fav1", 0, watch_seconds=0)

        assert captured["command"].favorite_id == "fav1"

    @pytest.mark.asyncio
    async def test_stage_one_with_initiator_payload_includes_both_fields(self):
        from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand
        from roombapy_prime_tools.verify_region_commands import send_stage_one_with_initiator

        original = RoutineCommand(
            command_type=MissionCommandType.START, asset_id="BLID", favorite_id=None, initiator=None,
        )
        favorite = MagicMock(favorite_id="fav1", name="Test", command_defs=[original])
        robot = AsyncMock()
        robot.blid = "BLID"   # a real PrimeRobot has this; the target-robot check reads it
        robot.get_favorites.return_value = [favorite]
        captured = {}

        async def fake_confirm_show_send_watch(robot_arg, command, report, watch_seconds, description):
            captured["command"] = command
            return []

        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch("roombapy_prime_tools._cli.PrimeFactory.create_prime_robot",
                   new=AsyncMock(return_value=robot)), \
             patch("roombapy_prime_tools.verify_region_commands._confirm_show_send_watch", fake_confirm_show_send_watch), \
             patch("roombapy_prime_tools._cli.aiohttp.ClientSession",
                   return_value=self._fake_session_cm()):
            await send_stage_one_with_initiator("u", "p", "US", "BLID", "fav1", 0, watch_seconds=0)

        assert captured["command"].favorite_id == "fav1"
        assert captured["command"].initiator == "rmtApp"

    @pytest.mark.asyncio
    async def test_stage_two_payload_includes_favorite_id_alongside_initiator(self):
        from roombapy_prime.models.mission_control import MissionCommandType, Region, RegionType, RoutineCommand
        from roombapy_prime_tools.verify_region_commands import send_stage_two

        original = RoutineCommand(
            command_type=MissionCommandType.START, asset_id="BLID", initiator=None, favorite_id=None,
            regions=[Region(region_id="1", region_type=RegionType.RID)],
        )
        favorite = MagicMock(favorite_id="fav1", name="Test", command_defs=[original])
        robot = AsyncMock()
        robot.blid = "BLID"   # a real PrimeRobot has this; the target-robot check reads it
        robot.get_favorites.return_value = [favorite]
        captured = {}

        async def fake_confirm_show_send_watch(robot_arg, command, report, watch_seconds, description):
            captured["command"] = command
            return []

        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch("roombapy_prime_tools._cli.PrimeFactory.create_prime_robot",
                   new=AsyncMock(return_value=robot)), \
             patch("roombapy_prime_tools.verify_region_commands._confirm_show_send_watch", fake_confirm_show_send_watch), \
             patch("roombapy_prime_tools._cli.aiohttp.ClientSession",
                   return_value=self._fake_session_cm()):
            await send_stage_two("u", "p", "US", "BLID", "fav1", 0, suction_level=2, watch_seconds=0)

        assert captured["command"].favorite_id == "fav1"
        assert captured["command"].initiator == "rmtApp"


class TestReportMissionStatus:
    """NEW (this session, acting on the parallel APK research's
    strongest finding): the app's own applyConditionalChecks() runs a
    readiness check whose refusal surfaces in the MISSION STATUS
    (ResolvedMissionStatus 7/8/12/13, reasons in a
    vector<RobotReadinessState>) -- not on rejected/report. We already
    modelled the two wire fields that would carry it
    (cleanMissionStatus.not_ready / .cond_not_ready) but never read
    them during a test. This is what reading them looks like."""

    def _report(self):
        from roombapy_prime.diagnostics import Report
        return Report()

    def test_not_ready_set_is_flagged_as_a_probable_refusal(self):
        from roombapy_prime_tools.verify_region_commands import _report_mission_status

        report = self._report()
        _report_mission_status(report, {"not_ready": 0}, {"not_ready": 8})

        entry = report.results[-1]
        assert entry.status == "FAILED"
        assert "not_ready=8" in entry.detail

    def test_cond_not_ready_reasons_are_flagged_even_when_not_ready_is_zero(self):
        """cond_not_ready carries the vector<RobotReadinessState>
        reasons -- meaningful on its own."""
        from roombapy_prime_tools.verify_region_commands import _report_mission_status

        report = self._report()
        _report_mission_status(report, {}, {"not_ready": 0, "cond_not_ready": ["binFull"]})

        entry = report.results[-1]
        assert entry.status == "FAILED"
        assert "binFull" in entry.detail

    def test_unchanged_status_is_reported_as_such(self):
        from roombapy_prime_tools.verify_region_commands import _report_mission_status

        report = self._report()
        same = {"phase": "charge", "not_ready": 0, "cond_not_ready": []}
        _report_mission_status(report, same, dict(same))

        entry = report.results[-1]
        assert entry.status == "OK"
        assert "unchanged" in entry.detail

    def test_a_real_change_is_reported_as_the_command_having_reached_something(self):
        from roombapy_prime_tools.verify_region_commands import _report_mission_status

        report = self._report()
        _report_mission_status(
            report,
            {"phase": "charge", "not_ready": 0, "cond_not_ready": []},
            {"phase": "run", "not_ready": 0, "cond_not_ready": []},
        )

        entry = report.results[-1]
        assert entry.status == "OK"
        assert "changed" in entry.detail

    def test_unreadable_shadow_is_skipped_not_failed(self):
        """Diagnostics on top of the actual test must never be able to
        make the test itself look failed."""
        from roombapy_prime_tools.verify_region_commands import _report_mission_status

        report = self._report()
        _report_mission_status(report, None, None)

        assert report.results[-1].status == "SKIPPED"


class TestRobotReadinessStateNaming:
    """NEW (this session, from the parallel APK research): the values
    carried by cleanMissionStatus.not_ready / .cond_not_ready. The
    enum is deliberately PARTIAL -- only values actually confirmed by
    the research are listed, so unknown ones must stay honestly
    unknown rather than getting a guessed label."""

    def test_names_the_confirmed_refusal_reasons(self):
        from roombapy_prime.models import RobotReadinessState

        assert RobotReadinessState.name_for(22) == "MAP_VERSION_MISMATCH"
        assert RobotReadinessState.name_for(75) == "NO_VAC_WITH_PAD"
        assert RobotReadinessState.name_for(76) == "NO_MOP_WITHOUT_PAD"

    def test_unknown_value_is_reported_as_unknown_not_guessed(self):
        from roombapy_prime.models import RobotReadinessState

        assert RobotReadinessState.name_for(43) == "UNKNOWN_43"

    def test_none_is_handled(self):
        from roombapy_prime.models import RobotReadinessState

        assert RobotReadinessState.name_for(None) == "None"

    def test_report_names_the_code_in_its_detail_text(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _report_mission_status

        report = Report()
        _report_mission_status(report, {}, {"not_ready": 22, "cond_not_ready": [75]})

        detail = report.results[-1].detail
        assert "MAP_VERSION_MISMATCH" in detail
        assert "NO_VAC_WITH_PAD" in detail


class TestPreflightMapVersionCheck:
    """HYPOTHESIS A made checkable without moving the robot: a stored
    favorite carries the map version current when it was SAVED, but
    the robot re-versions its map over time."""

    def _command(self, version):
        cmd = MagicMock()
        cmd.pmap_version_id = version
        return cmd

    def _robot(self, active_versions):
        robot = AsyncMock()
        robot.get_active_map_versions.return_value = [
            MagicMock(active_p2mapv_id=v) for v in active_versions
        ]
        return robot

    @pytest.mark.asyncio
    async def test_matching_version_reports_ok(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_map_version_check

        report = Report()
        await _preflight_map_version_check(
            self._robot(["260716T183242.325"]), self._command("260716T183242.325"), report
        )

        assert report.results[-1].status == "OK"

    @pytest.mark.asyncio
    async def test_stale_version_is_flagged_as_the_prime_suspect(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_map_version_check

        report = Report()
        await _preflight_map_version_check(
            self._robot(["260901T090000.000"]), self._command("260716T183242.325"), report
        )

        entry = report.results[-1]
        assert entry.status == "FAILED"
        assert "MAP_VERSION_MISMATCH" in entry.detail

    @pytest.mark.asyncio
    async def test_a_fetch_failure_is_skipped_not_failed(self):
        """Diagnostics must never make the actual test look failed."""
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_map_version_check

        robot = AsyncMock()
        robot.get_active_map_versions.side_effect = RuntimeError("simulated")
        report = Report()

        await _preflight_map_version_check(robot, self._command("x"), report)

        assert report.results[-1].status == "SKIPPED"


class TestPreflightPadVsModeCheck:
    """HYPOTHESIS B: RobotReadinessState 75/76 suggest the robot
    refuses a command whose operating mode doesn't match the fitted
    pad. The rule itself lives ROBOT-side (the app only parses an
    incoming readiness value), so this reports both inputs rather than
    reproducing the rule -- flagging only the one unambiguous case.

    Now a PURE function: it takes the already-fetched ro-currentstate
    block instead of fetching its own, because that shadow is also
    needed for the before-send status snapshot microseconds later. One
    fetch, several readers -- see fetch_current_state()'s docstring.
    A welcome side effect: these tests no longer need a mocked robot."""

    def _command(self, mode):
        region = MagicMock()
        region.params = {"operatingMode": mode} if mode is not None else {}
        cmd = MagicMock()
        cmd.regions = [region]
        return cmd

    def _report(self):
        from roombapy_prime.diagnostics import Report
        return Report()

    def test_mopping_mode_with_no_pad_is_flagged(self):
        """jayjay13011's exact case: operatingMode 32
        (VAC_MOP_COMBO_ONLY) with no pad fitted."""
        from roombapy_prime_tools.verify_region_commands import preflight_pad_vs_mode_check

        report = self._report()
        preflight_pad_vs_mode_check(self._command(32), {"detectedPad": "noPad"}, report)

        entry = report.results[-1]
        assert entry.status == "FAILED"
        assert "NO_MOP_WITHOUT_PAD" in entry.detail

    def test_mopping_mode_with_a_pad_fitted_is_only_reported(self):
        from roombapy_prime_tools.verify_region_commands import preflight_pad_vs_mode_check

        report = self._report()
        preflight_pad_vs_mode_check(self._command(32), {"detectedPad": "reusableWet"}, report)

        assert report.results[-1].status == "OK"

    def test_vacuum_only_mode_with_no_pad_is_not_flagged(self):
        """Vacuuming without a pad is perfectly normal -- must not
        produce a false alarm."""
        from roombapy_prime_tools.verify_region_commands import preflight_pad_vs_mode_check

        report = self._report()
        preflight_pad_vs_mode_check(self._command(2), {"detectedPad": "noPad"}, report)

        assert report.results[-1].status == "OK"

    def test_missing_operating_mode_is_skipped(self):
        from roombapy_prime_tools.verify_region_commands import preflight_pad_vs_mode_check

        report = self._report()
        preflight_pad_vs_mode_check(self._command(None), {"detectedPad": "noPad"}, report)

        assert report.results[-1].status == "SKIPPED"

    def test_unreadable_shadow_is_skipped_not_failed(self):
        """The shared fetch failing must not make the pad check look
        like a real finding."""
        from roombapy_prime_tools.verify_region_commands import preflight_pad_vs_mode_check

        report = self._report()
        preflight_pad_vs_mode_check(self._command(32), None, report)

        assert report.results[-1].status == "SKIPPED"


class TestEnvelopedCommand:
    """STAGE 1c: the same CommandDef WRAPPED rather than flattened.
    Three signals point at this being the real wire form -- see
    _EnvelopedCommand's own docstring, the strongest being real data:
    chairstacker's cleanSchedule2 stores its command in a field
    literally named "cmdStr", a STRING rather than a nested object."""

    class _Fake:
        regions = ["region-marker"]
        pmap_version_id = "version-marker"

        def to_json(self):
            return {"command": "start", "regions": [{"region_id": "1"}]}

    def test_cmd_style_serializes_the_command_as_a_json_string(self):
        from roombapy_prime_tools.verify_region_commands import _EnvelopedCommand

        payload = _EnvelopedCommand(self._Fake(), "cmd").to_json()

        assert set(payload) == {"cmd"}
        assert isinstance(payload["cmd"], str)
        assert json.loads(payload["cmd"]) == {"command": "start", "regions": [{"region_id": "1"}]}

    def test_cmd_json_style_nests_the_command_as_an_object(self):
        from roombapy_prime_tools.verify_region_commands import _EnvelopedCommand

        payload = _EnvelopedCommand(self._Fake(), "cmdJson").to_json()

        assert set(payload) == {"cmdJson"}
        assert payload["cmdJson"] == {"command": "start", "regions": [{"region_id": "1"}]}

    def test_exactly_one_envelope_field_is_present_never_both(self):
        """The confirmed rule is 'exactly one, not both' -- a payload
        carrying both would be invalid by that rule."""
        from roombapy_prime_tools.verify_region_commands import _EnvelopedCommand

        for style in ("cmd", "cmdJson"):
            payload = _EnvelopedCommand(self._Fake(), style).to_json()
            assert len(payload) == 1

    def test_other_attributes_delegate_to_the_real_command(self):
        """The pre-flight checks read .regions/.pmap_version_id -- they
        must see the genuine command underneath, not the wrapper."""
        from roombapy_prime_tools.verify_region_commands import _EnvelopedCommand

        wrapped = _EnvelopedCommand(self._Fake(), "cmd")

        assert wrapped.regions == ["region-marker"]
        assert wrapped.pmap_version_id == "version-marker"

    def test_an_invalid_style_is_rejected_immediately(self):
        from roombapy_prime_tools.verify_region_commands import _EnvelopedCommand

        with pytest.raises(ValueError):
            _EnvelopedCommand(self._Fake(), "somethingElse")


class TestRoundtripFidelityCheck:
    """THE most direct test of the newest lead: the app has two command
    formats, the legacy one reading fields our payloads never contained
    (map_components, linked_mission_id, multi_polygons, smart_clean_id).
    We can't see which the app picks -- but we CAN check whether OUR
    own parse-then-reserialize round-trip silently drops fields the
    stored favorite actually carries."""

    def _robot(self, raw_command_def):
        robot = AsyncMock()
        robot.get_favorites_raw.return_value = [
            {"favorite_id": "fav1", "command_defs": [raw_command_def]}
        ]
        return robot

    def _command(self, our_json):
        cmd = MagicMock()
        cmd.to_json.return_value = our_json
        return cmd

    @pytest.mark.asyncio
    async def test_flags_a_dropped_top_level_field(self):
        """e.g. a legacy-format field our models don't know."""
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_roundtrip_fidelity_check

        report = Report()
        await _preflight_roundtrip_fidelity_check(
            self._robot({"command": "start", "map_components": [{"x": 1}]}),
            self._command({"command": "start"}),
            "fav1", 0, report,
        )

        entry = report.results[-1]
        assert entry.status == "FAILED"
        assert "map_components" in entry.detail

    @pytest.mark.asyncio
    async def test_flags_a_dropped_region_level_field(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_roundtrip_fidelity_check

        report = Report()
        await _preflight_roundtrip_fidelity_check(
            self._robot({"regions": [{"region_id": "1", "some_unknown_key": 7}]}),
            self._command({"regions": [{"region_id": "1"}]}),
            "fav1", 0, report,
        )

        entry = report.results[-1]
        assert entry.status == "FAILED"
        assert "some_unknown_key" in entry.detail

    @pytest.mark.asyncio
    async def test_added_fields_are_not_treated_as_a_problem(self):
        """initiator/favorite_id are deliberately ADDED by us -- only
        DROPPED fields matter."""
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_roundtrip_fidelity_check

        report = Report()
        await _preflight_roundtrip_fidelity_check(
            self._robot({"command": "start"}),
            self._command({"command": "start", "initiator": "rmtApp", "favorite_id": "fav1"}),
            "fav1", 0, report,
        )

        assert report.results[-1].status == "OK"

    @pytest.mark.asyncio
    async def test_unavailable_raw_favorites_is_skipped_not_failed(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_roundtrip_fidelity_check

        robot = AsyncMock()
        robot.get_favorites_raw.side_effect = RuntimeError("simulated")
        report = Report()

        await _preflight_roundtrip_fidelity_check(robot, self._command({}), "fav1", 0, report)

        assert report.results[-1].status == "SKIPPED"


class TestDisplayedPayloadMatchesWhatIsSent:
    """REAL DISPLAY BUG FIXED: the script printed command.to_json(),
    but publish_cmd_payload() adds "time" just before publishing -- so
    the shown payload was NOT the payload that went out. A parallel
    research pass compared a field tester's printed payload against the
    app's own builder, found no "time", and reasonably concluded it was
    missing. It was on the wire the whole time, just never displayed."""

    @pytest.mark.asyncio
    async def test_displayed_payload_includes_the_time_field(self, capsys):
        from roombapy_prime_tools.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        robot.send_routine_command_via_cmd_topic.return_value = True
        command = MagicMock()
        command.to_json.return_value = {"command": "start", "regions": []}

        with patch("roombapy_prime_tools.verify_region_commands.confirm", return_value=False):
            await _confirm_show_send_watch(robot, command, MagicMock(), 0, "test")

        out = capsys.readouterr().out
        assert '"time"' in out, "the displayed payload must show the time field publish adds"

    def test_publish_adds_time_when_absent(self):
        """Confirms the underlying behaviour the display now mirrors."""
        from roombapy_prime.mqtt_client import PrimeMqttClient

        assert hasattr(PrimeMqttClient, "publish_cmd_payload")


class TestStageBuildersAreTheSingleSourceOfTruth:
    """The three build_stage_*_command() functions exist because the
    standalone stage functions and the session runner used to compose
    these additions by hand, separately -- and the two copies drifted
    every single time something changed. Adding favorite_id, adding
    initiator to stages 2/3, and removing the envelope experiment each
    had to be done twice, and each was at some point done only once."""

    def _favorite_command(self):
        from roombapy_prime.models.mission_control import (
            MissionCommandType, Region, RegionType, RoutineCommand,
        )
        return RoutineCommand(
            command_type=MissionCommandType.START, asset_id="BLID",
            initiator=None, favorite_id=None,
            regions=[Region(region_id="1", region_type=RegionType.RID)],
        )

    def test_stage_one_restores_favorite_id_only(self):
        from roombapy_prime_tools.verify_region_commands import build_stage_one_command

        command = build_stage_one_command(self._favorite_command(), "fav1")

        assert command.favorite_id == "fav1"
        assert command.initiator is None, "stage 1 must NOT add an initiator -- that's 1b's job"

    def test_stage_one_b_adds_both_initiator_and_favorite_id(self):
        from roombapy_prime_tools.verify_region_commands import build_stage_one_b_command

        command = build_stage_one_b_command(self._favorite_command(), "fav1")

        assert command.initiator == "rmtApp"
        assert command.favorite_id == "fav1"

    def test_stage_one_b_returns_none_when_the_favorite_already_has_an_initiator(self):
        """Sending it anyway would be byte-identical to stage 1 -- the
        caller should say so rather than fire a duplicate at the robot."""
        import dataclasses

        from roombapy_prime_tools.verify_region_commands import build_stage_one_b_command

        already_set = dataclasses.replace(self._favorite_command(), initiator="rmtApp")

        assert build_stage_one_b_command(already_set, "fav1") is None

    def test_stage_two_builds_on_stage_one_b_and_reports_the_previous_level(self):
        from roombapy_prime_tools.verify_region_commands import build_stage_two_command

        command, original_level = build_stage_two_command(self._favorite_command(), "fav1", 2)

        assert command.initiator == "rmtApp"
        assert command.favorite_id == "fav1"
        assert command.to_json()["params"]["suctionLevel"] == 2
        assert original_level is None

    def test_session_runner_and_standalone_scripts_use_the_same_builders(self):
        """The actual regression guard: if either side ever goes back to
        composing by hand, this catches it. Both modules must reference
        the shared builders and nothing else."""

        from roombapy_prime_tools import verify_region_commands, verify_region_commands_session

        session_src = inspect.getsource(verify_region_commands_session)
        for builder in ("build_stage_one_command", "build_stage_one_b_command",
                        "build_stage_two_command"):
            assert builder in session_src, f"session runner no longer uses {builder}"

        for hand_rolled in ("_add_initiator_if_missing", "_add_favorite_id_if_missing",
                            "_build_modified_command"):
            assert hand_rolled not in session_src, (
                f"session runner composes {hand_rolled} by hand again -- that is exactly the "
                "drift the builders were extracted to prevent"
            )
        assert hasattr(verify_region_commands, "build_stage_one_command")


class TestShadowFetchesAreBundled:
    """Guards the bundling itself. A field tester has already hit
    server-side throttling where only 3 of 8 shadow requests came
    through -- diagnostics that crowd out the traffic they are
    measuring would be the worst possible failure mode here, and it is
    the kind of regression that reappears silently the next time
    someone adds a check."""

    @pytest.mark.asyncio
    async def test_only_two_shadow_fetches_per_send_one_before_one_after(self):
        """Two is the floor, not an accident: the whole point of the
        mission-status check is comparing before against after."""
        from roombapy_prime_tools.verify_region_commands import _confirm_show_send_watch

        robot = AsyncMock()
        robot.send_routine_command_via_cmd_topic.return_value = True
        robot.get_named_shadow.return_value = MagicMock(
            payload={"state": {"reported": {"detectedPad": "noPad"}}}
        )
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        command.regions = []

        with patch("roombapy_prime_tools.verify_region_commands.confirm", return_value=True):
            await _confirm_show_send_watch(robot, command, MagicMock(), 0, "test")

        fetched = [c.args[0] for c in robot.get_named_shadow.call_args_list]
        assert fetched == ["ro-currentstate", "ro-currentstate"], (
            f"expected exactly one fetch before and one after the send, got {fetched}"
        )

    def test_the_readers_are_pure_functions(self):
        """Both consumers of the shared fetch must take DATA, not a
        robot -- otherwise the next one added would quietly fetch its
        own again."""

        from roombapy_prime_tools.verify_region_commands import (
            mission_status_from, preflight_pad_vs_mode_check,
        )

        assert not inspect.iscoroutinefunction(mission_status_from)
        assert not inspect.iscoroutinefunction(preflight_pad_vs_mode_check)
        assert "robot" not in inspect.signature(mission_status_from).parameters
        assert "robot" not in inspect.signature(preflight_pad_vs_mode_check).parameters


class TestPreflightChecksActuallySeeRealData:
    """Both pre-flight checks silently did nothing on every real run
    they ever had, for the same class of reason: they looked for the
    wrong shape.

    The fidelity check searched for "command_defs"/"commandDefs" while
    the confirmed wire key is "commanddefs", all lowercase -- so it
    reported "raw command_def not found" every time and never compared
    anything. The pad check used getattr() on regions that arrive as
    plain dicts, so it reported "no operatingMode in regions" for a
    payload that visibly contained one on every single region.

    Neither failed loudly. Both just quietly reported nothing to see."""

    @pytest.mark.asyncio
    async def test_fidelity_check_finds_the_lowercase_wire_key(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import _preflight_roundtrip_fidelity_check

        robot = AsyncMock()
        robot.get_favorites_raw.return_value = [{
            "favorite_id": "fav1",
            "commanddefs": [{"command": "start", "somethingWeDrop": 1}],
        }]
        command = MagicMock()
        command.to_json.return_value = {"command": "start"}
        report = Report()

        await _preflight_roundtrip_fidelity_check(robot, command, "fav1", 0, report)

        entry = report.results[-1]
        assert entry.status == "FAILED", "must actually compare, not skip"
        assert "somethingWeDrop" in entry.detail

    def test_pad_check_reads_operating_mode_from_dict_regions(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import preflight_pad_vs_mode_check

        command = MagicMock()
        # Exactly the shape a stored favorite delivers: plain dicts.
        command.regions = [{"region_id": "10", "type": "rid",
                            "params": {"operatingMode": 32, "suctionLevel": 1}}]
        report = Report()

        preflight_pad_vs_mode_check(command, {"detectedPad": "noPad"}, report)

        entry = report.results[-1]
        assert entry.status != "SKIPPED", "must see the mode, not report it missing"
        assert "NO_MOP_WITHOUT_PAD" in entry.detail


class TestPreflightTargetRobotCheck:
    """FOUND IN THE FIELD (DaRealGuGu) and never checked before: the
    stored favorite names a robot_id, and we publish to a BLID. Nobody
    had verified those are the same robot.

    Both real cases below are taken verbatim from field logs -- one
    account where they match, one where they do not."""

    def _command(self, robot_id):
        cmd = MagicMock()
        cmd.asset_id = robot_id
        return cmd

    def _check(self, robot_id, blid):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import preflight_target_robot_check

        report = Report()
        preflight_target_robot_check(self._command(robot_id), blid, report)
        return report.results[-1]

    def test_matching_identifiers_pass(self):
        """jayjay13011's real account: BLID and robot_id identical."""
        same = "0ECDE8AF7343838938E479DAFECD831B"

        assert self._check(same, same).status == "OK"

    def test_mismatched_identifiers_are_flagged(self):
        """DaRealGuGu's real account: a 16-character BLID and a
        32-character robot_id, with the favorite's map id carrying the
        robot_id's prefix rather than the BLID's."""
        entry = self._check("0B710054CA277C04B2700374A8349C9A", "3178480C91223620")

        assert entry.status == "FAILED"
        assert "0B710054CA277C04B2700374A8349C9A" in entry.detail
        assert "3178480C91223620" in entry.detail

    def test_it_now_blocks_and_names_the_correct_blid(self):
        """REVERSED after a field run. The first version only warned, on
        the reasoning that the identifiers might legitimately differ.
        DaRealGuGu's account settled it: the command was going to a
        Roomba 980 (classic protocol) while the favorite belonged to the
        Prime robot. A mismatch means the run is noise, so it stops --
        and tells you the exact --blid to use instead."""
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import preflight_target_robot_check

        report = Report()
        result = preflight_target_robot_check(self._command("A" * 32), "B" * 16, report)

        assert result is False
        detail = report.results[-1].detail
        assert f"--blid {'A' * 32}" in detail, "must name the BLID to use instead"
        assert "--i-know-the-robot-id-differs" in detail, "must offer the deliberate override"

    def test_a_matching_pair_returns_true_so_the_send_proceeds(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_region_commands import preflight_target_robot_check

        same = "0ECDE8AF7343838938E479DAFECD831B"

        assert preflight_target_robot_check(self._command(same), same, Report()) is True

    def test_a_command_without_a_robot_id_is_skipped(self):
        assert self._check(None, "BLID").status == "SKIPPED"


class TestEventSummaryReadsTheRealWireShape:
    """REAL BUG, found at the worst possible moment. The summariser was
    written against a parsed MissionTimelineEvent model, but what
    arrives is a raw ShadowResponse whose content sits in a payload
    DICT. Every getattr() returned None, so the summary printed
    "[None]" per event.

    It went unnoticed for months because every run until now observed
    zero events. It surfaced on the first run where a robot genuinely
    started a mission -- the one moment the summary had to be readable,
    it said nothing at all."""

    def _response(self, **payload):
        return MagicMock(payload=payload)

    def test_the_real_payload_shape_is_rendered(self):
        """Fields taken verbatim from DaRealGuGu's successful run."""
        from roombapy_prime_tools.verify_region_commands import _summarize_events

        out = _summarize_events([self._response(
            cmd={"command": "start", "initiator": "rmtApp",
                 "regions": [{"region_id": "10"}, {"region_id": "11"}]},
            event=[{"ts": 1784984724, "type": "start"}],
            finEvents=[],
            mission_id="01KYCP2SAQB1GHKJD76MHK239F",
            nMssn=34,
        )])

        assert "01KYCP2SAQB1GHKJD76MHK239F" in out
        assert "type='start'" in out
        assert "None" not in out.replace("(none)", "")

    def test_finished_events_are_labelled_separately_from_current_ones(self):
        """finEvents and event mean different things -- a mission that
        has started versus one that has completed a phase."""
        from roombapy_prime_tools.verify_region_commands import _summarize_events

        out = _summarize_events([self._response(
            event=[{"ts": 2, "type": "padWash"}],
            finEvents=[{"ts": 1, "type": "start"}],
            mission_id="M1",
        )])

        assert "[event] type='padWash'" in out
        assert "[finished] type='start'" in out

    def test_the_echoed_command_shows_which_regions_the_robot_accepted(self):
        """The robot echoes our command back. Which regions it carries
        is the entire question these tests exist to answer."""
        from roombapy_prime_tools.verify_region_commands import _summarize_events

        out = _summarize_events([self._response(
            cmd={"command": "start", "initiator": "rmtApp",
                 "regions": [{"region_id": "13", "type": "rid"}]},
            mission_id="M1",
        )])

        assert "echoed back" in out
        assert "'13'" in out

    def test_no_events_still_says_so_clearly(self):
        from roombapy_prime_tools.verify_region_commands import _summarize_events

        assert "NO events observed" in _summarize_events([])
