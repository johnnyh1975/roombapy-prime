"""Tests for the testable parts of verify_map_edit.py -- _pick_test_room()'s
logic, completely mocked (no real robot, no real network). The actual
purpose of the script (editing a real room name on a real device) is by
nature not automatable to test -- see the module docstring, and
verify_mission_commands.py's own test file for the same reasoning.

SOURCE SWITCH (this session): room data now comes exclusively from the
downloaded map bundle (RoomFeature), not from get_active_map_versions().
A full APK decompilation confirmed the app itself never reads room
names from that endpoint at any level of richness -- see
_fetch_bundle_rooms()'s docstring in verify_map_edit.py for the full
evidence trail. These tests replace the old get_active_map_versions()
-shaped SimpleNamespace helpers with RoomFeature/RoomFeatureProperties
directly."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from roombapy_prime.models import Polygon, RoomCategory, RoomFeature, RoomFeatureProperties
from roombapy_prime.verify_map_edit import (
    _TEST_SUFFIX,
    _fetch_bundle_rooms,
    _pick_test_room,
    _pick_test_room_with_category,
)


def _room(room_id: str, name: str | None, room_type: object = None) -> RoomFeature:
    return RoomFeature(
        feature_id=room_id,
        geometry=Polygon(coordinates=[]),
        properties=RoomFeatureProperties(name=name, room_type=room_type),
    )


# =========================================================================
# _pick_test_room()
# =========================================================================


def test_pick_test_room_returns_first_named_room() -> None:
    rooms = [("map1", _room("r1", None)), ("map1", _room("r2", "Kitchen")), ("map1", _room("r3", "Bedroom"))]

    assert _pick_test_room(rooms) == ("map1", "r2", "Kitchen")


def test_pick_test_room_returns_none_when_no_room_has_a_name() -> None:
    """Deliberate safety property (see the module docstring): a room
    without a name is never picked, since there'd be no confirmed way
    to revert it back to "no name" afterward."""
    rooms = [("map1", _room("r1", None)), ("map1", _room("r2", None))]

    assert _pick_test_room(rooms) is None


def test_pick_test_room_returns_none_for_empty_input() -> None:
    assert _pick_test_room([]) is None


def test_pick_test_room_searches_across_multiple_map_ids() -> None:
    rooms = [("map1", _room("r1", None)), ("map2", _room("r2", None)), ("map2", _room("r3", "Living Room"))]

    assert _pick_test_room(rooms) == ("map2", "r3", "Living Room")


def test_pick_test_room_ignores_room_with_name_but_missing_id() -> None:
    """Defensive: a malformed/empty feature_id (RoomFeature.from_json()
    defaults to "" when GeoJSON "id" is absent) must not be picked --
    an id is needed to actually send the rename command."""
    rooms = [("map1", _room("", "Kitchen")), ("map1", _room("r2", "Bedroom"))]

    assert _pick_test_room(rooms) == ("map1", "r2", "Bedroom")


def test_test_suffix_is_clearly_identifiable() -> None:
    assert "roombapy-prime-test" in _TEST_SUFFIX


# =========================================================================
# _pick_test_room_with_category()
# =========================================================================


def test_pick_test_room_with_category_finds_matching_room() -> None:
    rooms = [("MAP1", _room("10", "Kitchen", room_type="kitchen"))]

    assert _pick_test_room_with_category(rooms) == ("MAP1", "10", "Kitchen", RoomCategory.KITCHEN)


def test_pick_test_room_with_category_skips_room_with_no_category() -> None:
    """A room with a name but no category at all must be skipped --
    this test needs a known-good original value to revert to."""
    rooms = [("MAP1", _room("11", "No Category Yet", room_type=None))]

    assert _pick_test_room_with_category(rooms) is None


def test_pick_test_room_with_category_skips_unparseable_room_type() -> None:
    """THE core new safety property (this session): RoomFeatureProperties
    .room_type is only confirmed BY NAME, not by value space -- a raw
    value that doesn't parse as a RoomCategory must be skipped, not
    guessed at, since a wrong guess would write back the wrong category
    on revert. See _pick_test_room_with_category()'s own docstring."""
    rooms = [
        ("MAP1", _room("12", "Mystery Room", room_type="some_unmapped_value")),
        ("MAP1", _room("13", "Bathroom", room_type="bathroom")),
    ]

    assert _pick_test_room_with_category(rooms) == ("MAP1", "13", "Bathroom", RoomCategory.BATHROOM)


# =========================================================================
# _fetch_bundle_rooms()
# =========================================================================


def _map_version(p2map_id: str, p2mapv_id: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(p2map_id=p2map_id, active_p2mapv_id=p2mapv_id)


@pytest.mark.asyncio
async def test_fetch_bundle_rooms_parses_real_shaped_geojson() -> None:
    """Core happy-path test: a realistic bundle "rooms" file (bare list
    of GeoJSON Features, per the confirmed RoomFeature wire shape) is
    downloaded, parsed, and converted into typed RoomFeature objects."""
    robot = AsyncMock()
    robot.get_map_geojson_link.return_value = {"map_url": "https://example.com/bundle.tgz"}
    robot.download_map_bundle.return_value = b"fake-bundle-bytes"

    import roombapy_prime.verify_map_edit as vme

    vme.parse_map_bundle = lambda _bytes: {  # type: ignore[assignment]
        "rooms": [
            {"type": "Feature", "id": "10", "geometry": {}, "properties": {"name": "Living Room", "type": "living_room"}},
            {"type": "Feature", "id": "11", "geometry": {}, "properties": {"name": "Kitchen"}},
        ]
    }

    result = await _fetch_bundle_rooms(robot, [_map_version("MAP1", "v1")])

    assert len(result) == 2
    assert result[0][0] == "MAP1"
    assert result[0][1].properties.name == "Living Room"
    assert result[0][1].feature_id == "10"
    assert result[1][1].properties.name == "Kitchen"


@pytest.mark.asyncio
async def test_fetch_bundle_rooms_skips_map_version_without_ids() -> None:
    robot = AsyncMock()

    result = await _fetch_bundle_rooms(robot, [_map_version(None, None)])

    assert result == []
    robot.get_map_geojson_link.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_bundle_rooms_survives_a_failed_download() -> None:
    """One map version's bundle failing to download must not abort the
    whole investigation -- same defensive pattern as the rest of this
    project's diagnostic scripts."""
    robot = AsyncMock()
    robot.get_map_geojson_link.side_effect = RuntimeError("network blip")

    result = await _fetch_bundle_rooms(robot, [_map_version("MAP1", "v1")])

    assert result == []
