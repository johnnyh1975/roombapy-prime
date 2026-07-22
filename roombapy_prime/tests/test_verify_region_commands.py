"""Tests for the testable parts of verify_region_commands.py --
_is_safe_command_def()'s TID-detection logic and _region_types()'s
tolerance for both typed Region objects and raw dicts. The actual
purpose of the script (sending a real region command to a real
device) is by nature not automatable to test -- that's the whole
point of the staged-risk approach described in its own module
docstring."""

from __future__ import annotations

from unittest.mock import MagicMock

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
