"""Flow-control tests for the remaining scripts.

Same principle throughout: these do not check that a payload is built
correctly -- the builders have their own tests -- they check the points
where a script decides NOT to act. Those are the decisions that protect
someone's real robot and real map, and they were the least-covered lines
in the package.

Signatures here were read from the source rather than assumed. Twice in
this project a test was written against an imagined interface and failed
for that reason alone; looking is cheaper than guessing.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from roombapy_prime.diagnostics import Report


@contextlib.asynccontextmanager
async def _connection(robot, report):
    yield robot, report


def _run(module, func_name, robot, *args, confirm_answer=True, **kwargs):
    report = Report()
    with patch.object(module, "connected_robot", lambda *a, **k: _connection(robot, report)), \
         patch.object(module, "confirm", return_value=confirm_answer):
        asyncio.run(getattr(module, func_name)("u", "p", "US", "BLID", *args, **kwargs))
    return report


class TestFavoriteWriteRefusals:
    """Favorites are one-tap routines a user created deliberately.
    Overwriting the wrong one is not recoverable from our side."""

    def _robot(self, favorites=None):
        robot = AsyncMock()
        fav = MagicMock()
        fav.favorite_id = "fav1"
        fav.name = "Kitchen"
        # The script prints the exact payload before asking -- so the
        # fixture has to survive json.dumps, not just attribute access.
        fav.to_json.return_value = {"favorite_id": "fav1", "name": "Kitchen"}
        robot.get_favorites.return_value = favorites if favorites is not None else [fav]
        return robot

    def test_unknown_favorite_id_writes_nothing(self):
        from roombapy_prime_tools import verify_favorite_write as mod

        robot = self._robot()

        _run(mod, "send_update_unchanged", robot, "does-not-exist")

        robot.update_favorite.assert_not_awaited()

    def test_declined_confirmation_writes_nothing(self):
        from roombapy_prime_tools import verify_favorite_write as mod

        robot = self._robot()

        _run(mod, "send_update_unchanged", robot, "fav1", confirm_answer=False)

        robot.update_favorite.assert_not_awaited()

    def test_listing_favorites_never_writes(self):
        """Stage 0 must be provably safe -- it is what a cautious
        tester runs first."""
        from roombapy_prime_tools import verify_favorite_write as mod

        robot = self._robot()

        _run(mod, "list_favorites", robot)

        robot.update_favorite.assert_not_awaited()
        robot.create_favorite.assert_not_awaited()
        robot.delete_favorite.assert_not_awaited()

    def test_declined_deletion_deletes_nothing(self):
        from roombapy_prime_tools import verify_favorite_write as mod

        robot = self._robot()

        _run(mod, "delete_by_id", robot, "fav1", confirm_answer=False)

        robot.delete_favorite.assert_not_awaited()


class TestVirtualWallWriteRefusals:
    """Keep-out zones and virtual walls exist to stop a robot entering
    somewhere. Corrupting that list has physical consequences."""

    def _robot(self, features=None):
        robot = AsyncMock()
        robot.get_map_geojson_link.return_value = "https://example.invalid/bundle"
        robot.get_active_map_versions.return_value = []
        return robot

    def test_listing_maps_never_writes(self):
        from roombapy_prime_tools import verify_virtual_wall_write as mod

        robot = self._robot()

        _run(mod, "list_maps", robot)

        robot.edit_map.assert_not_awaited()

    def test_declined_confirmation_writes_nothing(self):
        from roombapy_prime_tools import verify_virtual_wall_write as mod

        robot = self._robot()

        with contextlib.suppress(Exception):
            _run(mod, "send_update_unchanged", robot, "MAP", "VER", confirm_answer=False)

        robot.edit_map.assert_not_awaited()


class TestMissionCommandsHelpers:
    """_diff_reported_keys drives what a tester is told changed during a
    mission -- a wrong diff sends the investigation somewhere else.

    NOTE ON THE INPUT SHAPE: it takes whole get_state() snapshots and
    looks inside their "reported" block, not bare key dictionaries. An
    earlier version of these tests passed the inner dict directly and
    "passed" for an embarrassing reason: with both key sets empty the
    function prints "No new top-level keys appeared", and asserting
    that "a" and "c" appear in that output matched the letters inside
    the word "appeared". Substring assertions on prose are worth
    almost nothing -- these check for the specific labels instead.
    """

    def _snapshot(self, reported):
        return {"reported": reported}

    def test_new_and_missing_keys_are_reported_under_their_own_labels(self, capsys):
        from roombapy_prime_tools.verify_mission_commands import _diff_reported_keys

        _diff_reported_keys(
            self._snapshot({"onlyBefore": 1, "shared": 2}),
            self._snapshot({"shared": 2, "onlyAfter": 3}),
        )

        out = capsys.readouterr().out
        assert "NEW top-level keys" in out and "onlyAfter" in out
        assert "missing now" in out and "onlyBefore" in out

    def test_a_changed_value_is_reported_as_changed_not_as_new(self, capsys):
        """A field that CHANGED and a field that APPEARED mean very
        different things mid-mission -- conflating them would point an
        investigation the wrong way."""
        from roombapy_prime_tools.verify_mission_commands import _diff_reported_keys

        _diff_reported_keys(self._snapshot({"batPct": 90}), self._snapshot({"batPct": 85}))

        out = capsys.readouterr().out
        assert "VALUE changed" in out and "batPct" in out
        assert "NEW top-level keys" not in out

    def test_identical_states_report_no_difference_at_all(self, capsys):
        from roombapy_prime_tools.verify_mission_commands import _diff_reported_keys

        state = self._snapshot({"batPct": 90, "phase": "run"})
        _diff_reported_keys(state, {"reported": dict(state["reported"])})

        out = capsys.readouterr().out
        assert "No new top-level keys appeared" in out
        assert "No existing key's value changed" in out

    def test_missing_snapshots_do_not_crash(self, capsys):
        """A failed capture must not take the whole run down with it."""
        from roombapy_prime_tools.verify_mission_commands import _diff_reported_keys

        _diff_reported_keys(None, None)

        assert "Diff vs. pre-mission baseline" in capsys.readouterr().out
