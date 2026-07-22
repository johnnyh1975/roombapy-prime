"""Tests for the testable parts of verify_schedule_write.py --
_build_disabled_schedules()'s core logic. The actual purpose of the
script (writing a real schedule change to a real account) is by
nature not automatable to test -- that's the whole point of the
staged-risk approach described in its own module docstring."""

from __future__ import annotations

from roombapy_prime.models.schedules_dnd import HouseholdSchedule
from roombapy_prime.verify_schedule_write import _build_disabled_schedules


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
