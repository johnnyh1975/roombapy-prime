"""Targeted tests for verify_settings_write.py -- the mapping/argparse
logic that's actually testable without a real device. The staged
write itself (--toggle) is, by nature, not automatable -- same
reasoning as this project's other verify_*_write.py test files."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from roombapy_prime_tools import _cli

import pytest

from roombapy_prime.models import RobotSettings
from roombapy_prime_tools.verify_settings_write import _TARGET_SETTINGS


def test_target_settings_has_exactly_the_five_confirmed_fields():
    assert set(_TARGET_SETTINGS) == {
        "child_lock", "eco_charge", "sched_hold", "no_auto_passes", "vac_high",
    }


def test_target_settings_wire_keys_match_the_real_capture():
    """The wire keys here must match the real, confirmed capture
    (chairstacker's raw_shadows.json) exactly -- a typo here would
    silently write to a nonexistent field instead of the intended
    setting."""
    assert _TARGET_SETTINGS == {
        "child_lock": "childLock",
        "eco_charge": "ecoCharge",
        "sched_hold": "schedHold",
        "no_auto_passes": "noAutoPasses",
        "vac_high": "vacHigh",
    }


def test_every_target_attribute_actually_exists_on_robot_settings():
    """Catches a rename in RobotSettings (models/robot_info.py) that
    this script's own mapping wasn't updated for -- getattr() would
    otherwise silently return None via a typo'd attribute name rather
    than raising, which _list_settings/_send_toggle would then
    misreport as "setting is None"."""
    settings = RobotSettings()
    for attr_name in _TARGET_SETTINGS:
        assert hasattr(settings, attr_name), f"RobotSettings has no attribute {attr_name!r}"


def test_wire_keys_are_all_distinct():
    assert len(set(_TARGET_SETTINGS.values())) == len(_TARGET_SETTINGS)


class TestSendToggleBehaviour:
    """The parts of send_toggle() that decide something, exercised
    without a robot. This script had 44 test lines for 264 source
    lines, and none of them touched the actual toggle path -- the one
    place where a mistake writes to a real device."""

    def _session_cm(self):
        """A fake aiohttp session context manager. Without this the
        real one is created and never torn down cleanly, which the
        test environment's lingering-resource check rightly flags."""
        from unittest.mock import AsyncMock, MagicMock

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=MagicMock())
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    def _robot(self, current_value, readback=None, classic_sched_hold=None):
        from unittest.mock import AsyncMock, MagicMock

        robot = AsyncMock()
        robot.blid = "BLID123"
        settings = [
            MagicMock(payload={"state": {"reported": {"childLock": current_value}}}),
            MagicMock(payload={"state": {"reported": {"childLock": readback}}}),
        ]
        robot.get_settings.side_effect = settings
        robot.get_state.return_value = MagicMock(
            payload={"state": {"reported": {"schedHold": classic_sched_hold}}}
        )
        return robot

    @pytest.mark.asyncio
    async def test_unknown_key_is_refused_before_any_login(self):
        """The valid-key check must come first -- otherwise a typo
        costs the tester a password prompt before telling them."""

        from roombapy_prime_tools import verify_settings_write

        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch.object(_cli, "PrimeFactory") as factory, \
             patch("aiohttp.ClientSession", return_value=self._session_cm()):
            await verify_settings_write.send_toggle(
                "u", "p", "US", "BLID", "not_a_real_setting"
            )

        factory.create_prime_robot.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_none_current_value_aborts_rather_than_guessing(self):
        """There is no safe toggle direction from an unknown state --
        picking one would write a value the tester never chose."""

        from roombapy_prime_tools import verify_settings_write

        robot = self._robot(current_value=None)
        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch.object(_cli.PrimeFactory, "create_prime_robot",
                          return_value=robot), \
             patch("aiohttp.ClientSession", return_value=self._session_cm()), \
             patch.object(verify_settings_write, "confirm", return_value=True):
            await verify_settings_write.send_toggle("u", "p", "US", "BLID", "child_lock")

        robot.set_setting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_declining_the_confirmation_sends_nothing(self):

        from roombapy_prime_tools import verify_settings_write

        robot = self._robot(current_value=False)
        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch.object(_cli.PrimeFactory, "create_prime_robot",
                          return_value=robot), \
             patch("aiohttp.ClientSession", return_value=self._session_cm()), \
             patch.object(verify_settings_write, "confirm", return_value=False):
            await verify_settings_write.send_toggle("u", "p", "US", "BLID", "child_lock")

        robot.set_setting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_toggle_flips_to_the_opposite_of_the_current_value(self):
        """Running the same command twice must return the setting to
        where it started -- that is the documented undo, so the flip
        has to be derived from the CURRENT value, never hardcoded."""

        from roombapy_prime_tools import verify_settings_write

        robot = self._robot(current_value=False, readback=True)
        with patch("roombapy_prime_tools._cli.login", new=AsyncMock(return_value=MagicMock(robots={}))), \
             patch.object(_cli.PrimeFactory, "create_prime_robot",
                          return_value=robot), \
             patch("aiohttp.ClientSession", return_value=self._session_cm()), \
             patch.object(verify_settings_write, "confirm", return_value=True):
            await verify_settings_write.send_toggle("u", "p", "US", "BLID", "child_lock")

        robot.set_setting.assert_awaited_once_with("childLock", True)
