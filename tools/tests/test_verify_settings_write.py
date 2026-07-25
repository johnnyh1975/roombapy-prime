"""Targeted tests for verify_settings_write.py -- the mapping/argparse
logic that's actually testable without a real device. The staged
write itself (--toggle) is, by nature, not automatable -- same
reasoning as this project's other verify_*_write.py test files."""

from __future__ import annotations

import asyncio
import contextlib

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


class TestCrossCheckFailureDoesNotDestroyTheRun:
    """FIELD CASE (DaRealGuGu, Roomba Plus 505): the five settings
    printed perfectly, and then the optional cross-check against the
    UNNAMED shadow timed out -- taking the whole run down with a
    traceback and throwing away its own useful output.

    Not every robot has an unnamed shadow. The cross-check exists only
    to spot two sources of schedHold disagreeing; losing it must not
    cost the tester the result they actually came for."""

    @contextlib.asynccontextmanager
    async def _connection(self, robot, report):
        yield robot, report

    def _robot(self, state_error=None):
        robot = AsyncMock()
        robot.get_settings.return_value = MagicMock(
            payload={"state": {"reported": {
                "childLock": False, "ecoCharge": False, "schedHold": False,
                "noAutoPasses": False, "vacHigh": False,
            }}}
        )
        if state_error:
            robot.get_state.side_effect = state_error
        else:
            robot.get_state.return_value = MagicMock(
                payload={"state": {"reported": {"schedHold": True}}}
            )
        return robot

    def _run(self, robot):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_settings_write as mod

        report = Report()
        with patch.object(mod, "connected_robot",
                          lambda *a, **k: self._connection(robot, report)):
            asyncio.run(mod.list_settings("u", "p", "US", "BLID"))
        return report

    def test_a_failing_cross_check_is_recorded_not_raised(self):
        from roombapy_prime.mqtt_client import ShadowError

        report = self._run(self._robot(state_error=ShadowError("No response to GET")))

        entry = next(r for r in report.results if "Cross-check" in r.name)
        assert entry.status == "SKIPPED"

    def test_the_five_settings_are_still_read_when_the_cross_check_fails(self):
        """The point of the fix: the primary result survives."""
        from roombapy_prime.mqtt_client import ShadowError

        robot = self._robot(state_error=ShadowError("No response to GET"))

        self._run(robot)

        robot.get_settings.assert_awaited_once()

    def test_a_working_cross_check_is_still_reported(self):
        report = self._run(self._robot())

        entry = next(r for r in report.results if "Cross-check" in r.name)
        assert entry.status == "OK"


class TestKnownIneffectiveWritesAreFlagged:
    """FIELD RESULT (DaRealGuGu, a25): all five settings wrote and read
    back successfully, but schedHold's schedule stayed active in the
    app afterwards -- the write is accepted and simply does not do
    anything.

    What makes that worth encoding rather than just noting: this
    project's own cross-check against the classic/unnamed shadow
    FLAGGED the divergence (rw-settings True, classic still False)
    BEFORE the app was checked, and the app then confirmed it. Two
    sources disagreeing meant "the write did not really take".

    Without a warning, a tester sees five green checkmarks and
    reasonably concludes it worked."""

    def test_sched_hold_is_marked_ineffective(self):
        from roombapy_prime_tools.verify_settings_write import (
            _WRITES_ACCEPTED_BUT_INEFFECTIVE,
        )

        assert "sched_hold" in _WRITES_ACCEPTED_BUT_INEFFECTIVE

    def test_the_confirmed_working_settings_are_not_marked(self):
        """child_lock is confirmed end to end -- app showed it and the
        robot announced it audibly. Marking it would be actively wrong."""
        from roombapy_prime_tools.verify_settings_write import (
            _WRITES_ACCEPTED_BUT_INEFFECTIVE,
        )

        assert "child_lock" not in _WRITES_ACCEPTED_BUT_INEFFECTIVE

    def test_every_marked_key_is_a_real_setting(self):
        """A typo here would silently warn about nothing."""
        from roombapy_prime_tools.verify_settings_write import (
            _TARGET_SETTINGS,
            _WRITES_ACCEPTED_BUT_INEFFECTIVE,
        )

        assert _WRITES_ACCEPTED_BUT_INEFFECTIVE <= set(_TARGET_SETTINGS)
