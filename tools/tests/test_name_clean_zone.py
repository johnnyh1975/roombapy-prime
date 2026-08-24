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

    assert await _read_zones(robot, "MAP-1", "VER-1") is None


class TestZoneNamesComeFromTheBundle:
    """@chairstacker's `--list-rooms` showed `name=None` for all eight
    of his zones while his app labelled them.

    The listing read `get_map_metadata` → `rooms_metadata`, which
    carries ROOM names. A zone's name is a `properties.name` on its
    feature in the bundle's `cleanZones` layer, and nothing looked
    there.

    `--dump-config` does not answer it either: its summary is
    deliberately depth-limited so real home layouts stay out of shared
    reports, and zone names sit exactly under the cutoff.
    """

    @staticmethod
    async def _names(layer):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = SimpleNamespace(
            get_map_geojson_link=AsyncMock(return_value="url"),
            download_map_bundle=AsyncMock(return_value=b""),
            parse_map_bundle=lambda _blob: SimpleNamespace(
                zone_layers={"cleanZones": layer}
            ),
        )
        return await _zone_names_from_bundle(robot, "MAP-1", "VER-1")

    @pytest.mark.asyncio
    async def test_a_named_zone_is_found(self):
        names = await self._names(
            {"features": [
                {"properties": {"id": "100", "name": "Guest Access Zone"}},
                {"properties": {"id": "101", "name": "Living Room @Wall"}},
            ]}
        )

        assert names == {
            "100": "Guest Access Zone",
            "101": "Living Room @Wall",
        }

    @pytest.mark.asyncio
    async def test_an_unnamed_zone_is_not_invented(self):
        """An empty name is not a name. `Zone {id}` is the honest
        answer, and claiming otherwise sends someone looking for a
        rename that will not help."""
        names = await self._names(
            {"features": [
                {"properties": {"id": "100"}},
                {"properties": {"id": "101", "name": ""}},
            ]}
        )

        assert names == {}

    @pytest.mark.asyncio
    async def test_an_unreadable_bundle_says_so(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = SimpleNamespace(
            get_map_geojson_link=AsyncMock(side_effect=RuntimeError("nope"))
        )

        assert await _zone_names_from_bundle(robot, "MAP-1", "VER-1") == {}


class TestABundleFileIsAFeatureCollection:
    """@chairstacker: "0 room feature(s) found across all map bundles"
    on a robot with seven named rooms.

    `_fetch_bundle_rooms` read `parsed["rooms"]` expecting a bare list
    and got the GeoJSON wrapper `{"type": ..., "features": [...]}`. The
    isinstance check failed and it moved on — silently, because
    `continue` looks exactly like an empty map.

    `borders` really is a bare feature; `rooms` and `cleanZones` are
    collections. Both shapes are accepted rather than assuming either.
    """

    @staticmethod
    def _rooms(parsed):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from roombapy_prime_tools import verify_map_edit

        robot = SimpleNamespace(
            get_map_geojson_link=AsyncMock(return_value={"map_url": "http://x"}),
            download_map_bundle=AsyncMock(return_value=b""),
        )
        versions = [SimpleNamespace(p2map_id="MAP-1", active_p2mapv_id="v1")]

        with patch.object(
            verify_map_edit, "parse_map_bundle", return_value=parsed
        ):
            return asyncio.run(
                verify_map_edit._fetch_bundle_rooms(robot, versions)
            )

    def test_a_feature_collection_is_read(self):
        rooms = self._rooms(
            {"rooms": {"type": "FeatureCollection", "features": [
                {"properties": {"id": "15", "name": "Room 1"}},
            ]}}
        )

        assert len(rooms) == 1

    def test_a_bare_list_still_works(self):
        rooms = self._rooms(
            {"rooms": [{"properties": {"id": "15", "name": "Room 1"}}]}
        )

        assert len(rooms) == 1

    def test_a_missing_rooms_file_is_survivable(self):
        assert self._rooms({}) == []


class TestAllThreeZoneLayersAreRead:
    """@chairstacker's bundle had five files -- borders, manifest,
    metadata, policyZones, rooms -- and no `cleanZones` at all.

    Reading only that one layer returned {} and the tool reported "no
    zone names in the map bundle", which was literally true about the
    search and thoroughly wrong about the data. He then noticed the same
    names appearing in calendar entries, which is how the claim came
    apart.
    """

    @staticmethod
    def _bundle(**layers):
        from unittest.mock import MagicMock

        bundle = MagicMock()
        bundle.zone_layers = {
            name: {"features": feats} for name, feats in layers.items()
        }
        return bundle

    @staticmethod
    async def _names(bundle):
        from unittest.mock import AsyncMock, MagicMock, patch

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = MagicMock()
        robot.get_map_geojson_link = AsyncMock(return_value="link")
        robot.download_map_bundle = AsyncMock(return_value=b"")
        robot.parse_map_bundle = MagicMock(return_value=bundle)
        with patch("builtins.print"):
            return await _zone_names_from_bundle(robot, "MAP", "V1")

    @pytest.mark.asyncio
    async def test_policy_zones_alone_still_yield_names(self):
        """The exact shape of the bundle that broke this."""
        bundle = self._bundle(
            policyZones=[
                {"properties": {"id": "100", "name": "Stairs",
                                "zone_type": "KeepOutZone"}},
            ],
        )

        names = await self._names(bundle)

        assert "100" in names

    @pytest.mark.asyncio
    async def test_ad_hoc_zones_are_read_too(self):
        bundle = self._bundle(
            adHocCleanZones=[{"properties": {"id": "200", "name": "Spill"}}],
        )

        names = await self._names(bundle)

        assert names["200"] == "Spill"

    @pytest.mark.asyncio
    async def test_a_clean_zone_name_is_left_alone(self):
        """The name is the name. Appending the layer to every entry
        turned data into noise on the common case."""
        bundle = self._bundle(
            cleanZones=[{"properties": {"id": "101", "name": "Living Room"}}],
        )

        names = await self._names(bundle)

        assert names["101"] == "Living Room"

    @pytest.mark.asyncio
    async def test_a_no_go_zone_says_so(self):
        """The one distinction worth surfacing: sending a cleaning
        command at a keep-out zone is the mistake to prevent."""
        bundle = self._bundle(
            policyZones=[
                {"properties": {"id": "300", "name": "Cables",
                                "zone_type": "KeepOutZone"}},
            ],
        )

        names = await self._names(bundle)

        assert "KeepOutZone" in names["300"]
        assert names["300"].startswith("Cables")
