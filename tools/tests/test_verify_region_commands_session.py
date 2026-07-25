"""Targeted tests for verify_region_commands_session.py -- the pure,
testable picker logic. The actual multi-stage session (real login,
real robot, real confirmations) is by nature not automatable, same
reasoning as verify_region_commands.py's own test file."""

from __future__ import annotations

import contextlib
import pytest

from unittest.mock import AsyncMock, MagicMock, patch

from roombapy_prime.models.mission_control import Region, RegionType
from roombapy_prime_tools.verify_region_commands_session import _pick_favorite_interactively


def _favorite(favorite_id: str, name: str, command_defs: list):
    fav = MagicMock()
    fav.favorite_id = favorite_id
    fav.name = name
    fav.command_defs = command_defs
    return fav


def _command(regions):
    cmd = MagicMock()
    cmd.regions = regions
    return cmd


class TestPickFavoriteInteractively:
    def test_returns_none_when_no_favorites_at_all(self):
        assert _pick_favorite_interactively([]) is None

    def test_only_offers_stage_one_eligible_command_defs(self):
        """A TID-containing command_def must never be offered here --
        this session runner's own scope is stages 1/1b/2 only."""
        safe_cmd = _command([Region(region_id="1", region_type=RegionType.RID)])
        tid_cmd = _command([Region(region_id="2", region_type=RegionType.TID)])
        fav = _favorite("f1", "Kitchen", [safe_cmd, tid_cmd])

        with patch("builtins.input", return_value="1"):
            result = _pick_favorite_interactively([fav])

        assert result == (fav, 0)

    def test_picks_the_chosen_number(self):
        cmd_a = _command([Region(region_id="1", region_type=RegionType.RID)])
        cmd_b = _command([Region(region_id="2", region_type=RegionType.ZID)])
        fav_a = _favorite("f1", "Kitchen", [cmd_a])
        fav_b = _favorite("f2", "Living Room", [cmd_b])

        with patch("builtins.input", return_value="2"):
            result = _pick_favorite_interactively([fav_a, fav_b])

        assert result == (fav_b, 0)

    def test_returns_none_when_nothing_eligible(self):
        tid_cmd = _command([Region(region_id="2", region_type=RegionType.TID)])
        fav = _favorite("f1", "Ad-hoc only", [tid_cmd])

        result = _pick_favorite_interactively([fav])

        assert result is None

    def test_invalid_input_aborts_cleanly(self):
        safe_cmd = _command([Region(region_id="1", region_type=RegionType.RID)])
        fav = _favorite("f1", "Kitchen", [safe_cmd])

        with patch("builtins.input", return_value="not-a-number"):
            result = _pick_favorite_interactively([fav])

        assert result is None

    def test_out_of_range_number_aborts_cleanly(self):
        safe_cmd = _command([Region(region_id="1", region_type=RegionType.RID)])
        fav = _favorite("f1", "Kitchen", [safe_cmd])

        with patch("builtins.input", return_value="99"):
            result = _pick_favorite_interactively([fav])

        assert result is None


class TestRunSessionFlowControl:
    """The stage sequencing testers actually walk through, which sat
    entirely untested at 28% file coverage.

    What matters here is not that a command is built correctly -- the
    stage builders have their own tests -- but that a "no" at any
    prompt genuinely STOPS. Each stage moves a real robot, so a
    mis-wired prompt does not produce a wrong value; it produces an
    unwanted robot movement in someone's home.
    """

    def _favorite(self, favorite_id="fav1"):
        from roombapy_prime.models.mission_control import (
            MissionCommandType, Region, RegionType, RoutineCommand,
        )

        command = RoutineCommand(
            command_type=MissionCommandType.START, asset_id="BLID",
            initiator=None, favorite_id=None,
            regions=[Region(region_id="1", region_type=RegionType.RID)],
        )
        fav = MagicMock()
        fav.favorite_id = favorite_id
        fav.name = "Kitchen"
        fav.command_defs = [command]
        return fav

    @contextlib.asynccontextmanager
    async def _fake_connection(self, robot, report):
        yield robot, report

    def _patched(self, answers, sends):
        """answers: replies to the between-stage prompts, in order."""
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_region_commands_session as mod

        robot = AsyncMock()
        robot.get_favorites.return_value = [self._favorite()]

        return contextlib.ExitStack(), mod, robot, Report(), iter(answers), sends

    @pytest.mark.asyncio
    async def test_declining_the_first_prompt_stops_after_stage_one(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_region_commands_session as mod

        robot = AsyncMock()
        robot.get_favorites.return_value = [self._favorite()]
        sends: list = []

        async def fake_send(*args, **kwargs):
            sends.append(args[1])
            return [], []

        with patch.object(mod, "connected_robot",
                          lambda *a, **k: self._fake_connection(robot, Report())), \
             patch.object(mod, "run_session_preflight_checks", AsyncMock()), \
             patch.object(mod, "_confirm_show_send_watch", fake_send), \
             patch.object(mod, "confirm", return_value=False):
            await mod.run_session("u", "p", "US", "BLID", "fav1", 0, 2, 0)

        assert len(sends) == 1, "a 'no' after stage 1 must not run stage 1b"

    @pytest.mark.asyncio
    async def test_agreeing_throughout_runs_all_three_stages(self):
        """The mirror image -- otherwise a passing stop-test would also
        pass for a session that never runs anything."""
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_region_commands_session as mod

        robot = AsyncMock()
        robot.get_favorites.return_value = [self._favorite()]
        sends: list = []

        async def fake_send(*args, **kwargs):
            sends.append(args[1])
            return [], []

        with patch.object(mod, "connected_robot",
                          lambda *a, **k: self._fake_connection(robot, Report())), \
             patch.object(mod, "run_session_preflight_checks", AsyncMock()), \
             patch.object(mod, "_confirm_show_send_watch", fake_send), \
             patch.object(mod, "confirm", return_value=True):
            await mod.run_session("u", "p", "US", "BLID", "fav1", 0, 2, 0)

        assert len(sends) == 3

    @pytest.mark.asyncio
    async def test_an_unknown_favorite_id_sends_nothing(self):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_region_commands_session as mod

        robot = AsyncMock()
        robot.get_favorites.return_value = [self._favorite("fav1")]
        sends: list = []

        async def fake_send(*args, **kwargs):
            sends.append(args[1])
            return [], []

        with patch.object(mod, "connected_robot",
                          lambda *a, **k: self._fake_connection(robot, Report())), \
             patch.object(mod, "_confirm_show_send_watch", fake_send), \
             patch.object(mod, "confirm", return_value=True):
            await mod.run_session("u", "p", "US", "BLID", "does-not-exist", 0, 2, 0)

        assert sends == []
