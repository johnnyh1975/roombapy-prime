"""The zone-naming tool.

Renaming a zone is `SetPermanentAreasV1` carrying every zone on the
map, with one name changed -- there is no rename command. APK 3.0.0:
`updateCleanZones` reads `zone, id, name, geometry` per item, keeps
what is in `retainIds` and deletes the rest.

Rooms take a different path entirely (`setRoomMetadata`), which is why
@chairstacker can rename rooms and not zones.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _Zone(SimpleNamespace):
    pass


def _zones():
    return [
        _Zone(zone_id="100", name=None, geometry="G100"),
        _Zone(zone_id="101", name="Study", geometry="G101"),
        _Zone(zone_id="102", name=None, geometry="G102"),
    ]


@pytest.mark.asyncio
async def test_every_zone_is_resent_with_one_name_changed():
    """FULL REPLACE. Anything omitted is deleted, so the untouched
    zones have to go back exactly as they were."""
    from roombapy_prime_tools.name_clean_zone import _send_rename

    robot = SimpleNamespace(edit_map=AsyncMock(return_value={"ok": True}))
    zones = _zones()

    await _send_rename(robot, "MAP-1", zones, zones[0], "Office")

    (_p2map, command), _ = robot.edit_map.call_args
    sent = {a.area_id: a.name for a in command.areas}

    assert sent == {"100": "Office", "101": "Study", "102": ""}


@pytest.mark.asyncio
async def test_geometry_is_carried_unchanged():
    """Only the name differs. A zone resent with different geometry
    would move where the robot cleans."""
    from roombapy_prime_tools.name_clean_zone import _send_rename

    robot = SimpleNamespace(edit_map=AsyncMock(return_value={}))
    zones = _zones()

    await _send_rename(robot, "MAP-1", zones, zones[1], "Den")

    (_p2map, command), _ = robot.edit_map.call_args

    assert [a.geometry for a in command.areas] == ["G100", "G101", "G102"]


@pytest.mark.asyncio
async def test_an_unreadable_bundle_sends_nothing():
    """A partial list deletes the zones it does not carry, so failing
    to read the current set has to stop the operation."""
    from roombapy_prime_tools.name_clean_zone import _read_zones

    robot = SimpleNamespace(
        get_map_geojson_link=AsyncMock(side_effect=RuntimeError("nope"))
    )

    assert await _read_zones(robot, "MAP-1") is None
