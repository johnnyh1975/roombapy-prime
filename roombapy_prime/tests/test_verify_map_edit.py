"""Tests for the testable parts of verify_map_edit.py -- _pick_test_room()'s
logic, completely mocked (no real robot, no real network). The actual
purpose of the script (editing a real room name on a real device) is by
nature not automatable to test -- see the module docstring, and
verify_mission_commands.py's own test file for the same reasoning."""

from __future__ import annotations

from types import SimpleNamespace

from roombapy_prime.verify_map_edit import _TEST_SUFFIX, _pick_test_room


def _room(room_id: str, name: str | None) -> SimpleNamespace:
    return SimpleNamespace(room_id=room_id, name=name)


def _map_version(p2map_id: str, rooms: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(p2map_id=p2map_id, rooms_metadata=rooms)


def test_pick_test_room_returns_first_named_room() -> None:
    versions = [_map_version("map1", [_room("r1", None), _room("r2", "Kitchen"), _room("r3", "Bedroom")])]

    result = _pick_test_room(versions)

    assert result == ("map1", "r2", "Kitchen")


def test_pick_test_room_returns_none_when_no_room_has_a_name() -> None:
    """Deliberate safety property (see the module docstring): a room
    without a name is never picked, since there'd be no confirmed way
    to revert it back to "no name" afterward."""
    versions = [_map_version("map1", [_room("r1", None), _room("r2", None)])]

    assert _pick_test_room(versions) is None


def test_pick_test_room_returns_none_for_empty_input() -> None:
    assert _pick_test_room([]) is None
    assert _pick_test_room([_map_version("map1", [])]) is None


def test_pick_test_room_searches_across_multiple_map_versions() -> None:
    versions = [
        _map_version("map1", [_room("r1", None)]),
        _map_version("map2", [_room("r2", None), _room("r3", "Living Room")]),
    ]

    result = _pick_test_room(versions)

    assert result == ("map2", "r3", "Living Room")


def test_pick_test_room_ignores_room_with_name_but_missing_id() -> None:
    """Defensive: a malformed/partial room entry (name present, id
    missing) must not be picked -- room_id is needed to actually send
    the rename command."""
    versions = [_map_version("map1", [_room(None, "Kitchen"), _room("r2", "Bedroom")])]

    result = _pick_test_room(versions)

    assert result == ("map1", "r2", "Bedroom")


def test_test_suffix_is_clearly_identifiable() -> None:
    """Not a deep test, just a guard against an accidental change to
    the marker that makes the temporary test name recognizable to a
    person looking at their real app."""
    assert "roombapy-prime-test" in _TEST_SUFFIX
