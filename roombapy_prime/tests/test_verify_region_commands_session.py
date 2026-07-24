"""Targeted tests for verify_region_commands_session.py -- the pure,
testable picker logic. The actual multi-stage session (real login,
real robot, real confirmations) is by nature not automatable, same
reasoning as verify_region_commands.py's own test file."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from roombapy_prime.models.mission_control import Region, RegionType
from roombapy_prime.verify_region_commands_session import _pick_favorite_interactively


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
