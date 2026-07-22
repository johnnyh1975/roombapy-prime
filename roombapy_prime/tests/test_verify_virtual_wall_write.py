"""Tests for the testable parts of verify_virtual_wall_write.py --
_fetch_current_walls()'s full pipeline (mocked robot/bundle download,
real parsing/categorization logic). The actual purpose of the script
(writing a real virtual-wall change to a real device) is by nature
not automatable to test -- that's the whole point of the staged-risk
approach described in its own module docstring."""

from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from roombapy_prime.verify_virtual_wall_write import _fetch_current_walls


def _make_bundle_bytes(policy_zones: dict) -> bytes:
    """Builds a real, valid tar.gz bundle containing just a
    policyZones.json entry -- matching parse_map_bundle()'s own
    real extraction logic, not a mock of it."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = json.dumps(policy_zones).encode("utf-8")
        info = tarfile.TarInfo(name="policyZones.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_fetch_current_walls_full_pipeline_with_mocked_bundle_download():
    """Exercises the REAL parse_map_bundle() + PolicyZoneFeature +
    policy_zones_to_virtual_walls() pipeline end to end, only mocking
    the network-facing robot calls -- not the parsing/categorization
    logic itself, which is the actual thing this script depends on
    being correct."""
    policy_zones = {
        "features": [
            {
                "id": "kz1",
                "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]]},
                "properties": {"type": "KeepOutZone"},
            },
            {
                "id": "vw1",
                "geometry": {"type": "LineString", "coordinates": [[2.0, 2.0], [3.0, 3.0]]},
                "properties": {"type": "KeepOutZone"},
            },
        ]
    }
    robot = MagicMock()
    robot.get_map_geojson_link = AsyncMock(return_value={"map_url": "https://example.invalid/bundle.tar.gz"})
    robot.download_map_bundle = AsyncMock(return_value=_make_bundle_bytes(policy_zones))

    features, walls = await _fetch_current_walls(robot, "MAP1", "V1")

    assert len(features) == 2
    assert len(walls) == 2
    robot.get_map_geojson_link.assert_awaited_once_with("MAP1", "V1")
    robot.download_map_bundle.assert_awaited_once_with("https://example.invalid/bundle.tar.gz")


@pytest.mark.asyncio
async def test_fetch_current_walls_returns_empty_when_no_policy_zones_in_bundle():
    """A map with no policyZones file at all (never configured any
    zones/walls) must return empty lists, not raise."""
    robot = MagicMock()
    robot.get_map_geojson_link = AsyncMock(return_value={"map_url": "https://example.invalid/bundle.tar.gz"})

    # A genuinely empty/invalid archive -- parse_map_bundle() itself
    # is expected to handle this gracefully (returns {}), not this
    # function's own responsibility to special-case.
    empty_buf = io.BytesIO()
    with tarfile.open(fileobj=empty_buf, mode="w:gz"):
        pass
    robot.download_map_bundle = AsyncMock(return_value=empty_buf.getvalue())

    features, walls = await _fetch_current_walls(robot, "MAP1", "V1")

    assert features == []
    assert walls == []
