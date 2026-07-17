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


# =========================================================================
# _room_names_from_bundle() (session 44)
# =========================================================================


def test_room_names_from_bundle_extracts_non_geometry_fields() -> None:
    from roombapy_prime.verify_map_edit import _room_names_from_bundle

    parsed_bundle = {
        "rooms": [
            {"room_id": "r1", "name": "Kitchen", "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]}},
            {"room_id": "r2", "name": "Bedroom", "simplified_geometry": {"huge": "data"}},
        ],
        "borders": [{"geometry": "should never be reached anyway"}],
    }

    result = _room_names_from_bundle(parsed_bundle)

    assert result == [{"room_id": "r1", "name": "Kitchen"}, {"room_id": "r2", "name": "Bedroom"}]


def test_room_names_from_bundle_never_leaks_geometry_keys() -> None:
    """Regression test against this function's core privacy property
    (see its docstring): a floor plan is more personal than most other
    data this project captures -- geometry/polygon/coordinate fields
    must never appear in the result, under any key-name casing."""
    from roombapy_prime.verify_map_edit import _room_names_from_bundle

    parsed_bundle = {
        "rooms": [
            {
                "room_id": "r1",
                "name": "Living Room",
                "Geometry": "x",
                "POLYGON": "y",
                "Coordinates": "z",
                "poly": "w",
                "simplifiedGeometry": "v",
            }
        ]
    }

    result = _room_names_from_bundle(parsed_bundle)

    assert result == [{"room_id": "r1", "name": "Living Room"}]
    for entry in result:
        for key in entry:
            assert "geo" not in key.lower()
            assert "poly" not in key.lower()
            assert "coord" not in key.lower()


def test_room_names_from_bundle_returns_empty_when_no_rooms_file() -> None:
    from roombapy_prime.verify_map_edit import _room_names_from_bundle

    assert _room_names_from_bundle({"borders": [], "hazard": []}) == []
    assert _room_names_from_bundle({}) == []


def test_room_names_from_bundle_defensive_against_malformed_entries() -> None:
    """A rooms file entry that isn't a dict (unexpected shape) must be
    skipped, not crash the whole investigation."""
    from roombapy_prime.verify_map_edit import _room_names_from_bundle

    parsed_bundle = {"rooms": ["not-a-dict", {"room_id": "r1", "name": "Office"}, None]}

    result = _room_names_from_bundle(parsed_bundle)

    assert result == [{"room_id": "r1", "name": "Office"}]


# =========================================================================
# Regression: raw dict -> parse_active_map_versions() -> _pick_test_room()
# (this session, real capture from jadestar1864)
# =========================================================================


def test_pick_test_room_finds_name_through_the_real_parsing_pipeline() -> None:
    """BUG FIX regression test. _pick_test_room()'s own unit tests above
    always passed -- they use SimpleNamespace helpers with an idealized,
    flat `name` attribute that never matched what
    robot.get_active_map_versions() actually returns: a raw dict with
    the name nested under "room_metadata" (see prime_robot.py's own
    `-> list[dict]` type hint). run() was previously passing that raw
    list straight into _pick_test_room(), which used getattr() on it --
    silently returning None for every field, on every dict, always.

    This is the exact shape from jadestar1864's real
    get_active_map_versions() capture (BLID/robot_id redacted, room
    names are ones they explicitly set for testing, not their real
    room names)."""
    from roombapy_prime.models import parse_active_map_versions
    from roombapy_prime.verify_map_edit import _pick_test_room

    raw_response = [
        {
            "p2map_id": "2FB160A17D4ECE04A0FD062EDF4CB51D-1783319305",
            "entity_type": "p2map",
            "sku": "G185020",
            "active_p2mapv_id": "260717T144012.314",
            "state": "active",
            "visible": True,
            "name": "Main Room",
            "rooms_metadata": [
                {"room_id": "10", "room_metadata": {"name": "Living Room", "last_operating_mode": 32}},
                {"room_id": "11", "room_metadata": {"name": "Kitchen"}},
                {"room_id": "12", "room_metadata": {"name": "Entryway"}},
            ],
        }
    ]

    typed_versions = parse_active_map_versions(raw_response)
    result = _pick_test_room(typed_versions)

    assert result == ("2FB160A17D4ECE04A0FD062EDF4CB51D-1783319305", "10", "Living Room")


def test_pick_test_room_returns_none_on_raw_unparsed_dicts() -> None:
    """The failure mode this bug actually produced: feeding the RAW
    list[dict] (no parse_active_map_versions() call) into
    _pick_test_room() must not crash, but it also can never find a
    name -- getattr() on a dict always returns the default. Documents
    the exact wrong behavior run() used to have, so a future refactor
    that accidentally drops the parse_active_map_versions() call gets
    caught by more than just an unlucky live test run."""
    from roombapy_prime.verify_map_edit import _pick_test_room

    raw_response = [
        {
            "p2map_id": "map1",
            "rooms_metadata": [{"room_id": "10", "room_metadata": {"name": "Living Room"}}],
        }
    ]

    assert _pick_test_room(raw_response) is None
