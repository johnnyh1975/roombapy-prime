"""Tests for the testable parts of verify_schedule_write.py --
_build_disabled_schedules()'s core logic. The actual purpose of the
script (writing a real schedule change to a real account) is by
nature not automatable to test -- that's the whole point of the
staged-risk approach described in its own module docstring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncio
import contextlib

import dataclasses

from roombapy_prime.models.schedules_dnd import HouseholdSchedule
from roombapy_prime_tools.verify_schedule_write import _build_disabled_schedules


def _make_schedule(schedule_id: str, name: str, enabled: bool) -> HouseholdSchedule:
    return HouseholdSchedule.from_json(
        {"schedule_id": schedule_id, "options": {"name": name, "enabled": enabled}}
    )


def test_build_disabled_schedules_actually_executes_without_crashing():
    """Directly exercises _build_disabled_schedules() end-to-end
    against real HouseholdSchedule/ScheduleOptions instances -- same
    lesson as verify_region_commands.py's own
    test_build_modified_command_actually_executes_without_crashing():
    an executing test catches real construction bugs a syntax check
    alone cannot."""
    schedules = [
        _make_schedule("s1", "Morning", enabled=True),
        _make_schedule("s2", "Evening", enabled=True),
    ]

    new_schedules, was_enabled = _build_disabled_schedules(schedules, schedule_index=0)

    assert was_enabled is True
    assert new_schedules[0].options.enabled is False
    assert new_schedules[0].options.name == "Morning"  # untouched
    # the OTHER schedule in the list must be completely unaffected.
    assert new_schedules[1].options.enabled is True
    assert new_schedules[1] is schedules[1]


def test_build_disabled_schedules_does_not_mutate_the_input_list():
    """A caller might reasonably expect the original list to be safe
    to reuse (e.g. for a "what changed" diff) -- this must not be a
    silent in-place mutation."""
    schedules = [_make_schedule("s1", "Morning", enabled=True)]

    _build_disabled_schedules(schedules, schedule_index=0)

    assert schedules[0].options.enabled is True


class TestBuildDisabledSchedules:
    """The one piece of this script that transforms real user data
    before sending it back. Getting it wrong would not error -- it
    would quietly send a schedule set that differs from what the user
    actually has, which is precisely the failure mode a write test
    exists to rule out.

    Uses real dataclasses rather than mocks on purpose: the function
    relies on dataclasses.replace(), so a MagicMock would sail through
    while telling us nothing about whether it works."""

    @dataclasses.dataclass(frozen=True)
    class _Options:
        enabled: bool

    @dataclasses.dataclass(frozen=True)
    class _Schedule:
        name: str
        options: TestBuildDisabledSchedules._Options

    def _schedules(self, *enabled_flags):
        return [
            self._Schedule(name=f"s{i}", options=self._Options(enabled=flag))
            for i, flag in enumerate(enabled_flags)
        ]

    def test_only_the_targeted_schedule_is_disabled(self):
        from roombapy_prime_tools.verify_schedule_write import _build_disabled_schedules

        result, _ = _build_disabled_schedules(self._schedules(True, True, True), 1)

        assert [s.options.enabled for s in result] == [True, False, True]

    def test_the_input_list_is_not_mutated(self):
        """Documented promise -- and it matters: the caller still needs
        the original to report what changed, and to put it back."""
        from roombapy_prime_tools.verify_schedule_write import _build_disabled_schedules

        originals = self._schedules(True, True)
        _build_disabled_schedules(originals, 0)

        assert [s.options.enabled for s in originals] == [True, True]

    def test_reports_whether_the_target_had_been_enabled(self):
        from roombapy_prime_tools.verify_schedule_write import _build_disabled_schedules

        _, was_enabled = _build_disabled_schedules(self._schedules(True), 0)
        assert was_enabled is True

        _, was_enabled = _build_disabled_schedules(self._schedules(False), 0)
        assert was_enabled is False

    def test_an_already_disabled_target_stays_disabled(self):
        from roombapy_prime_tools.verify_schedule_write import _build_disabled_schedules

        result, _ = _build_disabled_schedules(self._schedules(False), 0)

        assert result[0].options.enabled is False

    def test_the_full_list_is_returned_not_just_the_changed_entry(self):
        """The API takes the complete list as the new truth -- sending
        back fewer schedules than exist would DELETE the missing ones."""
        from roombapy_prime_tools.verify_schedule_write import _build_disabled_schedules

        result, _ = _build_disabled_schedules(self._schedules(True, True, True, True), 2)

        assert len(result) == 4


class TestScheduleWriteFlowControl:
    """The orchestration around the schedule writes.

    A schedule is not a value in a database -- it is when a robot runs
    in someone's home. Sending a wrong or incomplete list does not throw;
    it silently reschedules or deletes cleaning runs, and the owner finds
    out days later when the robot does or does not appear.

    So these test the refusals: no household, no schedules, declined
    confirmation. In each case nothing may be sent.
    """

    @contextlib.asynccontextmanager
    async def _connection(self, robot, report):
        yield robot, report

    def _run(self, func_name, robot, *, confirm_answer=True, **kwargs):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_schedule_write as mod

        report = Report()
        with patch.object(mod, "connected_robot",
                          lambda *a, **k: self._connection(robot, report)), \
             patch.object(mod, "confirm", return_value=confirm_answer):
            asyncio.run(getattr(mod, func_name)("u", "p", "US", "BLID", **kwargs))
        return report

    def _robot(self, households=None, schedules=None):
        robot = AsyncMock()
        robot.get_households.return_value = households if households is not None else [
            {"household_id": "HH1"}
        ]
        robot.get_schedules.return_value = schedules if schedules is not None else []
        return robot

    def test_no_household_means_nothing_is_sent(self):
        """Without a household id there is nothing to address -- and
        guessing one would write to someone else's schedule."""
        robot = self._robot(households=[])

        self._run("send_update_unchanged", robot, household_schedule_id="HS1")

        robot.update_schedules.assert_not_awaited()

    def test_no_schedules_means_nothing_is_sent(self):
        """Sending an empty list is not a no-op here: the API treats the
        list as the new truth, so it would DELETE every schedule."""
        robot = self._robot(schedules=[])

        self._run("send_update_unchanged", robot, household_schedule_id="HS1")

        robot.update_schedules.assert_not_awaited()

    def test_declining_the_confirmation_sends_nothing(self):
        robot = self._robot()

        self._run("send_update_unchanged", robot, confirm_answer=False, household_schedule_id="HS1")

        robot.update_schedules.assert_not_awaited()

    def test_list_schedules_never_writes(self):
        """The stage-0 guarantee: a tester starting here must be certain
        it cannot change anything."""
        robot = self._robot()

        self._run("list_schedules", robot)

        robot.update_schedules.assert_not_awaited()
