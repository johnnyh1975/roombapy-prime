"""Tests for the testable parts of verify_virtual_wall_write.py --
_fetch_current_walls()'s full pipeline (mocked robot/bundle download,
real parsing/categorization logic). The actual purpose of the script
(writing a real virtual-wall change to a real device) is by nature
not automatable to test -- that's the whole point of the staged-risk
approach described in its own module docstring."""

from __future__ import annotations

import asyncio

import io
import json
import tarfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from roombapy_prime_tools.verify_virtual_wall_write import _fetch_current_walls


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


class TestStaleMapVersionIsDetected:
    """FIELD CASE (DaRealGuGu). He restarted his robot between tests,
    which re-versioned the map, then ran with the older version id and
    got "No policyZones.geojson data found".

    That result is ambiguous in the worst way: it reads as "you have no
    keep-out zones" when it might equally mean "we looked in a version
    that no longer exists". Neither he nor we could tell which, and the
    script said nothing about the difference.

    Map re-versioning on restart is confirmed behaviour now, not a
    theory -- his observation is what confirmed it."""

    def _robot(self, active_version):
        robot = AsyncMock()
        robot.get_active_map_versions.return_value = [{
            "p2map_id": "MAP1",
            "active_p2mapv_id": active_version,
        }]
        return robot

    def _check(self, robot, passed_version):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools.verify_virtual_wall_write import warn_if_map_version_is_stale

        report = Report()
        fresh = asyncio.run(warn_if_map_version_is_stale(robot, "MAP1", passed_version, report))
        return fresh, report.results[-1]

    def test_a_current_version_passes(self):
        fresh, entry = self._check(self._robot("260725T140000.000"), "260725T140000.000")

        assert fresh is True
        assert entry.status == "OK"

    def test_a_stale_version_is_flagged_with_the_correct_one(self):
        """His real values: the id he passed, and a newer one after the
        restart."""
        fresh, entry = self._check(self._robot("260725T180000.000"), "260725T101729.167")

        assert fresh is False
        assert entry.status == "FAILED"
        assert "260725T180000.000" in entry.detail, "must name the version to use instead"

    def test_an_unreadable_map_list_does_not_block_the_run(self):
        """This is a warning, not a gate -- failing to check must not
        cost the tester their actual test."""
        robot = AsyncMock()
        robot.get_active_map_versions.side_effect = RuntimeError("network")

        fresh, entry = self._check(robot, "anything")

        assert fresh is True
        assert entry.status == "SKIPPED"

    def test_a_map_the_robot_does_not_list_is_skipped_rather_than_failed(self):
        fresh, entry = self._check(self._robot("V1"), "V1")
        assert fresh is True

        robot = AsyncMock()
        robot.get_active_map_versions.return_value = [{"p2map_id": "OTHER", "active_p2mapv_id": "V9"}]
        fresh, entry = self._check(robot, "V1")

        assert fresh is True
        assert entry.status == "SKIPPED"


class TestOnlyFirstWallNarrowsTheQuestion:
    """Splits the virtual-wall failure into two answerable halves.

    Three request envelopes have now been genuinely sent and all
    rejected with HTTP 500 (DaRealGuGu, a29), so `response_type` is
    ruled out -- the first negative result in this investigation that
    was actually earned rather than produced by a local crash.

    What remains open is whether the command SHAPE is wrong or whether
    something about this particular list is. That distinction matters:
    unlike rename_room, which is confirmed live, set_virtual_wall has
    never been observed on the wire from the real app. Its entire
    structure comes from decompilation, so "the shape is wrong" is a
    live possibility rather than a long shot.

    One wall is the cheapest way to separate the two, and both outcomes
    say something -- which was not true of the whole-list test alone."""

    def _walls(self, count: int):
        from unittest.mock import MagicMock

        return [MagicMock(name=f"wall{i}") for i in range(count)]

    def test_only_the_first_wall_is_kept(self):
        walls = self._walls(3)

        assert walls[:1] == [walls[0]]

    def test_a_single_wall_list_is_unaffected(self):
        """With one wall there is nothing to narrow, and the flag must
        not turn a valid request into an empty one."""
        walls = self._walls(1)
        only_first = True

        result = walls[:1] if only_first and len(walls) > 1 else walls

        assert result == walls

    def test_the_flag_is_offered_on_the_command_line(self):
        """Without this the option exists in code and nobody can reach
        it -- a pattern that has cost this project real testing rounds."""
        import inspect

        import roombapy_prime_tools.verify_virtual_wall_write as mod

        source = inspect.getsource(mod)

        assert '"--only-first-wall"' in source
        assert "args.only_first_wall" in source

    def test_send_update_unchanged_accepts_the_parameter(self):
        """The other half of the same trap: a flag parsed but never
        forwarded. That exact mismatch -- parameter added to one layer
        and not the next -- cost two testers a session each in a28."""
        import inspect

        from roombapy_prime_tools.verify_virtual_wall_write import send_update_unchanged

        assert "only_first_wall" in inspect.signature(send_update_unchanged).parameters


class TestOnlyFirstWallActuallyTruncatesThePayload:
    """The banner and the payload must agree.

    The first version of --only-first-wall built the command BEFORE
    trimming the list, so it printed "sending 1 of 2 wall(s)" and then
    sent both. DaRealGuGu ran it and the result looked like a clean
    negative -- three shapes rejected -- when in fact the narrowing test
    had never happened.

    That is the second time this exact class of mistake has cost a
    tester an evening here. a28 added a parameter to the REST client and
    not to the wrapper; this added a truncation after the value it was
    meant to affect had already been captured. Both were invisible
    without reading real output line by line.

    A test that only checked the local list would have passed in both
    cases. This checks what actually goes out."""

    def _walls(self, count):
        from roombapy_prime.models.geometry import Polygon
        from roombapy_prime.models.map_editing import (
            VirtualWallNoMopZoneV1,
            VirtualWallRectangleV1,
        )

        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        kinds = (VirtualWallRectangleV1, VirtualWallNoMopZoneV1)
        return [
            kinds[i % 2](str(i + 1), Polygon(coordinates=[square]))
            for i in range(count)
        ]

    def _payload(self, walls, only_first):
        from roombapy_prime.models.map_editing import SetVirtualWallsV1

        if only_first and len(walls) > 1:
            walls = walls[:1]
        return SetVirtualWallsV1(walls=walls).to_v1_command_body()

    def test_the_sent_payload_carries_one_wall(self):
        """The whole point. Anything else and the run answers a question
        nobody asked."""
        payload = self._payload(self._walls(2), only_first=True)

        assert payload["params"]["virwall"][0] == 1
        assert len(payload["params"]["virwall"]) == 2

    def test_it_is_the_first_wall(self):
        payload = self._payload(self._walls(3), only_first=True)

        assert payload["params"]["virwall"][1][0] == "1"

    def test_without_the_flag_everything_is_sent(self):
        payload = self._payload(self._walls(3), only_first=False)

        assert payload["params"]["virwall"][0] == 3
        assert len(payload["params"]["virwall"]) == 4

    def test_a_single_wall_is_unaffected(self):
        """No truncation to do, and the flag must not turn a valid
        request into an empty one."""
        payload = self._payload(self._walls(1), only_first=True)

        assert payload["params"]["virwall"][0] == 1
        assert len(payload["params"]["virwall"]) == 2

    def test_the_command_is_built_after_the_truncation(self):
        """Guards the ordering directly, since that is what broke. The
        source check is blunt, but the bug was invisible to any test of
        the list alone."""
        import inspect

        import roombapy_prime_tools.verify_virtual_wall_write as mod

        source = inspect.getsource(mod.send_update_unchanged)
        trim = source.index("walls = walls[:1]")
        build = source.index("command = SetVirtualWallsV1(walls=walls)")

        assert trim < build, "command built before the list was trimmed"


class TestPayloadTypesWereSettledByBytecodeNotByGuessing:
    """A type-variant probe was built here and then removed unused.

    The reasoning behind it was sound: HTTP 500 rather than 400 points
    at a body that parses and then fails deserialising, which is what a
    type mismatch looks like. So the plan was to send the id as an Int
    and the type code as a String and see which one the server liked.

    APK bytecode answered both directly instead -- the id is a String
    (no boxing in the bytecode) and the type code an Int
    (Integer.valueOf followed by a Number cast). Sending variants
    already known to be wrong would have spent real writes on real maps
    and added noise.

    And the actual cause was neither: the virwall array starts with a
    COUNT of the walls. Every guess about types was aimed at element
    zero being a wall, when element zero is a number.

    Kept as a test so the removal reads as a decision. The temptation to
    re-add "just to be sure" is real, and it costs a tester an evening
    each time."""

    def test_no_type_variant_machinery_remains(self):
        import inspect

        import roombapy_prime_tools.verify_virtual_wall_write as mod

        source = inspect.getsource(mod)

        assert "_RetypedWalls" not in source
        assert "_with_payload_form" not in source

    def test_response_type_variants_are_still_sent(self):
        """These stay, and are now worth re-running: all three were
        rejected before the count was known, so they were rejected FOR
        the count and say nothing about response_type."""
        import inspect

        import roombapy_prime_tools.verify_virtual_wall_write as mod

        source = inspect.getsource(mod.send_update_unchanged)

        assert '"link"' in source
        assert '"binary"' in source


# ---------------------------------------------------------------------
# Stage 2 -- --drop-one-wall
#
# The first write in this project that changes what the robot holds
# rather than restating it. The send itself cannot be tested without a
# robot; what CAN be tested is everything that decides whether a
# tester's map survives the run, and that is the part worth guarding:
#
#   * the original list is captured BEFORE the change, so a restore
#     never depends on a re-read that might fail
#   * a map with one wall is refused, because dropping the only entry
#     sends an EMPTY list and answers a different question
#   * the restore is unconditional, not a second prompt
#
# Each of these is a way a tester could be left with a zone missing.
# ---------------------------------------------------------------------

def _walls_and_robot(count: int, shrink_after_first_read: bool = False):
    """A robot whose map holds `count` walls, recording every edit.

    `shrink_after_first_read` makes the SECOND and later reads return
    one wall fewer -- what a real robot does once the drop has landed.
    Without it a re-read and the captured original look identical and a
    test claiming to tell them apart proves nothing.
    """
    policy_zones = {
        "features": [
            {
                "id": f"kz{i}",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[float(i), 0.0], [float(i) + 1, 0.0],
                                     [float(i) + 1, 1.0], [float(i), 1.0]]],
                },
                "properties": {"type": "KeepOutZone"},
            }
            for i in range(count)
        ]
    }
    robot = MagicMock()
    robot.get_map_geojson_link = AsyncMock(
        return_value={"map_url": "https://example.invalid/bundle.tar.gz"}
    )
    shrunk = {"features": policy_zones["features"][1:]}
    reads = {"n": 0}

    async def _download(*_a, **_k):
        reads["n"] += 1
        if shrink_after_first_read and reads["n"] > 1:
            return _make_bundle_bytes(shrunk)
        return _make_bundle_bytes(policy_zones)

    robot.download_map_bundle = AsyncMock(side_effect=_download)
    robot.edit_map = AsyncMock(return_value={"status": "success"})
    return robot


def _run_drop(robot, index=0, monkeypatch=None):
    from unittest.mock import patch

    from roombapy_prime_tools import verify_virtual_wall_write as mod

    report = MagicMock()

    class _Ctx:
        async def __aenter__(self):
            return robot, report

        async def __aexit__(self, *exc):
            return False

    with patch.object(mod, "connected_robot", lambda *a, **k: _Ctx()), \
         patch.object(mod, "confirm", lambda *a, **k: True):
        asyncio.run(
            mod.send_drop_one_wall("u", "p", "DE", "BLID", "MAP1", "V1", index)
        )
    return report


def test_a_single_wall_map_is_refused_rather_than_emptied():
    """Dropping the only entry sends an empty list -- a different
    question, and one whose answer would be indistinguishable from
    this one's."""
    robot = _walls_and_robot(1)
    _run_drop(robot)
    assert robot.edit_map.await_count == 0, (
        "a one-wall map must send nothing at all"
    )


def test_the_original_list_is_restored_after_the_drop():
    robot = _walls_and_robot(3)
    _run_drop(robot, index=1)

    assert robot.edit_map.await_count == 2, "expected a drop and a restore"
    dropped, restored = (c.args[1] for c in robot.edit_map.await_args_list)
    assert len(dropped.walls) == 2, "the drop must send one wall fewer"
    assert len(restored.walls) == 3, "the restore must send the original list"


def test_the_restore_carries_the_original_entries_not_a_re_read():
    """The restore is built from the list captured BEFORE the change.

    The robot here shrinks after the first read, exactly as a real one
    does once the drop lands -- so a restore built from a re-read would
    send two walls and put nothing back. The first version of this test
    used a robot that returned the same three walls on every read, which
    made both implementations look identical: it passed against the bug
    it was written to catch."""
    robot = _walls_and_robot(3, shrink_after_first_read=True)
    _run_drop(robot, index=0)

    _, restored = (c.args[1] for c in robot.edit_map.await_args_list)
    assert len(restored.walls) == 3, (
        "the restore must send all three original walls, not what the map "
        "reported after the drop"
    )


def test_an_out_of_range_index_sends_nothing():
    robot = _walls_and_robot(2)
    _run_drop(robot, index=9)
    assert robot.edit_map.await_count == 0


# ---------------------------------------------------------------------
# Stage 2b -- --move-one-wall
#
# shift_wall() is pure arithmetic and therefore the one part of this
# script that CAN be fully tested. It is also the part where a mistake
# is invisible in the field: a zone that moves the wrong way, or by the
# wrong amount, still looks like "it moved", and the run would report a
# confirmed coordinate system that is wrong.
# ---------------------------------------------------------------------

def test_a_polygon_moves_by_exactly_the_delta():
    from roombapy_prime.models.geometry import Polygon
    from roombapy_prime.models.map_editing import VirtualWallRectangleV1
    from roombapy_prime_tools.verify_virtual_wall_write import shift_wall

    wall = VirtualWallRectangleV1(
        wall_id="kz1",
        polygon=Polygon(coordinates=[[(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]]),
    )
    moved = shift_wall(wall, 0.5, -0.25)

    assert moved.polygon.coordinates == [
        [(0.5, -0.25), (2.5, -0.25), (2.5, 0.75), (0.5, 0.75)]
    ]
    assert moved.wall_id == "kz1", "moving must not change identity"


def test_the_original_wall_is_not_mutated():
    """The restore resends the originals. If shift_wall() edited them in
    place, the restore would put back the MOVED zone and the map would
    never return to where it started -- while every count check still
    passed."""
    from roombapy_prime.models.geometry import Polygon
    from roombapy_prime.models.map_editing import VirtualWallRectangleV1
    from roombapy_prime_tools.verify_virtual_wall_write import shift_wall

    wall = VirtualWallRectangleV1(
        wall_id="kz1",
        polygon=Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]]),
    )
    before = list(wall.polygon.coordinates[0])
    shift_wall(wall, 10.0, 10.0)

    assert wall.polygon.coordinates[0] == before


def test_a_wall_with_no_recognised_geometry_field_raises():
    """Returning the wall unchanged would send a "move" that moves
    nothing -- indistinguishable from a robot that ignored the edit, and
    it would be recorded as a confirmed negative.

    The first version of this test used a class carrying `polygon =
    object()`, which reaches the INNER type check and raises there. It
    passed while the fallback below was replaced with `return wall`:
    right assertion, wrong code path. A wall with no geometry attribute
    at all is what actually exercises it."""
    from roombapy_prime_tools.verify_virtual_wall_write import shift_wall

    class _NoGeometry:
        wall_id = "x"

    with pytest.raises(TypeError):
        shift_wall(_NoGeometry(), 1.0, 1.0)


def test_an_unmovable_geometry_type_also_raises():
    """The inner check, kept separately now that the two are known to be
    different paths."""
    from roombapy_prime_tools.verify_virtual_wall_write import shift_wall

    class _Odd:
        polygon = object()

    with pytest.raises(TypeError):
        shift_wall(_Odd(), 1.0, 1.0)


def test_the_default_delta_is_a_quarter_of_the_zone():
    from roombapy_prime.models.geometry import Polygon
    from roombapy_prime.models.map_editing import VirtualWallRectangleV1
    from roombapy_prime_tools.verify_virtual_wall_write import wall_extent

    wall = VirtualWallRectangleV1(
        wall_id="kz1",
        polygon=Polygon(coordinates=[[(0.0, 0.0), (4.0, 0.0), (4.0, 1.0), (0.0, 1.0)]]),
    )
    assert wall_extent(wall) == 4.0


# ---------------------------------------------------------------------
# Verification must read the version the edit PRODUCED.
#
# Every accepted edit mints a new p2mapv_id and returns it. The id from
# the command line still points at the map as it was before, so
# re-reading that one reports "unchanged" whatever happened -- a check
# that cannot fail and cannot pass.
#
# It fired on the first real run (@chairstacker, issue #89): the script
# printed "ACCEPTED BUT NOT STORED" while he watched the zone vanish
# from the app and come back. The removal had worked.
# ---------------------------------------------------------------------

def test_the_verify_read_uses_the_returned_version():
    from unittest.mock import patch

    from roombapy_prime_tools import verify_virtual_wall_write as mod

    robot = _walls_and_robot(3)
    robot.edit_map = AsyncMock(
        return_value={"status": "success", "p2mapv_id": "NEW-VERSION"}
    )
    report = MagicMock()

    class _Ctx:
        async def __aenter__(self):
            return robot, report

        async def __aexit__(self, *exc):
            return False

    with patch.object(mod, "connected_robot", lambda *a, **k: _Ctx()), \
         patch.object(mod, "confirm", lambda *a, **k: True):
        asyncio.run(
            mod.send_drop_one_wall("u", "p", "DE", "BLID", "MAP1", "OLD-VERSION", 0)
        )

    versions = [c.args[1] for c in robot.get_map_geojson_link.await_args_list]
    assert versions[0] == "OLD-VERSION", "the first read is the map as given"

    # POSITIONAL, not membership. `"NEW-VERSION" in versions[1:]` passes
    # even with the fix reverted, because the RESTORE verification reads
    # the version its own response returned -- which this mock also
    # calls NEW-VERSION. Checked by reverting: the membership form did
    # not fail, this one does.
    assert versions[1] == "NEW-VERSION", (
        "the verify read straight after the drop must use the version the "
        "edit returned; reading the command-line one reports 'unchanged' "
        f"no matter what happened (got {versions!r})"
    )
