"""Tests for verify_map_edit's room-selection logic.

This script sits at 24% coverage while performing REAL map edits, and
the two pickers below carry its central safety property: a room is only
ever chosen as a test subject if it ALREADY has a name.

That is not a detail. The whole design rests on being able to put the
original value back afterwards, and RenameRoomV1's `name` field is a
plain required string -- there is no confirmed way to clear a name back
to nothing. A room without one is therefore unrevertible, and picking it
would leave a tester's real map permanently altered by a test that was
supposed to be reversible.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from roombapy_prime_tools.verify_map_edit import _pick_test_room, _pick_test_room_with_category


def _room(name, room_id="r1", room_type=None):
    feature = MagicMock()
    feature.feature_id = room_id
    feature.properties.name = name
    feature.properties.room_type = room_type
    return feature


class TestPickTestRoom:
    def test_picks_the_first_named_room(self):
        rooms = [("map1", _room("Kitchen", "10")), ("map1", _room("Hall", "11"))]

        assert _pick_test_room(rooms) == ("map1", "10", "Kitchen")

    def test_skips_unnamed_rooms_rather_than_using_them(self):
        """THE safety property: an unnamed room cannot be reverted, so
        it must never be chosen even when it is the first candidate."""
        rooms = [("map1", _room(None, "10")), ("map1", _room("Hall", "11"))]

        assert _pick_test_room(rooms) == ("map1", "11", "Hall")

    def test_an_empty_string_name_counts_as_unnamed(self):
        """Same reasoning -- "" is no more revertible than None."""
        rooms = [("map1", _room("", "10")), ("map1", _room("Hall", "11"))]

        assert _pick_test_room(rooms) == ("map1", "11", "Hall")

    def test_a_room_without_an_id_is_skipped(self):
        rooms = [("map1", _room("Kitchen", None)), ("map1", _room("Hall", "11"))]

        assert _pick_test_room(rooms) == ("map1", "11", "Hall")

    def test_returns_none_when_nothing_is_safe_to_test(self):
        """Better no test at all than an unrevertible one."""
        assert _pick_test_room([("map1", _room(None, "10"))]) is None

    def test_returns_none_for_an_empty_map_set(self):
        assert _pick_test_room([]) is None

    def test_searches_across_several_maps(self):
        """Multi-map households are the norm for these devices, and a
        named room may only exist on the second floor's map."""
        rooms = [("map1", _room(None, "10")), ("map2", _room("Study", "20"))]

        assert _pick_test_room(rooms) == ("map2", "20", "Study")


class TestPickTestRoomWithCategory:
    """Same safety property, plus a valid room category -- the category
    test cannot revert what it cannot first read."""

    def test_requires_a_recognised_category(self):
        from roombapy_prime.models.map_editing import RoomCategory

        valid = next(iter(RoomCategory)).value
        rooms = [
            ("map1", _room("Kitchen", "10", room_type="not-a-real-category")),
            ("map1", _room("Hall", "11", room_type=valid)),
        ]

        result = _pick_test_room_with_category(rooms)

        assert result is not None
        assert result[1] == "11"

    def test_a_room_with_no_category_at_all_is_skipped(self):
        rooms = [("map1", _room("Kitchen", "10", room_type=None))]

        assert _pick_test_room_with_category(rooms) is None

    def test_still_requires_an_existing_name(self):
        from roombapy_prime.models.map_editing import RoomCategory

        valid = next(iter(RoomCategory)).value
        rooms = [("map1", _room(None, "10", room_type=valid))]

        assert _pick_test_room_with_category(rooms) is None


class TestRunFlowControl:
    """run()'s orchestration, which sat entirely untested while the
    script performs real map edits.

    Each branch below is a point where the script decides NOT to touch
    someone's map. Those decisions matter more than the happy path: a
    rename that fires when it should have been skipped alters a real
    household's map, and this script's own safety argument is that it
    always reverts -- which only holds if it got as far as recording
    what to revert to.
    """

    @contextlib.asynccontextmanager
    async def _connection(self, robot, report):
        yield robot, report

    def _run_with(self, robot, confirm_answer=True):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_map_edit as mod

        report = Report()
        with patch.object(mod, "connected_robot",
                          lambda *a, **k: self._connection(robot, report)), \
             patch.object(mod, "confirm", return_value=confirm_answer):
            return asyncio.run(mod.run("u", "p", "US", "BLID")), report

    def test_a_failing_map_version_fetch_is_recorded_and_stops_cleanly(self):
        robot = AsyncMock()
        robot.get_active_map_versions.side_effect = RuntimeError("network gone")

        (report, _capture), _ = self._run_with(robot)

        entry = next(r for r in report.results if "map versions" in r.name)
        assert entry.status == "FAILED"
        assert "network gone" in entry.detail
        robot.edit_map.assert_not_awaited()

    def test_no_named_room_anywhere_means_nothing_is_edited(self):
        """The safety property from the picker, at the orchestration
        level: with nothing revertible, the script must do nothing."""
        robot = AsyncMock()
        robot.get_active_map_versions.return_value = []

        _result, _report = self._run_with(robot)

        robot.edit_map.assert_not_awaited()

    def test_declining_the_confirmation_does_not_edit_the_map(self):
        robot = AsyncMock()
        robot.get_active_map_versions.return_value = []

        _result, _report = self._run_with(robot, confirm_answer=False)

        robot.edit_map.assert_not_awaited()
