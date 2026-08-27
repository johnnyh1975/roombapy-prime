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

# Loaded by path: two conftest files exist in this repo, so a plain
# `from conftest import` resolves to whichever pytest imported first,
# and `tools.tests.conftest` only works from the repo root.
import importlib.util as _ilu
from pathlib import Path as _P
_spec = _ilu.spec_from_file_location(
    "_bundle_conftest", _P(__file__).resolve().parent / "conftest.py"
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_make_bundle = _mod.make_bundle_bytes



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
            # REAL BYTES, read by the REAL parser. This used to mock
            # `parse_map_bundle` as a robot method -- an attribute
            # `PrimeRobot` does not have -- so the mock agreed with a
            # call site that could only ever raise, and hid an
            # AttributeError behind a passing test.
            download_map_bundle=AsyncMock(
                return_value=_make_bundle({"cleanZones.geojson": layer})
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

        # None, NOT {}: "the read failed" and "the read found no names"
        # are different findings. Returning {} for both is what let the
        # caller print its no-names conclusion after a read that threw.
        assert await _zone_names_from_bundle(robot, "MAP-1", "VER-1") is None


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
        """A PLAIN DICT, which is what `parse_map_bundle` returns.

        The first version of this helper built a MagicMock with a
        `zone_layers` attribute, mirroring the production code's
        `getattr(bundle, "zone_layers", None)`. Both were wrong in the
        same way, so the tests passed and the tool still returned
        nothing on a real bundle.

        A mock that agrees with the code proves the code agrees with
        itself. `parse_map_bundle` returns {filename_without_extension:
        content} and never had a `zone_layers` attribute at all.
        """
        return {name: {"features": feats} for name, feats in layers.items()}

    @staticmethod
    async def _names(bundle):
        from unittest.mock import AsyncMock, MagicMock, patch

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = MagicMock()
        robot.get_map_geojson_link = AsyncMock(return_value="link")
        robot.download_map_bundle = AsyncMock(return_value=b"")
        # NO parse_map_bundle MOCK. It is a module function, not a
        # robot method -- mocking it here is what let an
        # AttributeError ship green. The download returns real bytes
        # and the real parser reads them.
        robot.download_map_bundle = AsyncMock(
            return_value=_make_bundle(bundle)
        )
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


class TestAgainstTheConfirmedFeatureShape:
    """Built from the field names confirmed in `map_bundle.py`, not from
    what the reading code happens to expect.

    Every earlier test here constructed its fixture to match the parser.
    That is why a typo survived: `getattr(bundle, "zone_layers")` on a
    dict returns None, so the function returned {} on every real bundle
    while three tests agreed it worked.

    A `CleanZoneFeature` carries `id` beside `geometry` and `status`,
    with `name` inside `properties` -- that pairing is what makes a
    clean zone nameable. A `PolicyZoneFeature`'s properties carry
    `zone_type` and no name at all.
    """

    @staticmethod
    async def _names(bundle):
        from unittest.mock import AsyncMock, MagicMock, patch

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = MagicMock()
        robot.get_map_geojson_link = AsyncMock(return_value="url")
        robot.download_map_bundle = AsyncMock(return_value=b"")
        # NO parse_map_bundle MOCK. It is a module function, not a
        # robot method -- mocking it here is what let an
        # AttributeError ship green. The download returns real bytes
        # and the real parser reads them.
        robot.download_map_bundle = AsyncMock(
            return_value=_make_bundle(bundle)
        )
        with patch("builtins.print"):
            return await _zone_names_from_bundle(robot, "MAP", "VER")

    @pytest.mark.asyncio
    async def test_a_clean_zone_uses_feature_level_id(self):
        """`id` sits on the feature, `name` inside properties. Reading
        both from properties would find nothing."""
        names = await self._names({
            "cleanZones": {"features": [{
                "type": "Feature",
                "id": "101",
                "geometry": {"type": "Polygon", "coordinates": [[]]},
                "properties": {"name": "Kitchen", "status": "active"},
            }]},
        })

        assert names == {"101": "Kitchen"}

    @pytest.mark.asyncio
    async def test_a_policy_zone_has_no_name_to_find(self):
        """Its properties carry `zone_type` and nothing else useful.
        Finding no name here is correct, not a failure."""
        names = await self._names({
            "policyZones": {"features": [{
                "type": "Feature",
                "id": "200",
                "geometry": {"type": "Polygon", "coordinates": [[]]},
                "properties": {"zone_type": "KeepOutZone"},
            }]},
        })

        assert names == {}

    @staticmethod
    async def _names_from_blob(blob, capsys):
        """Feed arbitrary bytes through the real parser."""
        from unittest.mock import AsyncMock, MagicMock

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = MagicMock()
        robot.get_map_geojson_link = AsyncMock(return_value="url")
        robot.download_map_bundle = AsyncMock(return_value=blob)
        names = await _zone_names_from_bundle(robot, "MAP", "VER")
        return names, capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_a_non_bundle_blob_is_a_failure_not_an_absence(self, capsys):
        """WAS a test that the reader ignores an object carrying the
        old `zone_layers` attribute. That attribute is gone, and the
        reader now parses real bytes -- so the case that remains is
        bytes that are not a bundle, which must report as a FAILED read
        rather than as "no names found"."""
        names, out = await self._names_from_blob(b"not a tar.gz", capsys)

        assert names is None
        assert "unreadable" in out


class TestTheBundleContentsAreNamed:
    """A tool that reports "nothing found" without saying where it
    looked cannot be checked from outside.

    That is how `getattr(bundle, "zone_layers")` survived as a claim
    about the data: it returned {} on every bundle, the message said
    the names were stored nowhere readable, and nobody could see that
    the search had not happened. Bundle contents also vary per map, so
    the file list is the first thing worth knowing when the answer is
    empty.
    """

    @staticmethod
    async def _run(bundle, capsys):
        from unittest.mock import AsyncMock, MagicMock

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = MagicMock()
        robot.get_map_geojson_link = AsyncMock(return_value="url")
        robot.download_map_bundle = AsyncMock(return_value=b"")
        # NO parse_map_bundle MOCK. It is a module function, not a
        # robot method -- mocking it here is what let an
        # AttributeError ship green. The download returns real bytes
        # and the real parser reads them.
        robot.download_map_bundle = AsyncMock(
            return_value=_make_bundle(bundle)
        )
        await _zone_names_from_bundle(robot, "MAP", "VER")
        return capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_the_file_list_is_printed(self, capsys):
        """Built from @chairstacker's actual bundle: five files, and no
        cleanZones among them."""
        out = await self._run({
            "borders": {}, "manifest": {}, "metadata": {},
            "policyZones": {"features": []}, "rooms": {},
        }, capsys)

        assert "policyZones" in out
        assert "cleanZones" not in out

    @pytest.mark.asyncio
    async def test_an_empty_bundle_says_so(self, capsys):
        out = await self._run({}, capsys)

        assert "nothing" in out


class TestTheGeojsonLinkIsADict:
    """@chairstacker (#64) on b15: `map bundle unreadable: Constructor
    parameter should be str`.

    `get_map_geojson_link` returns the whole response dict;
    `download_map_bundle` wants the URL string out of it. Passing the
    dict raised that message from yarl, which reads like a type bug in
    the library rather than a mistake at the call site.

    `verify_map_edit.py` has extracted the URL correctly all along.
    Two implementations of the same three lines, one of them wrong —
    and the wrong one was the one his zone-name question depended on.
    """

    @staticmethod
    async def _names(link_value, capsys):
        from unittest.mock import AsyncMock, MagicMock

        from roombapy_prime_tools.verify_region_commands import (
            _zone_names_from_bundle,
        )

        robot = MagicMock()
        robot.get_map_geojson_link = AsyncMock(return_value=link_value)

        async def _download(url):
            assert isinstance(url, str), f"got {type(url).__name__}"
            return b""

        robot.download_map_bundle = AsyncMock(side_effect=_download)
        robot.download_map_bundle = AsyncMock(return_value=_make_bundle({
            "cleanZones.geojson": {"features": [
                {"id": "101", "properties": {"name": "Kitchen"}},
            ]},
        }))
        names = await _zone_names_from_bundle(robot, "MAP", "VER")
        return names, capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_a_dict_response_still_finds_the_url(self, capsys):
        names, _ = await self._names(
            {"url": "https://example.invalid/bundle.tar.gz", "expires": 900},
            capsys,
        )

        assert names == {"101": "Kitchen"}

    @pytest.mark.asyncio
    async def test_a_plain_string_still_works(self, capsys):
        names, _ = await self._names("https://example.invalid/b.tar.gz", capsys)

        assert names == {"101": "Kitchen"}

    @pytest.mark.asyncio
    async def test_a_response_without_a_url_says_so(self, capsys):
        """Rather than passing None down and failing further in."""
        names, out = await self._names({"expires": 900}, capsys)

        assert names == {}
        assert "no download URL" in out


class TestTheSnapshotLagsInBothDirections:
    """@chairstacker's map showed `rooms_metadata` lagging each way at
    the same time, and the tool handled one case and not the other.

    Zone 107, deleted in the app, stayed in the snapshot and lost only
    its bundle name — so it printed as an unnamed zone that still
    exists.

    Zone 109, created in the app, had a name in the bundle and no
    snapshot entry — so it printed not at all. The proof was in the
    tool's own output: **nine zone names read from the bundle, eight
    printed.**

    The version endpoint is the honest source for which regions exist,
    and it was being compared in one direction only.
    """

    @staticmethod
    def _leftovers(version_ids, zone_names, known_ids):
        """Calls the real function.

        A FIRST VERSION REIMPLEMENTED IT HERE, which would have proved
        the test agrees with itself -- the same mistake that let a
        broken bundle reader ship green three times. The computation was
        extracted so it could be called instead.
        """
        from roombapy_prime_tools.verify_region_commands import (
            _regions_not_in_snapshot,
        )

        return _regions_not_in_snapshot(version_ids, zone_names, known_ids)

    def test_a_bundle_name_without_a_snapshot_entry_is_listed(self):
        """Zone 109: named in the bundle, absent from the snapshot."""
        extra = self._leftovers(
            version_ids=[], zone_names={"109": "Testing Zone 13b"},
            known_ids={"108"},
        )

        assert extra == ["109"]

    def test_a_version_id_without_a_snapshot_entry_still_works(self):
        """The case that already worked must keep working."""
        extra = self._leftovers(
            version_ids=["109"], zone_names={}, known_ids={"108"},
        )

        assert extra == ["109"]

    def test_a_region_in_both_is_not_listed_twice(self):
        extra = self._leftovers(
            version_ids=["109"], zone_names={"109": "Testing Zone 13b"},
            known_ids=set(),
        )

        assert extra == ["109"]

    def test_a_region_already_printed_is_not_repeated(self):
        extra = self._leftovers(
            version_ids=["108"], zone_names={"108": "Testing Zone 12a"},
            known_ids={"108"},
        )

        assert extra == []
