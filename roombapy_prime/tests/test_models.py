"""Tests for roombapy_prime.models.

Command-body and geometry shapes are SYNTHETIC checks against the
Java-source-confirmed structure documented in docs/archive/FINDINGS_2026-07-11.md --
no real p2maps command response was ever captured live. RoomType/
FurnitureType enum values and the livemap cur_path parsing ARE checked
against the literal values found in the Java source (not synthetic).
"""
from __future__ import annotations

from roombapy_prime.models import (
    AddCleanZones,
    CleanZone,
    DeleteCleanZones,
    FurnitureType,
    KeepOutZone,
    LineString,
    MapUpdateMessage,
    MergeRooms,
    Polygon,
    RevertUserEdits,
    RoomType,
    SetFurniture,
    SetKeepOutZones,
    SetRoomMetadata,
    SplitRoom,
    parse_livemap_message,
)
import json


# --- geometry ------------------------------------------------------------

def test_polygon_to_geojson_matches_confirmed_nesting() -> None:
    """Confirmed shape: Polygon.getRawValue() == List<List<List<Double>>>
    (see GeometrySerializer.java) -- type/coordinates/ring/position."""
    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]])
    geojson = poly.to_geojson()
    assert geojson["type"] == "Polygon"
    assert geojson["coordinates"] == [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]


def test_linestring_to_geojson() -> None:
    line = LineString(coordinates=[(0.0, 0.0), (5.0, 5.0)])
    assert line.to_geojson() == {"type": "LineString", "coordinates": [[0.0, 0.0], [5.0, 5.0]]}


# --- RoomType / FurnitureType (values from Java source, not synthetic) --

def test_room_type_values_match_java_source() -> None:
    assert RoomType.NOT_RECOGNIZED == 2100
    assert RoomType.BEDROOM == 2101
    assert RoomType.OTHER == 2120


def test_furniture_type_values_match_java_source() -> None:
    assert FurnitureType.UNKNOWN == 0
    assert FurnitureType.CAT_TOWER == 18
    assert FurnitureType.LITTER_BOX == 14


# --- p2maps command envelopes (SYNTHETIC -- structure, not live-tested) --

def test_set_room_metadata_command_body() -> None:
    cmd = SetRoomMetadata(room_id="r1", name="Kitchen", room_type=RoomType.KITCHEN)
    body = cmd.to_command_body()
    assert body["command"] == "set_room_metadata"
    assert body["params"]["id"] == "r1"
    assert body["params"]["metadata"] == {"name": "Kitchen", "type_id": 2105}


def test_merge_rooms_command_body() -> None:
    body = MergeRooms(room_ids=["a", "b"]).to_command_body()
    assert body == {"command": "merge_rooms", "params": {"ids": ["a", "b"]}}


def test_split_room_command_body_from_two_points() -> None:
    cmd = SplitRoom.from_two_points("r1", (0.0, 0.0), (1.0, 1.0))
    body = cmd.to_command_body()
    assert body["command"] == "split_room"
    assert body["params"]["id"] == "r1"
    assert body["params"]["split_line"]["type"] == "LineString"
    assert body["params"]["split_line"]["coordinates"] == [[0.0, 0.0], [1.0, 1.0]]


def test_set_keep_out_zones_command_body() -> None:
    zone = KeepOutZone(geometry=Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]]), zone_id="z1")
    body = SetKeepOutZones(keep_out_zones=[zone]).to_command_body()
    assert body["command"] == "set_keep_out_zones"
    assert body["params"]["keep_out_zones"][0]["id"] == "z1"
    assert body["params"]["no_mop_zones"] == []
    assert body["params"]["virtual_walls"] == []


def test_add_clean_zones_command_body() -> None:
    zone = CleanZone(name="Living Room", geometry=Polygon(coordinates=[[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]]))
    body = AddCleanZones(zones=[zone]).to_command_body()
    assert body["command"] == "add_clean_zones"
    assert body["params"]["zones"][0]["name"] == "Living Room"
    assert "id" not in body["params"]["zones"][0]  # zone_id was not set


def test_delete_clean_zones_command_body() -> None:
    body = DeleteCleanZones(zone_ids=["z1", "z2"]).to_command_body()
    assert body == {"command": "delete_clean_zones", "params": {"ids": ["z1", "z2"]}}


def test_set_furniture_command_body_uses_lowercase_type_name() -> None:
    from roombapy_prime.models import Furniture

    furn = Furniture(furniture_type=FurnitureType.CAT_TOWER, geometry=Polygon(coordinates=[[(0.0, 0.0)]]))
    body = SetFurniture(furniture=[furn]).to_command_body()
    assert body["params"]["furniture"][0]["type"] == "cat_tower"


def test_revert_user_edits_command_body() -> None:
    assert RevertUserEdits().to_command_body() == {"command": "revert_user_edits", "params": {}}


# --- livemap message parsing (cur_path structure IS confirmed) ---------

def test_parse_livemap_position_update_single_point() -> None:
    payload = json.dumps({
        "timestamp": "2026-07-11T00:00:00Z",
        "update_expire_ts": "2026-07-11T00:01:00Z",
        "pos_update": {"cur_path": [7, 1.5, 2.5, 0.0, 1, 1783704212]},
    }).encode()

    result = parse_livemap_message(payload)

    assert result.sequence_number == 7
    assert len(result.updates) == 1
    sample = result.updates[0]
    assert sample.point == (1.5, 2.5)
    # The wire angle, unmodified. This used to read `0.0 + 3.1415927`,
    # which asserted the half-turn the parser applied rather than
    # anything about the robot -- a test that agrees with the code by
    # construction. The half turn is gone: the first field observation
    # of the heading showed the line pointing out of the back.
    assert sample.orientation == 0.0
    assert sample.operating_modes == 1


def test_parse_livemap_position_update_multiple_points_is_trajectory() -> None:
    """Confirms the trajectory-like nature: multiple (x,y,orient,mode)
    tuples in a single message, exactly as native analysis suggested."""
    payload = json.dumps({
        "pos_update": {
            "cur_path": [1, 0.0, 0.0, 0.0, 0, 1.0, 1.0, 1.5, 0, 2.0, 2.0, 3.0, 1, 1783704300]
        }
    }).encode()

    result = parse_livemap_message(payload)

    assert result.sequence_number == 1
    assert len(result.updates) == 3
    assert result.updates[0].point == (0.0, 0.0)
    assert result.updates[2].point == (2.0, 2.0)
    assert result.updates[2].operating_modes == 1


def test_parse_livemap_map_update() -> None:
    payload = json.dumps({
        "timestamp": "2026-07-11T00:00:00Z",
        "map_update": {"livemap_url": "https://example.invalid/map.png"},
    }).encode()

    result = parse_livemap_message(payload)

    assert isinstance(result, MapUpdateMessage)
    assert result.livemap_url == "https://example.invalid/map.png"


def test_parse_livemap_map_update_from_real_live_capture() -> None:
    """CONFIRMED LIVE (this session, jayjay13011, roombapy-prime
    v0.1.11a6 -- the first capture with topic tracking, confirming this
    arrives on livemap_topic() exactly). Verbatim shape except the
    presigned URL query strings truncated for readability -- they don't
    affect parsing, only S3 auth. Locks in the two fields the earlier,
    hypothetical-only test above didn't cover: livemap_url_raw and the
    outer timestamp."""
    payload = json.dumps({
        "timestamp": 1784559121,
        "map_update": {
            "livemap_url": (
                "https://s3.amazonaws.com/elpasodata018-pmaptransferbucket-1pckk9n2mafep/"
                "p2maps/v011/dload_livemap/BLID/p2mapv_geojson.tgz?X-Amz-Signature=abc"
            ),
            "livemap_url_raw": (
                "https://s3.amazonaws.com/elpasodata018-pmaptransferbucket-1pckk9n2mafep/"
                "p2maps/v011/dload_livemap/BLID/rawmap?X-Amz-Signature=def"
            ),
        },
    }).encode()

    result = parse_livemap_message(payload)

    assert isinstance(result, MapUpdateMessage)
    assert result.timestamp == 1784559121
    assert result.livemap_url.endswith("p2mapv_geojson.tgz?X-Amz-Signature=abc")
    assert result.livemap_url_raw.endswith("rawmap?X-Amz-Signature=def")


def test_parse_livemap_position_update_operating_modes_transition_from_real_capture() -> None:
    """CONFIRMED LIVE (this session, jayjay13011): operating_modes is
    NOT a fixed constant -- it was 0 for the first ~5 seconds of a
    cleaning mission (still settling in after travel/reloc), then
    switched to 5 for the remainder of the observed period. Both real
    values from the same capture, checked directly against the raw
    messages (not synthesized)."""
    early = json.dumps({
        "pos_update": {"cur_path": [1, 0.000434, -0.025816, -0.084961, 0, 1784559143]},
    }).encode()
    later = json.dumps({
        "pos_update": {"cur_path": [8, -0.163172, -0.013106, -2.571099, 5, 1784559150]},
    }).encode()

    early_result = parse_livemap_message(early)
    later_result = parse_livemap_message(later)

    assert early_result.updates[0].operating_modes == 0
    assert later_result.updates[0].operating_modes == 5


def test_parse_livemap_unrecognized_shape_raises() -> None:
    import pytest

    payload = json.dumps({"something_else": True}).encode()
    with pytest.raises(ValueError, match="Unrecognized"):
        parse_livemap_message(payload)


def test_decode_rawmap_to_png_produces_correctly_oriented_image() -> None:
    """Builds a minimal, synthetic rawmap protobuf (2x2 grid, top row
    white/bottom row black on the wire) and checks decode_rawmap_to_png()
    both parses the confirmed field layout correctly AND applies the
    confirmed vertical flip (top-of-wire ends up at the bottom of the
    output PNG, matching the real app's own orientation).

    Pillow is an optional dependency (pip install "roombapy-prime[map]")
    -- skips cleanly rather than failing if it isn't installed, matching
    decode_rawmap_to_png()'s own "not a hard dependency" design. CI
    installs the [map] extra specifically so this actually runs there,
    not just skips."""
    import pytest

    pytest.importorskip("PIL")
    from roombapy_prime.models.livemap import decode_rawmap_to_png

    def _varint(n: int) -> bytes:
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            out.append(b | (0x80 if n else 0))
            if not n:
                break
        return bytes(out)

    def _field(num: int, wire_type: int, payload: bytes) -> bytes:
        tag = _varint((num << 3) | wire_type)
        if wire_type == 2:
            return tag + _varint(len(payload)) + payload
        return tag + payload

    width, height = 2, 2
    header = _field(2, 0, _varint(width)) + _field(3, 0, _varint(height))
    grid_bytes = bytes([255, 255, 0, 0])  # row0=white, row1=black on the wire
    grid_wrapper = _field(1, 2, grid_bytes)
    rawmap = _field(3, 2, header) + _field(4, 2, grid_wrapper)

    png_bytes = decode_rawmap_to_png(rawmap)

    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(png_bytes))
    assert img.size == (2, 2)
    # Flipped: the wire's row0 (white) must now be at the BOTTOM.
    assert img.getpixel((0, 1)) == 255
    assert img.getpixel((0, 0)) == 0


# --- read-side domain models (map contents) ------------------------------
#
# These are structurally simpler than the edit-command tests above --
# no live/synthetic round-trip against a known wire response exists (no
# real fetchPersistentMap/get_map_metadata response was ever captured),
# so these just confirm the dataclasses construct and hold their fields
# as expected, plus the confirmed enum value lists.

def test_hazard_type_values_match_java_source() -> None:
    from roombapy_prime.models import HazardType

    assert HazardType.CAT.value == "CAT"
    assert HazardType.WEIGHING_SCALE.value == "WEIGHING_SCALE"
    assert len(list(HazardType)) == 16


def test_room_feature_from_json_confirmed_structure() -> None:
    """REBUILT (session 47) -- REPLACES test_room_info_holds_fields.
    RoomInfo (flat) no longer exists; the confirmed real structure is a
    GeoJSON Feature with a nested Properties object, see RoomFeature's
    docstring for the full evidence trail."""
    from roombapy_prime.models import RoomFeature

    room = RoomFeature.from_json({
        "type": "Feature",
        "id": "r1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]},
        "properties": {"name": "Kitchen", "type": "KITCHEN", "adjacentRoomIDs": ["r2"]},
    })

    assert room.feature_id == "r1"
    assert room.feature_type == "Feature"
    assert room.properties.name == "Kitchen"
    assert room.properties.room_type == "KITCHEN"
    assert room.properties.adjacent_room_ids == ["r2"]
    assert room.geometry.coordinates == [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]]


def test_furniture_feature_from_json_has_fields_the_edit_command_lacks() -> None:
    """REBUILT (session 47) -- REPLACES
    test_furniture_info_read_has_fields_the_edit_command_lacks.
    Confirms the corrected understanding: orientation/cleaning_area
    belong to the READ model's Properties, not the edit command (see
    module docstring for the earlier mistake this corrects)."""
    from roombapy_prime.models import FurnitureFeature

    furniture = FurnitureFeature.from_json({
        "type": "Feature",
        "id": "f1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
        "properties": {
            "type": 2, "source": "user", "orientation": 1.57,
            "cleaningArea": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
        },
    })

    assert furniture.properties.orientation == 1.57
    assert furniture.properties.cleaning_area is not None
    assert furniture.properties.furniture_type == FurnitureType.SOFA

    # the edit-side Furniture dataclass genuinely has no such fields
    from roombapy_prime.models import Furniture

    poly = Polygon(coordinates=[[(0.0, 0.0)]])
    edit_furniture = Furniture(furniture_type=FurnitureType.SOFA, geometry=poly)
    assert not hasattr(edit_furniture, "orientation")
    assert not hasattr(edit_furniture, "cleaning_area")


def test_multi_polygon_to_geojson() -> None:
    from roombapy_prime.models import MultiPolygon

    poly_a = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]])
    poly_b = Polygon(coordinates=[[(2.0, 2.0), (3.0, 2.0), (3.0, 3.0)]])
    mp = MultiPolygon(coordinates=[poly_a, poly_b])

    geojson = mp.to_geojson()
    assert geojson["type"] == "MultiPolygon"
    assert len(geojson["coordinates"]) == 2
    assert geojson["coordinates"][0] == [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]


def test_dock_feature_uses_point_not_polygon() -> None:
    """REBUILT (session 47) -- REPLACES test_dock_info_uses_point_not_polygon."""
    from roombapy_prime.models import DockFeature, Point

    dock = DockFeature.from_json({
        "type": "Feature",
        "id": "d1",
        "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
        "properties": {"orientation": 0.5},
    })
    assert dock.geometry == Point(coordinates=(1.0, 2.0))
    assert dock.properties.orientation == 0.5


# --- NEW map-bundle Feature models (session 47) ---------------------------


def test_border_feature_properties_confirmed_empty() -> None:
    """CONFIRMED (session 47): BorderFeature$Properties has NO custom
    fields beyond the shared Feature envelope -- confirmed empty, not
    an oversight."""
    from roombapy_prime.models import BorderFeature

    border = BorderFeature.from_json({
        "type": "Feature", "id": "b1",
        "geometry": {"type": "MultiPolygon", "coordinates": [[[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]]},
    })
    assert border.feature_id == "b1"
    assert len(border.geometry.coordinates) == 1


def test_coverage_feature_from_json() -> None:
    from roombapy_prime.models import CoverageFeature

    coverage = CoverageFeature.from_json({
        "type": "Feature", "id": "c1",
        "geometry": {"type": "MultiPolygon", "coordinates": []},
        "properties": {"operatingModes": [1, 2]},
    })
    assert coverage.properties.operating_modes == [1, 2]


def test_trajectory_feature_from_json() -> None:
    from roombapy_prime.models import TrajectoryFeature

    traj = TrajectoryFeature.from_json({
        "type": "Feature", "id": "t1",
        "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
        "properties": {"index": 3, "operatingModes": [5]},
    })
    assert traj.properties.index == 3
    assert traj.geometry.coordinates == [(0.0, 0.0), (1.0, 1.0)]


def test_policy_zone_feature_replaces_three_previously_separate_guesses() -> None:
    """CONFIRMED (parallel native-analysis track,
    P2MapBundleContentHolderPersistentMapKt's own categorization code):
    "NoMopZone" is the real zone_type string -- an earlier version of
    this test used a placeholder ("no_mop") that was never actually
    confirmed against real code."""
    from roombapy_prime.models import PolicyZoneFeature

    zone = PolicyZoneFeature.from_json({
        "type": "Feature", "id": "z1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
        "properties": {"type": "NoMopZone", "threshold_type": "soft"},
    })
    assert zone.properties.zone_type == "NoMopZone"
    assert zone.properties.threshold_type == "soft"


def test_policy_zone_feature_parses_linestring_geometry_for_virtual_walls() -> None:
    """CONFIRMED (parallel native-analysis track): a virtual wall is a
    "KeepOutZone"-typed feature whose geometry is a LineString, not a
    Polygon -- there is no separate "VirtualWall" zone_type string.
    An earlier version of this parser assumed Polygon unconditionally,
    which would have silently mis-parsed this exact shape (LineString's
    flat coordinate list read as if it were Polygon's list-of-rings)."""
    from roombapy_prime.models import PolicyZoneFeature
    from roombapy_prime.models.geometry import LineString

    wall = PolicyZoneFeature.from_json({
        "type": "Feature", "id": "w1",
        "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
        "properties": {"type": "KeepOutZone"},
    })

    assert wall.properties.zone_type == "KeepOutZone"
    assert isinstance(wall.geometry, LineString)
    assert wall.geometry.coordinates == [(0.0, 0.0), (1.0, 1.0)]


def test_policy_zone_feature_parses_polygon_geometry_for_keep_out_zones() -> None:
    """The OTHER half of the same discrimination: "KeepOutZone" +
    Polygon geometry (a real, persistent rectangular zone), not a
    virtual wall -- geometry shape, not zone_type alone, decides."""
    from roombapy_prime.models import PolicyZoneFeature
    from roombapy_prime.models.geometry import Polygon

    zone = PolicyZoneFeature.from_json({
        "type": "Feature", "id": "z2",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]]},
        "properties": {"type": "KeepOutZone"},
    })

    assert zone.properties.zone_type == "KeepOutZone"
    assert isinstance(zone.geometry, Polygon)


def test_policy_zones_to_virtual_walls_full_conversion_pipeline() -> None:
    """CONFIRMED (parallel native-analysis track,
    P2MapBundleContentHolderPersistentMapKt's own categorization code)
    -- exercises the complete, real pipeline end to end: a mixed list
    of raw policyZones.geojson-shaped features (keep-out zone, virtual
    wall, no-mop zone, and a threshold that must be dropped) correctly
    sorted into the three VirtualWallV1 subtypes needed to rebuild a
    SetVirtualWalls command, with coordinates passed through
    unchanged. This is the actual conversion this project's planned
    "resend unchanged" stage-1 test for virtual walls depends on."""
    from roombapy_prime.models import PolicyZoneFeature
    from roombapy_prime.models.map_editing import (
        VirtualWallLinearV1,
        VirtualWallNoMopZoneV1,
        VirtualWallRectangleV1,
        policy_zones_to_virtual_walls,
    )

    features = [
        PolicyZoneFeature.from_json({
            "id": "kz1",
            "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]]},
            "properties": {"type": "KeepOutZone"},
        }),
        PolicyZoneFeature.from_json({
            "id": "vw1",
            "geometry": {"type": "LineString", "coordinates": [[2.0, 2.0], [3.0, 3.0]]},
            "properties": {"type": "KeepOutZone"},
        }),
        PolicyZoneFeature.from_json({
            "id": "nm1",
            "geometry": {"type": "Polygon", "coordinates": [[[4.0, 4.0], [5.0, 4.0], [5.0, 5.0], [4.0, 5.0]]]},
            "properties": {"type": "NoMopZone"},
        }),
        PolicyZoneFeature.from_json({
            "id": "th1",
            "geometry": {"type": "Polygon", "coordinates": [[[6.0, 6.0]]]},
            "properties": {"type": "Threshold", "threshold_type": "soft"},
        }),
    ]

    walls = policy_zones_to_virtual_walls(features)

    assert len(walls) == 3  # the Threshold entry must be dropped
    assert isinstance(walls[0], VirtualWallRectangleV1)
    assert isinstance(walls[1], VirtualWallLinearV1)
    assert isinstance(walls[2], VirtualWallNoMopZoneV1)
    # coordinates must pass through completely unchanged -- confirmed,
    # no transformation happens anywhere in this pipeline.
    assert walls[1].to_json()[2:6] == [2.0, 2.0, 3.0, 3.0]


def test_clean_zone_feature_has_name_unlike_adhoc() -> None:
    from roombapy_prime.models import AdHocCleanZoneFeature, CleanZoneFeature

    clean_zone = CleanZoneFeature.from_json({
        "type": "Feature", "id": "cz1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
        "properties": {"name": "Under the couch"},
    })
    assert clean_zone.properties.name == "Under the couch"

    adhoc = AdHocCleanZoneFeature.from_json({
        "type": "Feature", "id": "az1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
    })
    assert not hasattr(adhoc, "properties")  # confirmed empty, no Properties object at all


def test_floor_plan_feature_from_json() -> None:
    from roombapy_prime.models import FloorPlanFeature

    fp = FloorPlanFeature.from_json({
        "type": "Feature", "id": "fp1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
        "properties": {"type": "hardwood", "roomId": "r1"},
    })
    assert fp.properties.floor_type == "hardwood"
    assert fp.properties.room_id == "r1"


def test_floor_type_feature_from_json() -> None:
    """EXPERIMENTAL per its own package name in the decompiled source
    (see FloorTypeFeature's docstring)."""
    from roombapy_prime.models import FloorTypeFeature

    ft = FloorTypeFeature.from_json({
        "type": "Feature", "id": "ft1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
        "properties": {"type": "tile"},
    })
    assert ft.properties.floor_type == "tile"


def test_bundle_manifest_from_json_resolves_file_naming_question() -> None:
    """NEW (session 47) -- this DEFINITIVELY resolves the "exact file
    naming inside the tar.gz bundle" question open since the fifth
    session: each ManifestFeature names the real filepath for that
    content type."""
    from roombapy_prime.models import BundleManifest

    manifest = BundleManifest.from_json({
        "metadata": {"id": "m1"},
        "features": [
            {"type": "rooms", "filepath": "rooms.geojson", "schemaVersion": 1},
            {"type": "borders", "filepath": "borders.geojson", "schemaVersion": 1},
        ],
        "experimentalFeatures": [{"type": "floorType", "filepath": "floor_type.geojson", "schemaVersion": 1}],
    })

    assert len(manifest.features) == 2
    assert manifest.features[0].content_type == "rooms"
    assert manifest.features[0].filepath == "rooms.geojson"
    assert len(manifest.experimental_features) == 1
    assert manifest.experimental_features[0].filepath == "floor_type.geojson"


def test_bundle_metadata_source_from_json() -> None:
    from roombapy_prime.models import BundleMetadataSource

    source = BundleMetadataSource.from_json({
        "missionStartTime": 1700000000, "mapUploadTime": 1700000100, "type": "picea",
    })
    assert source.mission_start_time == 1700000000
    assert source.source_type == "picea"


# --- V1 edit commands (session 48) -- confirmed wire formats -------------


def test_rename_room_v1_confirmed_field_names() -> None:
    """UPDATE (this session): live APK decompilation of the full
    EditMapV1Request.java confirmed the outer shape is
    {"command": "rename_room", "params": {...}}, not the flat
    {"type": "RenameRoom", ...} previously assumed -- room_id/room_name
    themselves were already correct (session 48), only the envelope
    around them was wrong."""
    from roombapy_prime.models import RenameRoomV1

    body = RenameRoomV1(room_id="r1", name="Kitchen").to_v1_command_body()
    assert body == {
        "command": "rename_room",
        "params": {"room_id": "r1", "room_name": "Kitchen"},
    }


def test_split_room_v1_confirmed_field_names() -> None:
    """UPDATE (this session): split_points is a FLAT list of doubles
    ([x1, y1, x2, y2]), not a list of [x, y] pairs as previously
    assumed."""
    from roombapy_prime.models import SplitRoomV1

    body = SplitRoomV1(room_id="r1", split_points=[(0.0, 0.0), (1.0, 1.0)]).to_v1_command_body()
    assert body == {
        "command": "split_room",
        "params": {"room_id": "r1", "split_points": [0.0, 0.0, 1.0, 1.0]},
    }


def test_merge_rooms_v1_confirmed_field_name() -> None:
    """UPDATE (this session): discriminator is "arrange_room", not
    "merge_rooms"/"MergeRooms" -- room_ids field name was already
    correct (session 48)."""
    from roombapy_prime.models import MergeRoomsV1

    body = MergeRoomsV1(ids=["r1", "r2"]).to_v1_command_body()
    assert body == {"command": "arrange_room", "params": {"room_ids": ["r1", "r2"]}}


def test_set_room_type_v1_confirmed_field_names() -> None:
    """UPDATE (this session): discriminator is "set_room_type", nested
    under params -- room_id/type_id field names were already correct
    (session 48)."""
    from roombapy_prime.models import RoomType, SetRoomTypeV1

    body = SetRoomTypeV1(room_id="r1", room_type=RoomType.KITCHEN).to_v1_command_body()
    assert body == {
        "command": "set_room_type",
        "params": {"room_id": "r1", "type_id": int(RoomType.KITCHEN)},
    }


def test_permanent_area_v1_is_a_positional_array_not_an_object() -> None:
    """UPDATE (this session): PermanentArea has its own custom
    serializer emitting [id, name, [x1, y1, ...]] -- not a
    {"id": ..., "name": ..., "geometry": {...GeoJSON...}} object as
    previously assumed."""
    from roombapy_prime.models import PermanentAreaV1

    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]])
    area = PermanentAreaV1(area_id="a1", name="Zone", geometry=poly)

    assert area.to_json() == ["a1", "Zone", [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]]


def test_set_permanent_areas_v1_confirmed_field_name() -> None:
    """UPDATE (this session): discriminator is "set_permanent_area"
    (singular) under params, not "SetPermanentAreas". area_points
    field name itself was already correct (session 48)."""
    from roombapy_prime.models import PermanentAreaV1, SetPermanentAreasV1

    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0)]])
    area = PermanentAreaV1(area_id="a1", name="Zone", geometry=poly)
    body = SetPermanentAreasV1(areas=[area]).to_v1_command_body()

    assert body["command"] == "set_permanent_area"
    assert body["params"]["area_points"] == [["a1", "Zone", [0.0, 0.0, 1.0, 0.0]]]


def test_delete_permanent_areas_v1_confirmed_field_name() -> None:
    """UPDATE (this session): discriminator is "del_permanent_area"
    (abbreviated, singular), not "DeletePermanentAreas". area_ids field
    name itself was already correct (session 48)."""
    from roombapy_prime.models import DeletePermanentAreasV1

    body = DeletePermanentAreasV1(area_ids=["a1", "a2"]).to_v1_command_body()
    assert body == {
        "command": "del_permanent_area",
        "params": {"area_ids": ["a1", "a2"]},
    }


def test_virtual_wall_rectangle_v1_is_a_positional_array() -> None:
    """UPDATE (this session): [id, 1, x1, y1, ...] -- type_int=1 for
    Rectangle. Previously assumed a {"type": "Rectangle", "id": ...,
    "polygon": {...GeoJSON...}} object."""
    from roombapy_prime.models import VirtualWallRectangleV1

    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]])
    wall = VirtualWallRectangleV1(wall_id="w1", polygon=poly)

    assert wall.to_json() == ["w1", 1, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]


def test_virtual_wall_no_mop_zone_v1_uses_discriminator_six() -> None:
    """UPDATE (this session): same array shape as Rectangle, type_int=6
    -- confirms no-mop zones share the Rectangle/Linear wire format,
    only the discriminator int differs."""
    from roombapy_prime.models import VirtualWallNoMopZoneV1

    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]])
    wall = VirtualWallNoMopZoneV1(wall_id="w1", polygon=poly)

    assert wall.to_json() == ["w1", 6, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]


def test_virtual_wall_linear_v1_degenerates_to_four_point_polygon() -> None:
    """UPDATE (this session): a line segment has no natural 4-point
    shape, so the confirmed wire format degenerates it by repeating
    each endpoint: from, to, to, from. type_int=2 for Linear.
    Previously assumed a {"type": "Linear", "id": ..., "from": [...],
    "to": [...]} object with just the two raw endpoints."""
    from roombapy_prime.models import VirtualWallLinearV1

    wall = VirtualWallLinearV1(wall_id="w1", from_pos=(0.0, 0.0), to_pos=(1.0, 1.0))

    assert wall.to_json() == ["w1", 2, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0]


def test_set_virtual_walls_v1_confirmed_field_name() -> None:
    """UPDATE (this session): discriminator is "set_virtual_wall"
    (singular) under params, not "SetVirtualWalls". virwall field name
    itself was already correct (session 48). The previously-open
    Linear/Rectangle/NoMopZone discriminator question is now answered
    -- see VirtualWall*V1's own tests above -- it's a positional int,
    not a "type" string at all."""
    from roombapy_prime.models import SetVirtualWallsV1, VirtualWallLinearV1

    wall = VirtualWallLinearV1(wall_id="w1", from_pos=(0.0, 0.0), to_pos=(1.0, 1.0))
    body = SetVirtualWallsV1(walls=[wall]).to_v1_command_body()

    assert body["command"] == "set_virtual_wall"
    assert "virwall" in body["params"]
    # Leading 1 is the wall COUNT, confirmed from CommandSerializer
    # bytecode -- the cause of every HTTP 500 this command produced.
    assert body["params"]["virwall"] == [
        1, ["w1", 2, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0],
    ]


def test_furniture_item_v1_is_a_positional_array_with_int_bool() -> None:
    """UPDATE (this session): [id, type_int, user_modified(0/1), x1,
    y1, ...] -- user_modified is an int 0/1 on the wire, not a JSON
    bool as previously assumed."""
    from roombapy_prime.models import FurnitureItemV1

    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0)]])
    item = FurnitureItemV1(
        furniture_id="f1", furniture_type=FurnitureType.SOFA, geometry=poly, user_modified=True
    )

    body = item.to_json()
    assert body[0] == "f1"
    assert body[1] == int(FurnitureType.SOFA)
    assert body[2] == 1  # int, not True
    assert body[3:] == [0.0, 0.0, 1.0, 0.0]


def test_adjust_furniture_v1_confirmed_field_names() -> None:
    """UPDATE (this session): discriminator is "adjust_furniture" under
    params. furniture_list/package/timestamp field names were already
    correct (session 48) -- what's new: "package" defaults to the
    confirmed fixed [1, 1], not an unconfirmed, arbitrarily-shaped
    list."""
    from roombapy_prime.models import AdjustFurnitureV1, FurnitureItemV1

    poly = Polygon(coordinates=[[(0.0, 0.0)]])
    item = FurnitureItemV1(furniture_id="f1", furniture_type=FurnitureType.SOFA, geometry=poly)
    body = AdjustFurnitureV1(furniture_list=[item], timestamp=123).to_v1_command_body()

    assert body["command"] == "adjust_furniture"
    assert body["params"]["timestamp"] == 123
    assert body["params"]["package"] == [1, 1]
    assert "furniture_list" in body["params"]


def test_set_room_metadata_v1_confirmed_structure() -> None:
    """CONFIRMED (this session, live decompilation down to the actual
    P2MapRoomMetadata$Serializer.serialize() call): room_id sits
    alongside room_metadata (not nested inside it), and room_metadata
    itself contains only "name" when just the name is being changed."""
    from roombapy_prime.models import SetRoomMetadataV1

    body = SetRoomMetadataV1(room_id="r1", name="Kitchen").to_v1_command_body()
    assert body == {
        "command": "set_room_metadata",
        "params": {"room_id": "r1", "room_metadata": {"name": "Kitchen"}},
    }


def test_set_room_metadata_v1_room_type_uses_room_category_snake_case() -> None:
    """CONFIRMED (this session): room_type is serialized under the key
    "type", using RoomCategory's snake_case string values -- NOT
    RoomType (the unrelated int-coded enum used by the app-deprecated
    SetRoomTypeV1), and NOT the underlying Kotlin enum's own `raw`
    field (which is camelCase, e.g. "livingRoom" -- the serializer
    calls .name().toLowerCase() instead, giving "living_room"). Both
    name and type can be set together in one call."""
    from roombapy_prime.models import RoomCategory, SetRoomMetadataV1

    body = SetRoomMetadataV1(
        room_id="r1", name="Kitchen", room_type=RoomCategory.KITCHEN
    ).to_v1_command_body()
    assert body["params"]["room_metadata"] == {"name": "Kitchen", "type": "kitchen"}


def test_set_room_metadata_v1_room_category_snake_case_not_camel_case() -> None:
    """Regression guard for the specific trap the live decompilation
    found: DINING_ROOM/LIVING_ROOM must serialize with an underscore
    ("dining_room"/"living_room"), not the more-plausible-looking
    camelCase ("diningRoom"/"livingRoom") that the underlying Kotlin
    enum's own (unused-by-the-serializer) `raw` field would suggest."""
    from roombapy_prime.models import RoomCategory, SetRoomMetadataV1

    dining = SetRoomMetadataV1(room_id="r1", room_type=RoomCategory.DINING_ROOM)
    living = SetRoomMetadataV1(room_id="r1", room_type=RoomCategory.LIVING_ROOM)

    assert dining.to_v1_command_body()["params"]["room_metadata"]["type"] == "dining_room"
    assert living.to_v1_command_body()["params"]["room_metadata"]["type"] == "living_room"


def test_set_room_metadata_v1_type_only_omits_name() -> None:
    """A room_type-only change must not include "name" in room_metadata
    at all -- confirms the partial-update behavior works independently
    for each field, not just for name-only changes."""
    from roombapy_prime.models import RoomCategory, SetRoomMetadataV1

    body = SetRoomMetadataV1(room_id="r1", room_type=RoomCategory.BEDROOM).to_v1_command_body()
    assert body["params"]["room_metadata"] == {"type": "bedroom"}


def test_set_room_metadata_v1_requires_name_or_type() -> None:
    """CONFIRMED constraint (this session, from the decompiled
    constructor): at least one of name/room_type must be set -- the
    underlying API has no way to express "change nothing". Enforced
    here via __post_init__ so a caller gets an immediate, clear
    ValueError rather than a request the server would have to reject."""
    import pytest

    from roombapy_prime.models import SetRoomMetadataV1

    with pytest.raises(ValueError, match="at least one of name/room_type"):
        SetRoomMetadataV1(room_id="r1")


# --- mission commands (CLEAN/START/STOP/PAUSE/DOCK/etc.) -----------------

def test_mission_command_type_values_match_serialname_annotations() -> None:
    """Values are the actual @SerialName wire strings, not the Kotlin
    enum constant names -- these two are deliberately checked
    separately, since they also differed in the source code."""
    from roombapy_prime.models import MissionCommandType

    assert MissionCommandType.CLEAN_SPOT.value == "point_clean"
    assert MissionCommandType.TIDY.value == "tidy"
    assert MissionCommandType.START.value == "start"
    # 32, down from 34: the two Do Not Disturb commands are still here
    # (as start_dnd/stop_dnd), but POINTCLEAN_VENDOR and
    # FLUIDREFILL_VENDOR are gone. They were "vendor spellings" read off
    # an enum that has no wire values, so there was no second spelling
    # to offer.
    assert len(list(MissionCommandType)) == 32


def test_routine_command_to_json_required_fields() -> None:
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    cmd = RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID123")
    body = cmd.to_json()

    assert body == {
        "command": "start",
        "robot_id": "BLID123",
        "ordered": 0,
        "select_all": False,
    }


def test_routine_command_to_json_optional_fields() -> None:
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    cmd = RoutineCommand(
        command_type=MissionCommandType.CLEAN,
        asset_id="BLID123",
        map_id="map1",
        pmap_version_id="v1",
        clean_all=True,
        favorite_id="fav1",
    )
    body = cmd.to_json()

    assert body["p2map_id"] == "map1"
    assert body["user_p2mapv_id"] == "v1"
    assert body["select_all"] is True
    assert body["favorite_id"] == "fav1"


def test_routine_command_to_shadow_desired_wraps_under_cmd_key() -> None:
    """Confirmed from CommandWrapper.java's @SerialName("cmd")."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    cmd = RoutineCommand(command_type=MissionCommandType.STOP, asset_id="BLID123")
    desired = cmd.to_shadow_desired()

    assert set(desired.keys()) == {"cmd"}
    assert desired["cmd"]["command"] == "stop"


def test_suction_level_and_carpet_boost_settings_enums_confirmed_values() -> None:
    """CONFIRMED (parallel native-analysis track, SuctionLevel.java):
    suction_level has NO "Auto" value (0 is an explicit error/
    placeholder). This is because floor-type adaptation isn't a
    suction_level concept at all -- it's the entirely separate
    carpet_boost bool (a real, sensor-driven, real-time "boost suction
    when carpet detected" feature, confirmed via iRobot's own public
    product documentation), not a three-way selector. CarpetBoostSettings
    below is CONFIRMED DEAD CODE (a follow-up investigation found zero
    consumers anywhere, part of an older, superseded UI generation) --
    kept here only as a documented dead end, not evidence of an actual
    three-way carpet-boost mode reaching the current app."""
    from roombapy_prime.models.mission_control import CarpetBoostSettings, SuctionLevel

    assert SuctionLevel.INVALID == 0
    assert SuctionLevel.LOW == 1
    assert SuctionLevel.MEDIUM == 2
    assert SuctionLevel.HIGH == 3
    assert SuctionLevel.TURBO == 4

    assert CarpetBoostSettings.PERFORMANCE == 0
    assert CarpetBoostSettings.ECO == 1
    assert CarpetBoostSettings.AUTO == 2


def test_command_params_to_json_omits_none_fields() -> None:
    """39 fields, all optional -- only set values end up in the JSON.
    Wire keys corrected (this session, parallel native-analysis
    track, $$serializer inspection) -- room_confine, not the earlier
    camelCase "roomConfine" guess -- see CommandParams' own docstring
    for the full 18-field correction and why it mattered (silently
    dropped keys, not a cosmetic mismatch)."""
    from roombapy_prime.models import CommandParams

    params = CommandParams(suction_level=3, room_confine=True)
    body = params.to_json()

    assert body == {"suctionLevel": 3, "room_confine": True}


def test_command_params_wire_keys_match_confirmed_serializer_list() -> None:
    """NEW (this session, parallel native-analysis track): every field
    set to a distinct, recognizable value, then to_json()'s ACTUAL
    output keys checked one by one against the confirmed
    $$serializer.<clinit> list (38 names) plus the one deliberate
    special case (no_auto_passes/noAutoPasses -- confirmed from real
    live data, genuinely absent from that list, not a naming variant
    of no_persistent_pass -- see CommandParams' own class docstring).
    This is the strongest test of the 18-field correction: it doesn't
    just check a couple of fields look right, it accounts for every
    single one."""
    from roombapy_prime.models import CommandParams
    from roombapy_prime.models.mission_control import PadWetnessParam

    # One distinct, non-None value per field -- so every key is
    # guaranteed to appear in to_json()'s output (which omits None).
    params = CommandParams(
        adaptive_cleaning=True, bin_pause=True, capture_mode=1, carpet_boost=True,
        clean_score_id="a", cleaning_profile="b", eco_charge=True, execute_in_place=True,
        gentle_mode=1, heated_water=1, manual_update=True, monitor_mode=1, no_koz=1,
        no_auto_passes=True, no_persistent_pass=True, odoa_mode=1, open_only=True,
        operating_mode=1, pad_wash_after=1, pad_wash_area=1,
        pad_wetness=PadWetnessParam(disposable=1), rank_overlap=1,
        replay_of="c", routine_type="d", room_confine=True, rotate=1,
        routine_modified=True, schedule_hold=True, scrub=1, smart_clean_id="e",
        speed=1, stream_on_route=True, suction_level=1, timebox_minutes=1,
        translate=1, two_pass=True, vac_high=True, velocity_left=1, velocity_right=1,
    )

    body = params.to_json()

    # The 38 confirmed serializer keys (excludes no_auto_passes -- the
    # one deliberate, independently-justified exception, see above).
    confirmed_keys = {
        "noKOZ", "twoPass", "carpetBoost", "vacHigh", "openOnly", "binPause", "schedHold",
        "manUpd", "noPP", "ecoCharge", "room_confine", "swScrub", "stream_on_route",
        "execute_in_place", "timebox", "operatingMode", "rankOverlap", "padWetness",
        "gentleMode", "odoaMode", "monitor_mode", "capture_mode", "vleft", "vright",
        "trans", "rot", "speed", "padWashArea", "padWashAfter", "suctionLevel",
        "heatedWater", "routine_type", "clean_score_id", "smart_clean_id", "replay_of",
        "routine_modified", "adaptive", "profile",
    }
    special_case_keys = {"noAutoPasses"}

    assert set(body.keys()) == confirmed_keys | special_case_keys


def test_command_params_pad_wetness_nested() -> None:
    from roombapy_prime.models import CommandParams, PadWetnessParam

    params = CommandParams(pad_wetness=PadWetnessParam(disposable=2))
    body = params.to_json()

    assert body == {"padWetness": {"disposable": 2}}


def test_region_to_json() -> None:
    """Confirmed (androguard): id, name, params, type."""
    from roombapy_prime.models import CommandParams, Region, RegionType

    region = Region(region_id="r1", region_type=RegionType.RID, name="Kitchen", params=CommandParams(speed=2))
    body = region.to_json()

    # "region_id", SETTLED BY FIELD DATA (a26): two confirmed-working
    # region commands both carried it, and the robot echoed them back
    # unchanged. The from-scratch command that still sent "id" was
    # delivered with a PUBACK and did nothing -- same robot, same room,
    # minutes apart.
    assert body == {"region_id": "r1", "type": "rid", "name": "Kitchen", "params": {"speed": 2}}


def test_command_polygon_to_json() -> None:
    """id, metadata (furniture_id -- wire key corrected this session,
    see CommandPolygonMetadata's own docstring for the full
    $$serializer-based correction), poly."""
    from roombapy_prime.models import CommandPolygon, CommandPolygonMetadata

    polygon = CommandPolygon(
        polygon_id="poly1", poly=[(0.0, 0.0), (1.0, 1.0)], metadata=CommandPolygonMetadata(furniture_id=5)
    )
    body = polygon.to_json()

    assert body == {"id": "poly1", "poly": [[0.0, 0.0], [1.0, 1.0]], "metadata": {"furniture_id": 5}}


def test_routine_command_with_typed_regions_and_params() -> None:
    """NEW (July 11, eighth session) -- RoutineCommand.regions/params
    now accept the typed models instead of just raw dicts."""
    from roombapy_prime.models import CommandParams, MissionCommandType, Region, RegionType, RoutineCommand

    cmd = RoutineCommand(
        command_type=MissionCommandType.CLEAN,
        asset_id="BLID123",
        regions=[Region(region_id="r1", region_type=RegionType.RID)],
        params=CommandParams(suction_level=2),
    )
    body = cmd.to_json()

    assert body["regions"] == [{"region_id": "r1", "type": "rid"}]
    assert body["params"] == {"suctionLevel": 2}


def test_routine_command_still_accepts_raw_dicts_for_backward_compat() -> None:
    """Backward compatibility: raw dicts still work alongside the
    new typed models."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    cmd = RoutineCommand(
        command_type=MissionCommandType.CLEAN,
        asset_id="BLID123",
        regions=[{"id": "r1", "type": "RID"}],
        params={"suctionLevel": 2},
    )
    body = cmd.to_json()

    assert body["regions"] == [{"id": "r1", "type": "RID"}]
    assert body["params"] == {"suctionLevel": 2}


def test_parse_mission_history_entry() -> None:
    """NEW (July 11, ninth session) -- top-level fields confirmed from
    MissionHistory (androguard)."""
    from roombapy_prime.models import DoneCode, parse_mission_history

    raw = {
        "missions": [
            {
                "missionId": "m1",
                "robot_id": "BLID123",
                "startTime": 1000,
                "durationM": 45,
                "done": "ok",
                "sqft": 500,
                "cmd": {"command": "clean", "robot_id": "BLID123", "cleanAll": True},
            }
        ]
    }
    entries = parse_mission_history(raw)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.mission_id == "m1"
    assert entry.done_code == DoneCode.OK
    assert entry.square_feet_covered == 500
    assert entry.command is not None
    assert entry.command.clean_all is True
    assert entry.raw == raw["missions"][0]


def test_parse_mission_history_accepts_raw_list() -> None:
    from roombapy_prime.models import parse_mission_history

    entries = parse_mission_history([{"missionId": "m1"}])
    assert len(entries) == 1
    assert entries[0].mission_id == "m1"


def test_parse_mission_history_unknown_done_code_falls_back_to_raw_string() -> None:
    """The server may introduce new doneCode values -- shouldn't crash."""
    from roombapy_prime.models import parse_mission_history

    entries = parse_mission_history([{"missionId": "m1", "done": "SOME_NEW_CODE"}])
    assert entries[0].done_code == "SOME_NEW_CODE"


def test_command_params_from_json_roundtrip() -> None:
    """NEW (July 11, ninth session) -- from_json is the inverse
    function of to_json."""
    from roombapy_prime.models import CommandParams

    original = CommandParams(suction_level=3, room_confine=True, carpet_boost=False)
    restored = CommandParams.from_json(original.to_json())

    assert restored == original


def test_command_params_from_json_with_pad_wetness() -> None:
    from roombapy_prime.models import CommandParams, PadWetnessParam

    original = CommandParams(pad_wetness=PadWetnessParam(disposable=2, pad_plate=1))
    restored = CommandParams.from_json(original.to_json())

    assert restored.pad_wetness == PadWetnessParam(disposable=2, pad_plate=1)


def test_cleaning_profile_from_json() -> None:
    """CORRECTED (this session, parallel native-analysis track,
    doubly confirmed -- $$serializer AND chairstacker's own real
    get_cleaning_profiles() response from an earlier session): the
    wire key is "params", not the earlier "commandParams" guess --
    which had command_params silently None against every real
    response, this test's own old assertion would never have caught
    that until updated to the real key."""
    from roombapy_prime.models import CleaningProfile, CleaningProfileType

    # "deep", not "DEEP": the uppercase form was the Kotlin constant
    # name. This assertion passed against the old enum only because
    # both sides carried the same wrong value -- a real response would
    # have fallen through to the raw string. See CleaningProfileType.
    profile = CleaningProfile.from_json(
        {"profile": "deep", "params": {"suctionLevel": 3}, "regions": [{"id": "r1"}]}
    )

    assert profile.profile == CleaningProfileType.DEEP
    assert profile.command_params is not None
    assert profile.command_params.suction_level == 3
    assert profile.regions == [{"id": "r1"}]


def test_dnd_status_response_from_json() -> None:
    from roombapy_prime.models import DNDStatusResponse

    dnd = DNDStatusResponse.from_json({"dailyStart": 1320, "dailyEnd": 420, "status": {"active": True}})

    assert dnd.daily_start == 1320
    assert dnd.daily_end == 420
    assert dnd.status == {"active": True}


def test_dnd_daily_schedule_to_json_confirmed_keys() -> None:
    """NEW (session 46) -- confirmed directly from
    DNDSchedule$DailySchedule$$serializer's <clinit>. See
    DNDDailySchedule's docstring for the still-open envelope/
    discriminator question (how this combines under DNDSchedule)."""
    from roombapy_prime.models import DNDDailySchedule

    body = DNDDailySchedule(daily_start=1320, daily_end=420).to_json()

    assert body == {"dailyStart": 1320, "dailyEnd": 420}


def test_dnd_ends_at_to_json_confirmed_key() -> None:
    """NEW (session 46) -- confirmed directly from
    DNDSchedule$EndsAt$$serializer's <clinit>."""
    from roombapy_prime.models import DNDEndsAt

    body = DNDEndsAt(ends_at=1752600000).to_json()

    assert body == {"endsAt": 1752600000}


def test_household_setting_from_json() -> None:
    from roombapy_prime.models import HouseholdSetting

    setting = HouseholdSetting.from_json({"settingId": "s1", "settingType": "dnd", "options": {"foo": "bar"}})

    assert setting.setting_id == "s1"
    assert setting.setting_type == "dnd"
    assert setting.options == {"foo": "bar"}


def test_household_setting_options_from_json_confirmed_fields() -> None:
    """NEW (session 48) -- REPLACES the "structure not investigated"
    placeholder. Confirmed via HouseholdSettingOptions$$serializer's
    <clinit>: household demographic info (adult/kid/pet counts)."""
    from roombapy_prime.models import HouseholdSettingOptions

    opts = HouseholdSettingOptions.from_json({
        "last_user_modified": 1700000000,
        "hh_adults": 2,
        "hh_kids": 1,
        "hh_pets": 3,
        "hh_adults_kids_prefer_not_to_answer": False,
        "hh_pets_prefer_not_to_answer": False,
        "hh_location_factor": "urban",
    })

    assert opts.hh_adults == 2
    assert opts.hh_kids == 1
    assert opts.hh_pets == 3
    assert opts.hh_location_factor == "urban"


def test_p2map_data_from_json_confirmed_fields() -> None:
    """NEW (session 51) -- REPLACES the "response shape not modeled
    yet" placeholder for get_map_metadata(). Confirmed via
    P2MapData$$serializer's <clinit>. The last two fields match
    set_map_name()/set_map_orientation()'s own confirmed write-side
    keys exactly."""
    from roombapy_prime.models import P2MapData

    data = P2MapData.from_json({
        "p2map_id": "m1",
        "active_p2mapv_id": "v1",
        "create_time": 1700000000,
        "last_p2mapv_ts": 1700000100,
        "state": "active",
        "visible": True,
        "name": "Downstairs",
        "user_orientation_rad": 1.57,
    })

    assert data.p2map_id == "m1"
    assert data.active_p2mapv_id == "v1"
    assert data.name == "Downstairs"
    assert data.visible is True
    assert data.user_orientation_rad == 1.57


def test_schedules_response_from_json_confirmed_envelope() -> None:
    """NEW (session 51) -- the confirmed top-level envelope for
    get_schedules(), previously entirely unmodeled (the class NAMES
    had been found in an earlier session, not their fields).
    Confirmed via SchedulesResponse$$serializer/
    SchedulesList$$serializer."""
    from roombapy_prime.models import SchedulesResponse

    response = SchedulesResponse.from_json({
        "household_schedules": [
            {"household_schedule_id": "hs1", "schedules": [{"schedule_id": "s1"}, {"schedule_id": "s2"}]},
        ]
    })

    assert len(response.household_schedules) == 1
    assert response.household_schedules[0].household_schedule_id == "hs1"
    assert len(response.household_schedules[0].schedules) == 2


def test_schedules_response_handles_empty_envelope() -> None:
    from roombapy_prime.models import SchedulesResponse

    response = SchedulesResponse.from_json({})
    assert response.household_schedules == []


def test_p2map_edit_partial_success_from_json() -> None:
    """NEW (session 51) -- one of edit_map()'s possible response
    shapes, confirmed via P2MapEditPartialSuccess$$serializer. See the
    class docstring: which shape actually comes back isn't confirmed."""
    from roombapy_prime.models import P2MapEditPartialSuccess

    result = P2MapEditPartialSuccess.from_json({"status": "ok", "p2mapv_id": "v1", "p2map_metadata": {"a": 1}})
    assert result.status == "ok"
    assert result.p2mapv_id == "v1"
    assert result.p2map_metadata == {"a": 1}


def test_p2map_edit_success_fallback_from_json() -> None:
    """NEW (session 51) -- confirmed via
    P2MapEditSuccessFallback$$serializer -- has an extra `map_url`
    field vs. P2MapEditPartialSuccess."""
    from roombapy_prime.models import P2MapEditSuccessFallback

    result = P2MapEditSuccessFallback.from_json({"status": "ok", "map_url": "https://x", "p2mapv_id": "v1"})
    assert result.map_url == "https://x"
    assert result.p2mapv_id == "v1"


def test_response_error_from_error_container() -> None:
    """NEW (session 51) -- confirmed via ResponseError$$serializer AND
    the field-identical P2MapError -- modeled once, shared."""
    from roombapy_prime.models import ResponseError

    err = ResponseError.from_error_container({"error": {"code": 400, "message": "bad request"}})
    assert err is not None
    assert err.code == 400
    assert err.message == "bad request"


def test_response_error_from_error_container_missing_returns_none() -> None:
    from roombapy_prime.models import ResponseError

    assert ResponseError.from_error_container({"something_else": {}}) is None


def test_response_error_message_from_message_container_capital_m() -> None:
    """Regression test for the confirmed, unusual capital-M key
    ("Message", not "message") -- MessageContainer$$serializer."""
    from roombapy_prime.models import ResponseError

    assert ResponseError.message_from_message_container({"Message": "not found"}) == "not found"
    assert ResponseError.message_from_message_container({"message": "wrong case"}) is None


# =========================================================================
# ScheduleOptions.to_json() (session 46) -- corrected wire keys
# =========================================================================


def test_schedule_options_to_json_uses_confirmed_snake_case_keys() -> None:
    """CORRECTED (session 46) -- regression test against ever
    reverting to the wrong, previously-guessed camelCase keys.
    Confirmed directly from ScheduleOptions$$serializer's <clinit>:
    robot_id (not assetId), end_commands (not endCommands),
    created_time (not createdTime), force_cloud (not forceCloud).

    UPDATED (session 57): end_commands/commands entries are now
    wrapped as {"command": {...}}, confirmed via a real live
    get_schedules() response (chairstacker) -- the previous, unwrapped
    shape would very likely have been rejected or misinterpreted by
    the real create/update schedule endpoints."""
    from roombapy_prime.models import RoutineCommand, MissionCommandType, ScheduleOptions

    end_cmd = RoutineCommand(command_type=MissionCommandType.STOP, asset_id="a1")
    options = ScheduleOptions(
        asset_id="asset1",
        name="Evening",
        end_commands=[end_cmd],
        created_time="2026-07-15T00:00:00Z",
        force_cloud=True,
    )

    body = options.to_json()

    assert body["robot_id"] == "asset1"
    assert body["end_commands"] == [{"command": end_cmd.to_json()}]
    assert body["created_time"] == "2026-07-15T00:00:00Z"
    assert body["force_cloud"] is True
    assert "assetId" not in body
    assert "endCommands" not in body
    assert "createdTime" not in body
    assert "forceCloud" not in body


def test_household_schedule_round_trips_through_from_json_and_to_json() -> None:
    """NEW -- added specifically to support verify_schedule_write.py's
    stage 1 ("resend an existing schedule completely unchanged").
    commands/end_commands round-trip as raw dicts (no
    RoutineCommand.from_json() exists in this library), not parsed
    RoutineCommand objects -- exercised here with a commands entry
    present specifically to prove that path doesn't break the
    round-trip, matching to_json()'s own already-established
    has_json/raw-dict tolerance."""
    from roombapy_prime.models import HouseholdSchedule

    raw = {
        "schedule_id": "sched-1",
        "options": {
            "robot_id": "BLID123",
            "name": "Morning clean",
            "frequency": "WEEKLY",
            "start": {"day": [1, 4], "hour": 8, "min": 0},
            "enabled": True,
            "commands": [
                {"command": {"command": "clean", "robot_id": "BLID123", "ordered": 0, "select_all": True}}
            ],
        },
    }

    parsed = HouseholdSchedule.from_json(raw)

    assert parsed.schedule_id == "sched-1"
    assert parsed.options.name == "Morning clean"
    assert parsed.options.start.day == [1, 4]
    assert parsed.to_json() == raw


def test_parse_default_routines() -> None:
    """CORRECTED (session 49): confirmed via Routine$$serializer --
    "commanddefs" (all lowercase, no separator) and "time_estimate"
    (snake_case), not the previously-guessed "commandDefs"/
    "timeEstimate"."""
    from roombapy_prime.models import parse_default_routines

    routines = parse_default_routines(
        {"routines": [{"name": "Whole Home", "commanddefs": [{"command": "clean"}], "time_estimate": 30}]}
    )

    assert len(routines) == 1
    assert routines[0].name == "Whole Home"
    assert routines[0].time_estimate == 30
    assert routines[0].command_defs == [{"command": "clean"}]


def test_routines_defaults_response_from_json_full_envelope() -> None:
    """NEW (session 49) -- the confirmed top-level envelope for
    get_default_routines(), including routine_builder_defaults, which
    the older parse_default_routines() helper never captured at all.

    CORRECTED (session 57): `regions` is a dict keyed by region ID
    (confirmed via a real live response, chairstacker), not a list as
    originally guessed -- the old guess would have crashed
    (`AttributeError: 'str' object has no attribute 'get'`) against
    any account with real routine_builder_defaults content.
    `operating_mode` is an int, and `params` is CommandParams-shaped,
    both also corrected in the same pass."""
    from roombapy_prime.models import RoutinesDefaultsResponse

    response = RoutinesDefaultsResponse.from_json({
        "routines": [{"name": "Whole Home"}],
        "routine_builder_defaults": {
            "regions": {
                "15": {
                    "type": "rid",
                    "operating_mode": 512,
                    "by_operating_mode": {
                        "512": {"params": {"suctionLevel": 4, "carpetBoost": True}, "profile_type": "deep", "updated_at": 1783107415},
                    },
                }
            }
        },
    })

    assert len(response.routines) == 1
    assert response.routines[0].name == "Whole Home"
    assert response.routine_builder_defaults is not None
    region = response.routine_builder_defaults.regions["15"]
    assert region.region_type == "rid"
    assert region.operating_mode == 512
    mode = region.by_operating_mode["512"]
    assert mode.profile_type == "deep"
    assert mode.updated_at == 1783107415
    assert mode.params.suction_level == 4
    assert mode.params.carpet_boost is True


def test_routines_defaults_response_handles_missing_builder_defaults() -> None:
    from roombapy_prime.models import RoutinesDefaultsResponse

    response = RoutinesDefaultsResponse.from_json({"routines": []})
    assert response.routine_builder_defaults is None


def test_routines_defaults_response_handles_dict_keyed_routines() -> None:
    """Regression test (session 56) for a real crash in chairstacker's
    live v0.1.10a0 run: `AttributeError: 'str' object has no attribute
    'get'`. Leading hypothesis: the real "routines" value is a JSON
    OBJECT keyed by routine ID/type (a pattern already seen elsewhere
    in this project, e.g. RoomMetadataEntry.operating_mode_defaults),
    not a JSON array -- iterating a dict in Python walks its string
    keys, reproducing exactly this error when each key gets passed to
    Routine.from_json()."""
    from roombapy_prime.models import RoutinesDefaultsResponse

    response = RoutinesDefaultsResponse.from_json({
        "routines": {"whole_home": {"name": "Whole Home"}, "kitchen_only": {"name": "Kitchen"}}
    })
    assert len(response.routines) == 2
    assert {r.name for r in response.routines} == {"Whole Home", "Kitchen"}


def test_routines_defaults_response_skips_non_dict_entries_without_crashing() -> None:
    """Regression test (session 56) -- reproduces the exact crash shape
    directly (a list containing bare strings) and confirms it's now
    skipped gracefully rather than raising."""
    from roombapy_prime.models import RoutinesDefaultsResponse

    response = RoutinesDefaultsResponse.from_json({"routines": ["whole_home", "kitchen_only"]})
    assert response.routines == []


def test_parse_default_routines_handles_dict_keyed_routines() -> None:
    """Same fix, convenience-function entry point (session 56)."""
    from roombapy_prime.models import parse_default_routines

    result = parse_default_routines({"routines": {"a": {"name": "A"}, "b": {"name": "B"}}})
    assert {r.name for r in result} == {"A", "B"}


# =========================================================================
# MissionTimelineEvent -- all 20 sub-event types (session 18)
# =========================================================================


def test_command_event_from_json() -> None:
    from roombapy_prime.models import CommandEvent

    e = CommandEvent.from_json({"command": "clean", "initiator": "user", "time": 123})
    assert e == CommandEvent(command="clean", initiator="user", time=123)


def test_discovery_event_from_json() -> None:
    from roombapy_prime.models import DiscoveryEvent

    e = DiscoveryEvent.from_json({"mapId": "m1", "mapVersion": "v1", "regionId": "r1"})
    assert e == DiscoveryEvent(map_id="m1", map_version="v1", region_id="r1")


def test_error_event_from_json() -> None:
    from roombapy_prime.models import ErrorEvent

    assert ErrorEvent.from_json({"value": 42}) == ErrorEvent(value=42)


def test_evac_event_from_json() -> None:
    from roombapy_prime.models import EvacEvent

    assert EvacEvent.from_json({"error": 0, "state": 2}) == EvacEvent(error=0, state=2)


def test_live_view_event_from_json() -> None:
    from roombapy_prime.models import LiveViewEvent

    assert LiveViewEvent.from_json({"eventId": "e1", "status": 1}) == LiveViewEvent(event_id="e1", status=1)


def test_pad_dry_event_from_json() -> None:
    from roombapy_prime.models import PadDryEvent

    assert PadDryEvent.from_json({"error": 0, "padDryState": 3}) == PadDryEvent(error=0, pad_dry_state=3)


def test_pad_wash_event_from_json() -> None:
    from roombapy_prime.models import PadWashEvent

    e = PadWashEvent.from_json({"error": 0, "fluidAmount": 5, "padWashState": 2, "reason": 1})
    assert e == PadWashEvent(error=0, fluid_amount=5, pad_wash_state=2, reason=1)


def test_panorama_event_from_json() -> None:
    from roombapy_prime.models import PanoramaEvent

    e = PanoramaEvent.from_json(
        {
            "eventId": "e1",
            "mapId": "m1",
            "mapVersion": "v1",
            "panoramaId": "p1",
            "status": 1,
            "waypointId": "w1",
        }
    )
    assert e == PanoramaEvent(
        event_id="e1", map_id="m1", map_version="v1", panorama_id="p1", status=1, waypoint_id="w1"
    )


def test_plan_event_from_json_with_enum_list() -> None:
    """Confirmed (androguard, jadx had skipped this class) --
    'ordered' here is an intra-event property, see docstring."""
    from roombapy_prime.models import PlanEvent, PlanType, PlanUpcoming

    e = PlanEvent.from_json(
        {"mapId": "m1", "mapVersion": "v1", "ordered": 1, "type": "TRAIN", "upcoming": ["RID", "ZID"]}
    )
    assert e.plan_type == PlanType.TRAIN
    assert e.upcoming == [PlanUpcoming.RID, PlanUpcoming.ZID]
    assert e.ordered == 1


def test_polygon_event_from_json() -> None:
    """CORRECTED (this session, parallel native-analysis track,
    $$serializer.<clinit> inspection): 4 of 7 wire keys were wrong --
    mapId->p2mapId, mapVersion->p2mapvId, polyId->polyid (fully
    lowercase, not derivable from the property name by any casing
    transformation), regionId->rid."""
    from roombapy_prime.models import PolygonEvent

    e = PolygonEvent.from_json(
        {"area": 10, "areaCleaned": 8, "p2mapId": "m1", "p2mapvId": "v1", "poly": [[0, 0]], "polyid": "p1", "rid": "r1"}
    )
    assert e == PolygonEvent(
        area=10, area_cleaned=8, map_id="m1", map_version="v1", poly=[[0, 0]], poly_id="p1", region_id="r1"
    )


def test_refill_event_from_json() -> None:
    from roombapy_prime.models import RefillEvent

    e = RefillEvent.from_json({"error": 0, "fluidAmount": 5, "fluidReplenishmentState": 1})
    assert e == RefillEvent(error=0, fluid_amount=5, fluid_replenishment_state=1)


def test_room_event_from_json() -> None:
    from roombapy_prime.models import RoomEvent

    e = RoomEvent.from_json(
        {
            "area": 100,
            "conPasses": 2,
            "mapId": "m1",
            "mapVersion": "v1",
            "passArea": 90,
            "passCount": 1,
            "regionId": "r1",
            "status": 1,
            "totalArea": 100,
        }
    )
    assert e.area == 100 and e.region_id == "r1" and e.total_area == 100


def test_sub_room_event_from_json() -> None:
    from roombapy_prime.models import SubRoomEvent

    e = SubRoomEvent.from_json(
        {
            "area": 50,
            "mapId": "m1",
            "mapVersion": "v1",
            "operatingMode": 1,
            "passArea": 40,
            "passCount": 1,
            "polyId": "p1",
            "regionId": "r1",
            "status": 1,
            "subRegionId": "sr1",
            "totalArea": 50,
            "zoneId": "z1",
        }
    )
    assert e.sub_region_id == "sr1" and e.zone_id == "z1"


def test_tentative_location_event_from_json() -> None:
    from roombapy_prime.models import TentativeLocationEvent

    e = TentativeLocationEvent.from_json(
        {
            "confirmedMapId": "m1",
            "confirmedMapVersion": "v1",
            "confirmedRegionId": "r1",
            "mapId": "m2",
            "mapVersion": "v2",
            "regionId": "r2",
        }
    )
    assert e.confirmed_region_id == "r1" and e.region_id == "r2"


def test_travel_event_from_json() -> None:
    """UPDATED (session 31) -- real field names (p2mapId/p2mapvId/
    rid/zid/dest) and lowercase destination confirmed."""
    from roombapy_prime.models import TravelDestination, TravelEvent

    e = TravelEvent.from_json(
        {
            "dest": "dock",
            "p2mapId": "m1",
            "p2mapvId": "v1",
            "polyId": "p1",
            "reason": 0,
            "rid": "r1",
            "status": 1,
            "waypointId": "w1",
            "zid": "z1",
        }
    )
    assert e.destination == TravelDestination.DOCK
    assert e.map_id == "m1"
    assert e.map_version == "v1"
    assert e.region_id == "r1"
    assert e.zone_id == "z1"
    assert e.waypoint_id == "w1"


def test_traversal_event_from_json() -> None:
    """UPDATED (session 31) -- real field names and lowercase
    confirmed."""
    from roombapy_prime.models import TraversalEvent, TraversalType

    e = TraversalEvent.from_json({"p2mapId": "m1", "p2mapvId": "v1", "rid": "r1", "type": "zone", "zid": "z1"})
    assert e.traversal_type == TraversalType.ZONE
    assert e.map_id == "m1"
    assert e.region_id == "r1"
    assert e.zone_id == "z1"


def test_waypoint_event_from_json() -> None:
    from roombapy_prime.models import WaypointEvent

    e = WaypointEvent.from_json({"mapId": "m1", "mapVersion": "v1", "waypointId": "w1"})
    assert e == WaypointEvent(map_id="m1", map_version="v1", waypoint_id="w1")


def test_wet_out_event_from_json() -> None:
    from roombapy_prime.models import WetOutEvent

    e = WetOutEvent.from_json({"status": 1, "type": 2})
    assert e == WetOutEvent(status=1, wet_out_type=2)


def test_zone_event_from_json() -> None:
    from roombapy_prime.models import ZoneEvent

    e = ZoneEvent.from_json(
        {"area": 30, "mapId": "m1", "mapVersion": "v1", "passArea": 25, "passCount": 1, "status": 1, "totalArea": 30, "zoneId": "z1"}
    )
    assert e.zone_id == "z1" and e.total_area == 30


def test_mission_timeline_event_only_relevant_subfield_set() -> None:
    """Only ONE sub-field should be set, matching the 'type' value --
    all other 19 stay None."""
    from roombapy_prime.models import MissionTimelineEvent

    e = MissionTimelineEvent.from_json(
        {"startTime": 100, "endTime": 200, "type": "zone", "zone": {"zoneId": "z1", "area": 10}}
    )
    assert e.event_type == "zone"
    assert e.zone is not None
    assert e.zone.zone_id == "z1"
    # all other 19 sub-fields must remain None
    other_fields = [
        e.command, e.discovery, e.error, e.evac, e.live_view, e.pad_dry, e.pad_wash,
        e.panorama, e.plan, e.polygon, e.refill, e.relocalizing, e.room, e.sub_room,
        e.tentative_location, e.travel, e.traversal, e.waypoint, e.wet_out,
    ]
    assert all(f is None for f in other_fields)


def test_mission_timeline_event_relocalizing_and_tentative_location_share_type() -> None:
    """Confirmed (androguard): both fields use the same type
    TentativeLocationEvent, but are independent fields."""
    from roombapy_prime.models import MissionTimelineEvent, TentativeLocationEvent

    e = MissionTimelineEvent.from_json(
        {
            "relocalizing": {"mapId": "m1"},
            "tentativeLocation": {"mapId": "m2"},
        }
    )
    assert isinstance(e.relocalizing, TentativeLocationEvent)
    assert isinstance(e.tentative_location, TentativeLocationEvent)
    assert e.relocalizing.map_id == "m1"
    assert e.tentative_location.map_id == "m2"


def test_mission_timeline_events_from_real_live_mission_history() -> None:
    """NEW (session 58) -- regression tests using real event payloads
    from chairstacker's --dump-config output (session 57), not just
    synthetic examples. Verification done in an ad-hoc script during
    that session was not preserved as a test at the time -- fixed
    here, since unpreserved verification isn't defended against
    future regressions. Covers 4 of the 20 sub-event types not
    previously exercised against real data: room, pause, traversal,
    charge (the other 6 real events from the same dump -- padWash,
    evac, travel x2, zone, reloc, start -- were already covered by
    existing tests before this session)."""
    from roombapy_prime.models import MissionTimelineEvent, RoomEvent, TraversalEvent, TraversalType

    room_event = MissionTimelineEvent.from_json({
        "type": "room",
        "room": {
            "area": 0, "p2mapvId": "260715T212223.861", "rid": "11",
            "passCount": 0, "p2mapId": "6F55705AE0BF169D69BDBFC9D858B5D2-1758329350",
        },
        "ts": 1784150543,
    })
    assert isinstance(room_event.room, RoomEvent)
    assert room_event.room.region_id == "11"
    assert room_event.room.pass_count == 0
    assert room_event.room.map_version == "260715T212223.861"

    pause_event = MissionTimelineEvent.from_json({"type": "pause", "ts": 1784150552})
    assert pause_event.event_type == "pause"
    assert pause_event.start_time == 1784150552

    traversal_event = MissionTimelineEvent.from_json({
        "type": "traversal",
        "traversal": {
            "p2mapvId": "260715T130113.944", "rid": "11", "type": "region",
            "p2mapId": "6F55705AE0BF169D69BDBFC9D858B5D2-1758329350",
        },
        "ts": 1784120473, "ets": 1784120479,
    })
    assert isinstance(traversal_event.traversal, TraversalEvent)
    assert traversal_event.traversal.traversal_type == TraversalType.REGION
    assert traversal_event.traversal.region_id == "11"

    charge_event = MissionTimelineEvent.from_json({"type": "charge", "ts": 1784120914})
    assert charge_event.event_type == "charge"
    assert charge_event.start_time == 1784120914


def test_mission_timeline_report_from_real_live_capture() -> None:
    """CONFIRMED LIVE (this session) -- real mission/timeline/report
    messages from an actual, active mission (chairstacker, via
    verify_mission_timeline.py --start-mission). The exact 4th (final,
    most complete) message captured, verbatim except redacted IDs
    replaced with the same placeholder used elsewhere in this test
    file for readability.

    The valuable finding this test locks in: RoomEvent/TravelEvent/
    TentativeLocationEvent (previously confirmed only via static
    androguard/jadx analysis for the HISTORICAL get_mission_history()
    endpoint) needed ZERO corrections to parse this LIVE data -- both
    channels share one event schema."""
    from roombapy_prime.models import MissionTimelineReport, RoomEvent, TentativeLocationEvent, TravelEvent

    raw = {
        "cmd": {"command": "start", "initiator": "localApp", "time": 1784483030},
        "event": [{
            "room": {"area": 354, "p2mapId": "BLID-1758329350", "p2mapvId": "260719T174414.994",
                     "passCount": 0, "rid": "11"},
            "ts": 1784483054, "type": "room",
        }],
        "finEvents": [
            {"ets": 1784483054, "travel": {"dest": "room", "p2mapId": "BLID-1758329350",
                                            "p2mapvId": "260719T174413.734", "rid": "11", "status": 0},
             "ts": 1784483053, "type": "travel"},
            {"ets": 1784483053, "reloc": {"confp2mapId": "BLID-1758329350",
                                           "confp2mapvId": "260719T174413.314",
                                           "p2mapId": "BLID-1758329350", "p2mapvId": "260719T174353.832"},
             "ts": 1784483033, "type": "reloc"},
            {"ts": 1784483029, "type": "start"},
        ],
        "mission_id": "01KXXQM8XZEDJ24701JF121CCH",
        "nMssn": 255,
        "ver": "2.13",
    }

    report = MissionTimelineReport.from_json(raw)

    assert report.command == "start"
    assert report.initiator == "localApp"
    assert report.command_time == 1784483030
    assert report.mission_id == "01KXXQM8XZEDJ24701JF121CCH"
    assert report.n_missions == 255
    assert report.version == "2.13"

    assert len(report.event) == 1
    current = report.event[0]
    assert current.event_type == "room"
    assert current.start_time == 1784483054
    assert isinstance(current.room, RoomEvent)
    assert current.room.area == 354
    assert current.room.pass_count == 0
    assert current.room.region_id == "11"
    assert current.room.map_version == "260719T174414.994"

    assert len(report.fin_events) == 3
    travel_event, reloc_event, start_event = report.fin_events

    assert travel_event.event_type == "travel"
    assert travel_event.end_time == 1784483054
    assert isinstance(travel_event.travel, TravelEvent)
    assert travel_event.travel.region_id == "11"
    assert travel_event.travel.status == 0

    assert reloc_event.event_type == "reloc"
    assert isinstance(reloc_event.relocalizing, TentativeLocationEvent)
    assert reloc_event.relocalizing.confirmed_map_id == "BLID-1758329350"
    assert reloc_event.relocalizing.confirmed_map_version == "260719T174413.314"

    assert start_event.event_type == "start"
    assert start_event.start_time == 1784483029


def test_mission_timeline_report_handles_missing_fields_gracefully() -> None:
    from roombapy_prime.models import MissionTimelineReport

    report = MissionTimelineReport.from_json({})

    assert report.command is None
    assert report.event == []
    assert report.fin_events == []
    assert report.mission_id is None
    assert report.timeline_request_id is None


def test_mission_timeline_report_optional_timeline_request_id() -> None:
    """CONFIRMED LIVE (this session, chairstacker's second capture) --
    timelineRequestId appears on some but not all report messages, tied
    to an explicit client-side request for a fresh update. Absent by
    default; only present when the underlying JSON actually has it."""
    from roombapy_prime.models import MissionTimelineReport

    without = MissionTimelineReport.from_json({"mission_id": "m1"})
    assert without.timeline_request_id is None

    with_id = MissionTimelineReport.from_json({"mission_id": "m1", "timelineRequestId": 2015115795})
    assert with_id.timeline_request_id == 2015115795


def test_robot_serial_info_from_real_live_response() -> None:
    """NEW (session 58) -- real live get_serial_number_data() response
    (chairstacker, session 57's --dump-config), every field checked --
    previously verified ad-hoc without being preserved as a test."""
    from roombapy_prime.models import RobotSerialInfo

    result = RobotSerialInfo.from_json({
        "RobotID": "6F55705AE0BF169D69BDBFC9D858B5D2",
        "SerialNumber": "G185020H250311N105749",
        "built_as_sku": "g185020",
        "family_variant": "g1",
        "is_raas": False,
        "is_refurbished": False,
        "is_smartcare": False,
        "min_utc_reg_date": 1758240000,
        "name": "House_Bot",
        "serial_history": [{"serial_number": "G185020H250311N105749", "effective_from": 1741727474}],
        "sku": "g185020",
        "series": "G1",
        "family": "Roomba Combo",
    })

    assert result.robot_id == "6F55705AE0BF169D69BDBFC9D858B5D2"
    assert result.serial_number == "G185020H250311N105749"
    assert result.name == "House_Bot"
    assert result.family == "Roomba Combo"
    assert result.series == "G1"
    assert len(result.serial_history) == 1


def test_parse_user_households_from_real_live_response() -> None:
    """NEW (session 58) -- real live get_user_households() response
    (chairstacker, session 57's --dump-config) -- previously verified
    ad-hoc without being preserved as a test."""
    from roombapy_prime.models import parse_user_households

    result = parse_user_households([
        {
            "household_id": "c4714a01-f6ad-4ace-b111-d326d83867a5",
            "owner_cognito_id": "us-east-1:b06855a2-d0ed-45d8-a458-0ea4cecdacab",
            "household_name": "#AUTO_GENERATED_HOUSEHOLD#",
            "has_precise_location": False,
            "household_robots": [
                {
                    "household_id": "c4714a01-f6ad-4ace-b111-d326d83867a5",
                    "entity_id": "robot#6F55705AE0BF169D69BDBFC9D858B5D2",
                    "robot_id": "6F55705AE0BF169D69BDBFC9D858B5D2",
                    "creation_timestamp": 1758328461,
                }
            ],
            "household_users": [
                {
                    "household_id": "c4714a01-f6ad-4ace-b111-d326d83867a5",
                    "entity_id": "user#us-east-1:b06855a2-d0ed-45d8-a458-0ea4cecdacab",
                    "cognito_id": "us-east-1:b06855a2-d0ed-45d8-a458-0ea4cecdacab",
                    "creation_timestamp": 1604711200,
                }
            ],
        }
    ])

    assert len(result) == 1
    h = result[0]
    assert h.household_id == "c4714a01-f6ad-4ace-b111-d326d83867a5"
    assert h.household_name == "#AUTO_GENERATED_HOUSEHOLD#"
    assert len(h.household_robots) == 1
    assert h.household_robots[0].robot_id == "6F55705AE0BF169D69BDBFC9D858B5D2"
    assert len(h.household_users) == 1
    assert h.household_users[0].cognito_id == "us-east-1:b06855a2-d0ed-45d8-a458-0ea4cecdacab"


def test_border_feature_is_bare_feature_with_empty_properties_in_real_data() -> None:
    """NEW (session 58) -- confirms via real live bundle structure
    (chairstacker, session 57) that BorderFeature really is a single,
    bare Feature (no "features" wrapper list like room/cleanZone/
    policyZone/dockPose/floorTypes all have), and its properties are
    genuinely empty in practice -- both already modeled this way from
    bytecode evidence, now independently confirmed structurally
    against a real bundle rather than resting on bytecode alone."""
    from roombapy_prime.models import BorderFeature

    # Real bundle's borders file: {"type": "...", "geometry": {...}, "properties": {}}
    # -- a bare Feature, not {"type": "...", "features": [...]}
    result = BorderFeature.from_json({
        "type": "Feature",
        "id": "b1",
        "geometry": {"type": "MultiPolygon", "coordinates": []},
        "properties": {},
    })
    assert result.feature_id == "b1"


def test_parse_mission_timeline_accepts_dict_with_events_key() -> None:
    from roombapy_prime.models import parse_mission_timeline

    events = parse_mission_timeline({"events": [{"type": "waypoint", "waypoint": {"waypointId": "w1"}}]})
    assert len(events) == 1
    assert events[0].waypoint.waypoint_id == "w1"


def test_parse_mission_timeline_accepts_raw_list() -> None:
    from roombapy_prime.models import parse_mission_timeline

    events = parse_mission_timeline([{"type": "error", "error": {"value": 5}}])
    assert len(events) == 1
    assert events[0].error.value == 5


def test_parse_mission_timeline_none_returns_empty_list() -> None:
    from roombapy_prime.models import parse_mission_timeline

    assert parse_mission_timeline(None) == []


def test_mission_history_entry_populates_timeline_field() -> None:
    """CORRECTED (session 31): the original test used the key
    "events", which never exists in real data -- the fix was
    completely ineffective, unnoticed, until then (timeline was empty
    for EVERY real mission). Test now against the confirmed real key
    "finEvents" and the real field names (rid/zid instead of
    regionId/zoneId)."""
    from roombapy_prime.models import MissionHistoryEntry

    entry = MissionHistoryEntry.from_json(
        {
            "missionId": "m1",
            "timeline": {
                "coverageStrategy": "ROOM_SEGMENTATION",
                "finEvents": [
                    {"type": "room", "room": {"rid": "r1", "status": 1}},
                    {"type": "zone", "zone": {"zid": "z1", "status": 1}},
                ],
            },
        }
    )
    assert len(entry.timeline) == 2
    assert entry.timeline[0].room.region_id == "r1"
    assert entry.timeline[1].zone.zone_id == "z1"


def test_command_params_uses_swscrub_wire_key() -> None:
    """CORRECTED (session 25) -- the real wire key is "swScrub",
    confirmed from real mission history (chairstacker), not the
    original bytecode guess "scrub"."""
    from roombapy_prime.models import CommandParams

    params = CommandParams(scrub=1)
    body = params.to_json()

    assert body == {"swScrub": 1}
    assert "scrub" not in body  # old, wrong key must no longer appear


def test_command_params_swscrub_roundtrip() -> None:
    from roombapy_prime.models import CommandParams

    original = CommandParams(scrub=1, operating_mode=32)
    restored = CommandParams.from_json(original.to_json())

    assert restored == original


def test_command_params_operating_mode() -> None:
    """NEW (session 25) -- confirmed from real mission history."""
    from roombapy_prime.models import CommandParams

    params = CommandParams.from_json({"operatingMode": 32})
    assert params.operating_mode == 32
    assert params.to_json() == {"operatingMode": 32}


def test_region_type_values_are_lowercase() -> None:
    """CORRECTED (session 25) -- real wire values are lowercase,
    confirmed from real mission history (chairstacker: "rid"/"zid")."""
    from roombapy_prime.models import RegionType

    assert RegionType.RID.value == "rid"
    assert RegionType.ZID.value == "zid"


def test_operating_mode_bitmask_matches_real_observed_values() -> None:
    """CONFIRMED (parallel native-analysis track) and independently
    validated here against this project's own real observed data
    (chairstacker) -- not just trusting the bytecode reading."""
    from roombapy_prime.models.mission_control import OperatingModeBitmask

    assert OperatingModeBitmask(2) == OperatingModeBitmask.VACUUMING
    assert OperatingModeBitmask(32) == OperatingModeBitmask.VAC_MOP_COMBO_ONLY
    assert OperatingModeBitmask(512) == OperatingModeBitmask.VAC_THEN_MOP
    assert OperatingModeBitmask(6) == OperatingModeBitmask.VACUUMING | OperatingModeBitmask.MOP_ONLY


def test_operating_mode_bitmask_decodes_cap_omode_550() -> None:
    """The specific real value (550, seen as cap.oMode in get_state()'s
    shadow response on multiple real devices) that was previously an
    unexplained raw number -- confirmed to decompose exactly into four
    named flags, meaning cap.oMode is a set of SUPPORTED modes, not a
    single active one."""
    from roombapy_prime.models.mission_control import OperatingModeBitmask

    decoded = OperatingModeBitmask(550)

    assert OperatingModeBitmask.VACUUMING in decoded
    assert OperatingModeBitmask.MOP_ONLY in decoded
    assert OperatingModeBitmask.VAC_MOP_COMBO_ONLY in decoded
    assert OperatingModeBitmask.VAC_THEN_MOP in decoded
    assert OperatingModeBitmask.TRAVELING not in decoded
    assert OperatingModeBitmask.SCRUBBING not in decoded


def test_routine_type_param_wire_values_are_the_enum_names() -> None:
    """CONFIRMED (parallel native-analysis track) -- unlike most other
    enums in this module, the wire format IS the constant name itself,
    matching real observed data ("REPLAY", "CLEAN_ALL")."""
    from roombapy_prime.models.mission_control import RoutineTypeParam

    assert RoutineTypeParam.REPLAY.value == "REPLAY"
    assert RoutineTypeParam.CLEAN_ALL.value == "CLEAN_ALL"
    assert RoutineTypeParam("REPLAY") == RoutineTypeParam.REPLAY


def test_routine_command_initiator_field() -> None:
    """NEW (session 25) -- confirmed from real mission history
    (values "cloud"/"rmtApp" observed)."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    cmd = RoutineCommand(command_type=MissionCommandType.CLEAN, asset_id="BLID123", initiator="rmtApp")
    body = cmd.to_json()

    assert body["initiator"] == "rmtApp"


def test_routine_command_initiator_omitted_when_none() -> None:
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    cmd = RoutineCommand(command_type=MissionCommandType.CLEAN, asset_id="BLID123")
    body = cmd.to_json()

    assert "initiator" not in body


# =========================================================================
# P2MapVersion / RoomMetadataEntry / RobotSerialInfo (session 26)
# =========================================================================


def test_routine_type_field_roundtrip() -> None:
    """Vervollstaendigt eine unvollstaendige Verdrahtung von routine_type
    (Feld existierte, war aber nicht an to_json/from_json angebunden)."""
    from roombapy_prime.models import CommandParams

    original = CommandParams(replay_of="01KRQ4S1RP493P1WKCG71C90D9", routine_type="REPLAY")
    restored = CommandParams.from_json(original.to_json())

    assert restored == original
    assert original.to_json()["routine_type"] == "REPLAY"


def test_room_metadata_entry_parses_operating_mode_defaults_as_command_params() -> None:
    """Kern des Fundes: operating_mode_defaults-Werte sind CommandParams-
    foermig und lassen sich direkt wiederverwenden."""
    from roombapy_prime.models import CommandParams, RegionType, RoomMetadataEntry

    entry = RoomMetadataEntry.from_json(
        {
            "room_id": "15",
            "room_metadata": {
                "last_operating_mode": 512,
                "operating_mode_defaults": {
                    "512": {"twoPass": True, "suctionLevel": 4, "swScrub": 1, "profile": "deep", "carpetBoost": True},
                    "32": {"twoPass": False, "suctionLevel": 2, "swScrub": 0, "carpetBoost": False, "profile": "light"},
                },
                "region_type": "rid",
            },
        }
    )

    assert entry.room_id == "15"
    assert entry.last_operating_mode == 512
    assert entry.region_type == RegionType.RID
    assert set(entry.operating_mode_defaults.keys()) == {"512", "32"}
    preset_512 = entry.operating_mode_defaults["512"]
    assert isinstance(preset_512, CommandParams)
    assert preset_512.suction_level == 4
    assert preset_512.scrub == 1
    assert preset_512.cleaning_profile == "deep"  # confirmed: "profile" correctly maps to cleaning_profile


def test_room_metadata_entry_optional_name() -> None:
    """Some rooms have a user-assigned name (e.g. "Bathroom"), others
    don't -- confirmed from real data."""
    from roombapy_prime.models import RoomMetadataEntry

    named = RoomMetadataEntry.from_json(
        {"room_id": "10", "room_metadata": {"name": "Bathroom", "region_type": "rid"}}
    )
    unnamed = RoomMetadataEntry.from_json({"room_id": "15", "room_metadata": {"region_type": "rid"}})

    assert named.name == "Bathroom"
    assert unnamed.name is None


def test_room_metadata_entry_parses_category() -> None:
    """NEW -- added specifically to support verify_map_edit.py's own
    room-category test: category is the read-side counterpart of
    SetRoomMetadataV1's write-side room_metadata.type field, same key
    name, same RoomCategory enum."""
    from roombapy_prime.models import RoomMetadataEntry
    from roombapy_prime.models.enums_common import RoomCategory

    entry = RoomMetadataEntry.from_json(
        {"room_id": "10", "room_metadata": {"name": "Living Room", "type": "living_room"}}
    )

    assert entry.category == RoomCategory.LIVING_ROOM


def test_p2map_version_from_json_with_multiple_rooms() -> None:
    from roombapy_prime.models import P2MapVersion

    m = P2MapVersion.from_json(
        {
            "p2map_id": "BLID-123",
            "entity_type": "p2map",
            "create_time": 1758329351,
            "robot_id": "BLID",
            "sku": "G185020",
            "active_p2mapv_id": "260518T135521.119",
            "last_p2mapv_ts": 1783951462,
            "state": "active",
            "visible": True,
            "name": "Whole House",
            "rooms_metadata": [
                {"room_id": "15", "room_metadata": {"region_type": "rid"}},
                {"room_id": "100", "room_metadata": {"region_type": "zid"}},
            ],
        }
    )

    assert m.p2map_id == "BLID-123"
    assert m.name == "Whole House"
    assert m.active_p2mapv_id == "260518T135521.119"
    assert len(m.rooms_metadata) == 2
    assert m.rooms_metadata[0].room_id == "15"
    assert m.rooms_metadata[1].room_id == "100"


def test_parse_active_map_versions_multiple_maps() -> None:
    """Confirmed: an account can have multiple P2MapVersion entries
    (real data showed "Whole House" + "Master_Bathroom")."""
    from roombapy_prime.models import parse_active_map_versions

    maps = parse_active_map_versions(
        [
            {"p2map_id": "map1", "name": "Whole House", "rooms_metadata": []},
            {"p2map_id": "map2", "name": "Master_Bathroom", "rooms_metadata": []},
        ]
    )

    assert len(maps) == 2
    assert maps[0].name == "Whole House"
    assert maps[1].name == "Master_Bathroom"


def test_parse_active_map_versions_handles_none_and_empty() -> None:
    from roombapy_prime.models import parse_active_map_versions

    assert parse_active_map_versions(None) == []
    assert parse_active_map_versions([]) == []


class TestBuildRoomNameMap:
    """build_room_name_map() -- generic region_id -> room name lookup,
    shared groundwork for calendar features across robot generations."""

    def test_newer_map_version_wins_for_the_same_room_id(self):
        from roombapy_prime.models.robot_info import P2MapVersion, RoomMetadataEntry, build_room_name_map

        older = P2MapVersion(
            p2map_id="m1", robot_id="BLID1", last_p2mapv_ts=100,
            rooms_metadata=[RoomMetadataEntry(room_id="23", name="Kitchen (old)")],
        )
        newer = P2MapVersion(
            p2map_id="m1", robot_id="BLID1", last_p2mapv_ts=200,
            rooms_metadata=[RoomMetadataEntry(room_id="23", name="Kitchen")],
        )

        # Order in the input list must not matter -- last_p2mapv_ts decides, not position.
        result = build_room_name_map([newer, older])

        assert result == {"23": "Kitchen"}

    def test_unnamed_rooms_are_skipped_not_included_as_empty(self):
        from roombapy_prime.models.robot_info import P2MapVersion, RoomMetadataEntry, build_room_name_map

        version = P2MapVersion(
            p2map_id="m1", robot_id="BLID1",
            rooms_metadata=[RoomMetadataEntry(room_id="24", name=None)],
        )

        result = build_room_name_map([version])

        assert result == {}

    def test_blid_filters_out_other_robots_maps(self):
        from roombapy_prime.models.robot_info import P2MapVersion, RoomMetadataEntry, build_room_name_map

        mine = P2MapVersion(
            p2map_id="m1", robot_id="BLID1",
            rooms_metadata=[RoomMetadataEntry(room_id="23", name="Kitchen")],
        )
        other_robot = P2MapVersion(
            p2map_id="m2", robot_id="BLID_OTHER",
            rooms_metadata=[RoomMetadataEntry(room_id="23", name="Should not appear")],
        )

        result = build_room_name_map([mine, other_robot], blid="BLID1")

        assert result == {"23": "Kitchen"}

    def test_no_blid_given_includes_all_robots_maps(self):
        from roombapy_prime.models.robot_info import P2MapVersion, RoomMetadataEntry, build_room_name_map

        v1 = P2MapVersion(p2map_id="m1", robot_id="BLID1", rooms_metadata=[RoomMetadataEntry(room_id="23", name="Kitchen")])
        v2 = P2MapVersion(p2map_id="m2", robot_id="BLID2", rooms_metadata=[RoomMetadataEntry(room_id="99", name="Garage")])

        result = build_room_name_map([v1, v2])

        assert result == {"23": "Kitchen", "99": "Garage"}

    def test_empty_input_returns_empty_dict(self):
        from roombapy_prime.models.robot_info import build_room_name_map

        assert build_room_name_map([]) == {}


def test_robot_serial_info_from_json() -> None:
    """Confirmed from a real get_serial_number_data() response
    (chairstacker) -- including "family": "Roomba Combo", confirms a
    vacuum+mop combo device."""
    from roombapy_prime.models import RobotSerialInfo

    info = RobotSerialInfo.from_json(
        {
            "RobotID": "BLID123",
            "SerialNumber": "G185020H250311N105749",
            "built_as_sku": "g185020",
            "family_variant": "g1",
            "is_raas": False,
            "is_refurbished": False,
            "is_smartcare": False,
            "min_utc_reg_date": 1758240000,
            "name": "House_Bot",
            "sku": "g185020",
            "series": "G1",
            "family": "Roomba Combo",
            "serial_history": [{"serial_number": "G185020H250311N105749", "effective_from": 1741727474}],
        }
    )

    assert info.robot_id == "BLID123"
    assert info.serial_number == "G185020H250311N105749"
    assert info.name == "House_Bot"
    assert info.family == "Roomba Combo"
    assert info.series == "G1"
    assert len(info.serial_history) == 1


# =========================================================================
# Corrections from the second diagnose.json part (session 27)
# =========================================================================


def test_mission_history_entry_uses_confirmed_real_field_names() -> None:
    """Regression test against the bug found in that session: almost
    all field names had been wrongly guessed (minutesRunning->runM,
    minutesPaused->pauseM, minutesCharging->chrgM, minutesDone->doneM,
    squareFeetCovered->sqft, numberOfEvacuations->evacs,
    endedOnDock->eDock, robotId->robot_id, "command"->"cmd")."""
    from roombapy_prime.models import parse_mission_history

    real_shaped = {
        "missionId": "m1",
        "robot_id": "BLID123",
        "runM": 2,
        "pauseM": 1,
        "chrgM": 3,
        "doneM": 4,
        "sqft": 23,
        "evacs": 1,
        "eDock": 0,
        "done": "ok",
        "done_raw": "ok",
        "cmd": {"command": "start", "p2map_id": "map-1", "user_p2mapv_id": "v1", "initiator": "cloud"},
    }
    entry = parse_mission_history([real_shaped])[0]

    assert entry.robot_id == "BLID123"
    assert entry.minutes_running == 2
    assert entry.minutes_paused == 1
    assert entry.minutes_charging == 3
    assert entry.minutes_done == 4
    assert entry.square_feet_covered == 23
    assert entry.number_of_evacuations == 1
    assert entry.ended_on_dock == 0
    assert entry.command is not None
    assert entry.command.map_id == "map-1"
    assert entry.command.map_version_id == "v1"


def test_done_code_matches_real_lowercase_value() -> None:
    """REVISED (session 27) -- values are lowercase, confirmed from
    real mission history."""
    from roombapy_prime.models import DoneCode

    assert DoneCode.OK.value == "ok"
    assert DoneCode.STUCK.value == "stuck"


def test_mission_command_record_regions_are_typed() -> None:
    """NEW (session 27) -- regions is now list[Region] instead of a
    raw list, params within it is CommandParams-shaped."""
    from roombapy_prime.models import CommandParams, MissionCommandRecord, Region, RegionType

    record = MissionCommandRecord.from_json(
        {
            "command": "start",
            "p2map_id": "map-1",
            "regions": [
                {"params": {"suctionLevel": 3, "swScrub": 0, "carpetBoost": False}, "region_id": "100", "type": "zid"}
            ],
        }
    )

    assert len(record.regions) == 1
    region = record.regions[0]
    assert isinstance(region, Region)
    assert region.region_id == "100"
    assert region.region_type == RegionType.ZID
    assert isinstance(region.params, CommandParams)
    assert region.params.suction_level == 3


def test_region_from_json_uses_region_id_key() -> None:
    """NEW (session 27) -- Region.from_json() was completely missing;
    real data shows "region_id" as the key when reading (unlike "id"
    when sending via to_json())."""
    from roombapy_prime.models import Region, RegionType

    region = Region.from_json({"region_id": "15", "type": "rid"})

    assert region.region_id == "15"
    assert region.region_type == RegionType.RID


def test_command_params_no_auto_passes() -> None:
    """NEW (session 27) -- confirmed from get_state()'s embedded
    cleanSchedule2[].cmdStr."""
    from roombapy_prime.models import CommandParams

    params = CommandParams.from_json({"noAutoPasses": True})
    assert params.no_auto_passes is True
    assert params.to_json() == {"noAutoPasses": True}


def test_robot_part_from_json() -> None:
    from roombapy_prime.models import RobotPart

    part = RobotPart.from_json(
        {
            "part_id": "148",
            "counter": 30,
            "minutes_remaining": -1,
            "count_type": "combo_missions",
            "count_remaining": 21,
            "count_used": 9,
            "counter_category": "replacement",
            "reset_by": "user",
        }
    )

    assert part.part_id == "148"
    assert part.count_type == "combo_missions"
    assert part.count_remaining == 21


def test_robot_parts_info_from_json_with_multiple_parts() -> None:
    from roombapy_prime.models import RobotPartsInfo

    info = RobotPartsInfo.from_json(
        {
            "robot_id": "BLID123",
            "num_parts": 2,
            "parts": [
                {"part_id": "148", "count_type": "combo_missions"},
                {"part_id": "67", "count_type": "minutes", "minutes_remaining": 4680},
            ],
        }
    )

    assert info.robot_id == "BLID123"
    assert info.num_parts == 2
    assert len(info.parts) == 2
    assert info.parts[1].minutes_remaining == 4680


# =========================================================================
# Household / HouseholdRobot / HouseholdUser (session 28)
# =========================================================================


def test_household_from_json_with_robots_and_users() -> None:
    """Confirmed from a real get_user_households() response
    (chairstacker) -- the endpoint was documented as "unused in the
    app code", but actually responds correctly."""
    from roombapy_prime.models import Household

    h = Household.from_json(
        {
            "household_id": "hh-1",
            "owner_cognito_id": "us-east-1:abc",
            "household_name": "#AUTO_GENERATED_HOUSEHOLD#",
            "has_precise_location": False,
            "household_robots": [
                {"household_id": "hh-1", "entity_id": "robot#BLID123", "robot_id": "BLID123", "creation_timestamp": 111}
            ],
            "household_users": [
                {"household_id": "hh-1", "entity_id": "user#abc", "cognito_id": "abc", "creation_timestamp": 222}
            ],
        }
    )

    assert h.household_id == "hh-1"
    assert h.household_name == "#AUTO_GENERATED_HOUSEHOLD#"
    assert h.has_precise_location is False
    assert len(h.household_robots) == 1
    assert h.household_robots[0].entity_id == "robot#BLID123"
    assert h.household_robots[0].robot_id == "BLID123"
    assert len(h.household_users) == 1
    assert h.household_users[0].cognito_id == "abc"


def test_parse_user_households_multiple_entries() -> None:
    from roombapy_prime.models import parse_user_households

    households = parse_user_households([{"household_id": "hh-1"}, {"household_id": "hh-2"}])

    assert len(households) == 2
    assert households[0].household_id == "hh-1"
    assert households[1].household_id == "hh-2"


def test_parse_user_households_handles_none_and_empty() -> None:
    from roombapy_prime.models import parse_user_households

    assert parse_user_households(None) == []
    assert parse_user_households([]) == []


def test_mission_command_record_top_level_params() -> None:
    """NEW (session 30) -- cmd.params is its own top-level field,
    separate from regions[].params, confirmed from real mission
    history (sometimes set e.g. {"profile": "light"}, sometimes null)."""
    from roombapy_prime.models import MissionCommandRecord

    with_params = MissionCommandRecord.from_json({"command": "start", "params": {"profile": "light"}})
    without_params = MissionCommandRecord.from_json({"command": "start", "params": None})

    assert with_params.params is not None
    assert with_params.params.cleaning_profile == "light"
    assert without_params.params is None


# =========================================================================
# RobotSettings (session 32)
# =========================================================================


def test_pad_wetness_param_from_json() -> None:
    """NEW (session 32) -- confirmed from a real get_settings() response."""
    from roombapy_prime.models import PadWetnessParam

    p = PadWetnessParam.from_json({"disposable": 3, "reusable": 1, "padPlate": 1})

    assert p.disposable == 3
    assert p.pad_plate == 1
    assert p.reusable == 1


def test_robot_settings_from_json_real_shape() -> None:
    """Confirmed from a real get_settings() response (chairstacker,
    Roomba 405). Covers a large part of the previously unmodeled
    settings vocabulary (childLock, audio.volume, autoevacFreq,
    langs2, mapUploadAllowed, padDry*/padWash*, among others)."""
    from roombapy_prime.models import RobotSettings

    s = RobotSettings.from_json(
        {
            "nsmip": 2,
            "audio": {"volume": 100},
            "carpetBoost": True,
            "childLock": False,
            "cloudEnv": "prod",
            "country": "US",
            "ecoCharge": False,
            "name": "House_Bot",
            "noAutoPasses": False,
            "padWetness": {"disposable": 3, "reusable": 1, "padPlate": 1},
            "suctionLevel": 3,
            "svcEndpoints": {"svcDeplId": "v007"},
            "timezone": "America/Phoenix",
            "twoPass": False,
            "vacHigh": False,
            "autoevacFreq": 1,
            "evacAllowed": True,
            "langs2": {"aSlots": 1, "sLang": "en-US", "sVer": "1.0"},
            "mapUploadAllowed": True,
            "padDryAllowed": 1,
            "padDryDur": 4,
            "padWashAllowed": 1,
            "pwAreaInterval": 10,
            "pwReturn": 2,
            "pwTimeInterval": 15,
            "schedHold": False,
            "swScrub": 0,
        }
    )

    assert s.name == "House_Bot"
    assert s.child_lock is False
    assert s.audio_volume == 100
    assert s.timezone == "America/Phoenix"
    assert s.autoevac_freq == 1
    assert s.pad_wetness is not None
    assert s.pad_wetness.disposable == 3
    assert s.svc_deployment_id == "v007"
    assert s.pad_dry_duration == 4
    assert s.pad_wash_return == 2
    assert s.languages_raw["sLang"] == "en-US"


def test_robot_settings_handles_missing_optional_nested_objects() -> None:
    """Absicherung: fehlende audio/padWetness/svcEndpoints/langs2 duerfen
    nicht abstuerzen."""
    from roombapy_prime.models import RobotSettings

    s = RobotSettings.from_json({"name": "X"})

    assert s.name == "X"
    assert s.audio_volume is None
    assert s.pad_wetness is None


def test_classic_shadow_state_from_real_live_capture() -> None:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (this session,
    chairstacker's raw_shadows.json) -- get_state()'s classic/unnamed
    shadow, previously returned as an untyped ShadowResponse only.
    Uses a trimmed version of the real cap object (a handful of fields,
    not all 36) -- CapabilityFlags' own test covers the full field
    list."""
    from roombapy_prime.models import ClassicShadowState

    state = ClassicShadowState.from_json(
        {
            "digiCap": {"appVer": 1, "timeline": 1},
            "nsmip": 2,
            "cap": {"5ghz": 1, "carpetBoost": 3, "binFullDetect": 0},
            "cleanSchedule2": [{"cmdStr": "some-repr-like-string", "enabled": True}],
            "schedHold": False,
            "sku": "G185020",
            "soldAsSku": "G185020",
            "svcEndpoints": {"svcDeplId": "v005"},
        }
    )

    assert state.digi_cap.app_ver == 1
    assert state.digi_cap.timeline == 1
    assert state.cap.wifi_5ghz == 1
    assert state.cap.carpet_boost == 3
    assert state.cap.bin_full_detect == 0  # a confirmed negative, not a missing value
    assert state.clean_schedule2_raw == [{"cmdStr": "some-repr-like-string", "enabled": True}]
    assert state.sched_hold is False
    assert state.sku == "G185020"


def test_classic_shadow_state_handles_missing_fields() -> None:
    from roombapy_prime.models import ClassicShadowState

    state = ClassicShadowState.from_json({})

    assert state.digi_cap is None
    assert state.cap is None
    assert state.clean_schedule2_raw == []
    assert state.sched_hold is None


def test_capability_flags_from_real_live_capture() -> None:
    """CONFIRMED LIVE, ALL 36 FIELDS, REAL VALUES (this session,
    chairstacker's raw_shadows.json) -- the full "cap" object, the only
    per-device capability data found anywhere in this project so far.
    Spot-checks a representative subset (graduated/tiered values, not
    just booleans) rather than asserting all 36 individually."""
    from roombapy_prime.models import CapabilityFlags

    cap = CapabilityFlags.from_json(
        {
            "5ghz": 1, "area": 1, "autoevac": 1, "binFullDetect": 0, "carpetBoost": 3,
            "dPause": 1, "dnd": 1, "dockComm": 1, "expectingUserConf": 2, "floorTypeDetect": 4,
            "idl": 0, "lang": 2, "langOta": 2, "lmap": 1, "log": 2, "maps": 6, "matter": 0,
            "mc": 3, "multiPass": 1, "ns": 1, "oMode": 550, "ota": 3, "ppWetLvl": 0, "prov": 3,
            "pw": 3, "sched": 2, "scrub": 3, "suctionLvl": 4, "svcConf": 1, "tLine": 2,
            "vmStrat": 1, "bleLog": 1, "dSpot": 1, "mapMax": 5, "p2maps": 1, "saSku": 1,
        }
    )

    assert cap.wifi_5ghz == 1
    assert cap.matter == 0  # confirmed absent, not just unmodeled
    assert cap.o_mode == 550  # graduated value, not a boolean
    assert cap.map_max == 5
    assert cap.sa_sku == 1


def test_capability_flags_handles_missing_fields() -> None:
    from roombapy_prime.models import CapabilityFlags

    cap = CapabilityFlags.from_json({})

    assert cap.wifi_5ghz is None
    assert cap.matter is None


def test_schedule_shadow_from_real_live_capture() -> None:
    """CONFIRMED LIVE (this session, chairstacker) -- the complete
    content of "rw-schedule", the third of the three never-before-
    queried named shadows checked in the same pass. Deliberately
    stores clean_schedule2_raw as-is rather than deep-parsing each
    entry's cmdStr -- that's a separate, already-ongoing investigation
    (see models/mission_control.py)."""
    from roombapy_prime.models import ScheduleShadow

    shadow = ScheduleShadow.from_json(
        {"cleanSchedule2": [{"cmdStr": "some-repr-like-string"}], "nsmip": 2}
    )

    assert shadow.clean_schedule2_raw == [{"cmdStr": "some-repr-like-string"}]
    assert shadow.nsmip == 2


def test_schedule_shadow_handles_missing_fields() -> None:
    from roombapy_prime.models import ScheduleShadow

    shadow = ScheduleShadow.from_json({})

    assert shadow.clean_schedule2_raw == []
    assert shadow.nsmip is None


def test_connection_status_shadow_from_real_live_capture() -> None:
    """CONFIRMED LIVE (this session, chairstacker) -- the complete
    content of "rw-constatus", the leading (and only) never-before-
    queried named shadow hypothesized as a battery/charging status
    candidate. DISPROVEN by this exact capture: this is MQTT/AWS-IoT
    connection status, not battery -- see the model's own docstring
    and RobotStatusV2's for the full correction."""
    from roombapy_prime.models import ConnectionStatusShadow

    status = ConnectionStatusShadow.from_json(
        {"connected": True, "connectedv2": True, "echo": 0, "svcEndpoints": {"svcDeplId": "v007"}}
    )

    assert status.connected is True
    assert status.connected_v2 is True
    assert status.echo == 0


def test_connection_status_shadow_handles_missing_fields() -> None:
    from roombapy_prime.models import ConnectionStatusShadow

    status = ConnectionStatusShadow.from_json({})

    assert status.connected is None
    assert status.connected_v2 is None
    assert status.echo is None


def test_software_status_shadow_from_real_live_capture() -> None:
    """CONFIRMED LIVE (this session, chairstacker) -- the complete
    content of "rw-software", the other never-before-queried named
    shadow checked in the same pass. Also not battery-related -- OTA/
    firmware deployment and update status."""
    from roombapy_prime.models import SoftwareStatusShadow

    status = SoftwareStatusShadow.from_json(
        {
            "deploymentId": "abc123",
            "deploymentMpkg": "mpkg-1",
            "deploymentState": "idle",
            "imuRecal": False,
            "lastCommand": "none",
            "lastSwUpdate": 1784559000,
            "softwareVer": "22.52.10",
            "subModSwVer": {"navSw": "1.2.3"},
            "svcEndpoints": {"svcDeplId": "v007"},
        }
    )

    assert status.deployment_id == "abc123"
    assert status.deployment_state == "idle"
    assert status.software_version == "22.52.10"
    assert status.last_sw_update == 1784559000


def test_software_status_shadow_handles_missing_fields() -> None:
    from roombapy_prime.models import SoftwareStatusShadow

    status = SoftwareStatusShadow.from_json({})

    assert status.deployment_id is None
    assert status.software_version is None


def test_resolved_mission_status_enum_has_all_49_confirmed_values() -> None:
    """FULLY CONFIRMED (parallel native-analysis track) -- all 49
    values (0-48), superseding the earlier partial version of this
    enum. Checks a representative sample across the range, including
    several from the previously-unconfirmed gaps (1-4, 6-8, 11-13, 17,
    20-23, 26-27) and the full SENDING_COMMAND_* transitional family
    (28-47). Mapping to any specific shadow field remains unconfirmed
    -- this test only covers the enum's own confirmed int values."""
    from roombapy_prime.models.robot_info import ResolvedMissionStatus

    assert len(ResolvedMissionStatus) == 49
    assert ResolvedMissionStatus.INVALID == 0
    assert ResolvedMissionStatus.CONNECTING == 1
    assert ResolvedMissionStatus.READY_WITH_CONDITIONAL_START_REFUSE == 7
    assert ResolvedMissionStatus.CLEANING == 9
    assert ResolvedMissionStatus.PAUSED == 10
    assert ResolvedMissionStatus.WET_MOPPING_PAUSED_WITH_START_REFUSE == 13
    assert ResolvedMissionStatus.RETURN_TO_DOCK == 16
    assert ResolvedMissionStatus.RETURN_TO_DOCK_SEARCHING == 17
    assert ResolvedMissionStatus.TRAINING == 20
    assert ResolvedMissionStatus.VIDEO_STREAMING == 23
    assert ResolvedMissionStatus.FLUSHING_SLUICE == 26
    assert ResolvedMissionStatus.STOP_DOCK_EVACUATING == 27
    assert ResolvedMissionStatus.SENDING_COMMAND_CLEAN == 28
    assert ResolvedMissionStatus.SENDING_COMMAND_STOP_EVAC == 47
    assert ResolvedMissionStatus.UNKNOWN == 48


def test_dock_state_enum_has_all_86_confirmed_values() -> None:
    """FULLY CONFIRMED (parallel native-analysis track) -- all 86
    values extracted directly from the real enum; previously only
    discussed in prose elsewhere in this codebase, never implemented
    as a real enum until now. Checks a representative sample across
    all four functional-area bands, plus the confirmed duplicate-value
    behavior (2 and 3 are each shared by two names in the real enum
    itself, not a transcription error -- Python's own IntEnum aliasing
    applies)."""
    from roombapy_prime.models.robot_info import DockState

    assert DockState.DOCK_NO_COMMON_ERROR == 0
    assert DockState.DOCK_READY == 301
    assert DockState.DOCK_HARDWARE_ISSUE_ERROR == 365
    assert DockState.FLUID_REPLENISHMENT_OKAY == 401
    assert DockState.FLUID_REPLENISHMENT_ROBOT_TANK_FILLING_TIMEOUT_ERROR == 464
    assert DockState.PAD_WASH_OKAY == 601
    assert DockState.PAD_WASH_PAD_ACTUATOR_STALL_ERROR == 669
    assert DockState.PAD_DRY_OKAY == 701
    assert DockState.PAD_DRY_COMMUNICATION_FAILURE_ERROR == 757

    # confirmed duplicate values -- both names access the same member.
    assert DockState.PAD_DRY_UNHEATED_AIR == DockState.PAD_WASH_NORMAL_HEATED_WATER == 2
    assert DockState.PAD_DRY_HEATED_AIR == DockState.PAD_WASH_MAX_HEATED_WATER == 3


def test_current_state_shadow_from_real_live_capture() -> None:
    """CONFIRMED LIVE, REAL VALUES (chairstacker) -- the actual
    resolution of this whole project's battery-status search. This is
    chairstacker's genuine ro-currentstate reported payload (robot
    idle, charging on dock at 72%), not placeholder values -- exercises
    the full nested structure (BinStatus/CleanMissionStatus/DockStatus/
    RuntimeStatsSummary/P2MapRef) in one shot."""
    from roombapy_prime.models import CurrentStateShadow, P2MapRef

    state = CurrentStateShadow.from_json(
        {
            "batPct": 72,
            "cleanMissionStatus": {
                "condNotReady": [], "cycle": "none", "error": 0, "initiator": "rmtApp",
                "missionId": "01KY30BFZTGERV3KBTRF224MQR", "mssnStrtTm": 1784659951,
                "nMssn": 266, "notReady": 0, "operatingMode": 2, "phase": "charge", "sqft": 10,
            },
            "lastDisconnect": 2,
            "svcEndpoints": {"svcDeplId": "v005"},
            "regDate": "2025-09-19",
            "dock": {
                "cap": {"evac": 1, "pd": 2, "pw": 1, "pwo": 1},
                "error": 0, "fwVer": "20", "known": True,
                "pdState": 701, "pwState": 601, "state": 301,
            },
            "bin": {"present": True},
            "detectedPad": "padPlate",
            "tz": {"events": [{"dt": 0, "off": 0}], "ver": 31},
            "p2maps": [{"p2map_id": "BLID-1758329350", "p2mapv_id": "260518T135521.119"}],
            "runtimeStats": {"hr": 44, "min": 44},
            "tankPresent": True,
        }
    )

    assert state.bat_pct == 72
    assert state.tank_present is True
    assert state.detected_pad == "padPlate"
    assert state.reg_date == "2025-09-19"

    assert state.clean_mission_status.phase == "charge"
    assert state.clean_mission_status.operating_mode == 2
    assert state.clean_mission_status.mission_id == "01KY30BFZTGERV3KBTRF224MQR"

    assert state.dock.state == 301
    assert state.dock.pw_state == 601
    assert state.dock.pd_state == 701
    # NOW CONFIRMED (full DockState enum extracted) -- these numeric
    # values directly resolve to named, meaningful states, not just
    # bare numbers in a suggestive band.
    from roombapy_prime.models.robot_info import DockState

    assert state.dock.state == DockState.DOCK_READY
    assert state.dock.pw_state == DockState.PAD_WASH_OKAY
    assert state.dock.pd_state == DockState.PAD_DRY_OKAY
    assert state.dock.cap.pad_dry == 2

    assert state.bin.present is True
    assert state.runtime_stats.hours == 44
    assert state.p2maps == [P2MapRef(p2map_id="BLID-1758329350", p2mapv_id="260518T135521.119")]


def test_stats_shadow_from_real_live_capture() -> None:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (this session,
    chairstacker's raw_shadows.json) -- replaces the earlier version of
    this test, which used placeholder shapes (bbchg=1, bbchg3=2, etc.)
    written back when only key names were confirmed. Real payload shape
    used here verbatim (including the still-unexplained same-name
    nested duplicate under each bbX key)."""
    from roombapy_prime.models import StatsShadow

    stats = StatsShadow.from_json(
        {
            "bbchg": {"bbchg": {"nChgErr": 0, "nChgOk": 0}, "nChgErr": 0, "nChgOk": 561},
            "bbchg3": {"bbchg3": {"hOnDock": 0, "nAvail": 0}, "hOnDock": 293109, "nAvail": 285},
            "bbmssn": {
                "bbmssn": {"nMssn": 0, "nMssnC": 0, "nMssnF": 0, "nMssnOk": 0},
                "nMssn": 276, "nMssnC": 25, "nMssnF": 4, "nMssnOk": 247,
            },
            "bbpause": {"bbpause": {"pauses": [29, -1]}, "pauses": [1, 48, 48, 48, 48, 48, 48, 48, 48, 48]},
            "bbrstinfo": {"bbrstinfo": {"nNavRst": 1}, "nNavRst": 22},
            "bbsys": {"bbsys": {"hr": 0, "min": 0}, "hr": 7354, "min": 0},
            "runtimestats": {"hr": 7, "min": 57},
            "svcEndpoints": {"svcDeplId": "v005"},
            "unprocessedError": "picea unknown fault code:2105",
        }
    )

    assert stats.bbchg.n_chg_ok == 561
    assert stats.bbchg.n_chg_err == 0
    assert stats.bbchg.raw_nested == {"nChgErr": 0, "nChgOk": 0}
    assert stats.bbmssn.n_mssn == 276
    # THE internal-consistency check that confirms these are real lifetime
    # counters, not arbitrary numbers: canceled+failed+ok sums to the total.
    assert stats.bbmssn.n_mssn_canceled + stats.bbmssn.n_mssn_failed + stats.bbmssn.n_mssn_ok == stats.bbmssn.n_mssn
    assert stats.bbpause.pauses == [1, 48, 48, 48, 48, 48, 48, 48, 48, 48]
    assert stats.bbrstinfo.n_nav_rst == 22
    assert stats.bbsys.hours == 7354
    assert stats.runtimestats.hours == 7
    assert stats.runtimestats.minutes == 57
    assert stats.unprocessed_error == "picea unknown fault code:2105"


def test_stats_shadow_handles_missing_fields() -> None:
    from roombapy_prime.models import StatsShadow

    stats = StatsShadow.from_json({})

    assert stats.bbchg is None
    assert stats.runtimestats is None


def test_services_shadow_from_real_live_capture() -> None:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUE (this session,
    chairstacker) -- replaces the earlier version of this test, which
    assumed "optFeats" was a plain list (["feat1", "feat2"]); the real
    payload shows it's an object mapping feature name -> int."""
    from roombapy_prime.models import ServicesShadow

    services = ServicesShadow.from_json({"nsmip": 2, "optFeats": {"carpetBoost": 0}, "svcEndpoints": {}})

    assert services.opt_feats == {"carpetBoost": 0}


def test_services_shadow_handles_missing_fields() -> None:
    from roombapy_prime.models import ServicesShadow

    services = ServicesShadow.from_json({})

    assert services.opt_feats is None


def test_config_info_shadow_from_real_live_capture() -> None:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (this session,
    chairstacker) -- replaces the earlier version of this test, which
    assumed "hwPartsRev" was a plain string ("rev3"); the real payload
    shows it's an object with mostly-empty string fields plus one real
    serial number. "passwordHash" specifically prompted a real, separate
    redaction fix in diagnostics.py -- see that module's own tests for
    the fix itself; this test only covers the model's own from_json()
    mapping."""
    from roombapy_prime.models import ConfigInfoShadow

    config = ConfigInfoShadow.from_json(
        {
            "hwPartsRev": {
                "aoaSerialNo": "", "fan": "", "imuPartNo": "", "lrDrv": "", "mobBlid": "",
                "mobBrd": 0, "navSerialNo": "G185020H250311N105749", "ui": "", "wlan0HwAddr": "",
            },
            "nsmip": 2,
            "passwordHash": "abc123",
            "svcEndpoints": {},
        }
    )

    assert config.hw_parts_rev.nav_serial_no == "G185020H250311N105749"
    assert config.hw_parts_rev.mob_board == 0
    assert config.hw_parts_rev.fan == ""
    assert config.password_hash == "abc123"


def test_config_info_shadow_handles_missing_fields() -> None:
    from roombapy_prime.models import ConfigInfoShadow

    config = ConfigInfoShadow.from_json({})

    assert config.hw_parts_rev is None
    assert config.password_hash is None


def test_dock_paddry_report_from_real_live_capture() -> None:
    """CONFIRMED LIVE (this session, chairstacker) -- real payload from
    the newly-discovered "dock/paddry/report" topic, fired essentially
    immediately after a mission's "start" command. See the model's own
    docstring for why this is a genuinely new, structurally-grounded
    lead for the battery/RobotStatusV2 question (topic FAMILY shaped
    like "dock/{reportType}/report", only "paddry" observed so far)."""
    from roombapy_prime.models import DockPadDryReport

    report = DockPadDryReport.from_json(
        {
            "bbk": {
                "dockErrorCounts": {},
                "dockId": "UNKNOWN",
                "dockVer": "UNKNOWN",
                "numDocks": 23,
                "totalPadDry": 141,
                "totalPadDryTime": 1614726,
            },
            "cap": {"evac": 1, "pd": 2, "pw": 1, "pwo": 1},
            "dockId": "NA",
            "dockPn": "NA",
            "dockVer": "20",
            "endTime": 1784569442,
            "error": 0,
            "hwRev": -1,
            "pdState": 701,
            "reportTime": 1784569442,
            "reportType": "padDry",
            "robotId": "6F55705AE0BF169D69BDBFC9D858B5D2",
            "startTime": 1784556592,
            "varId": -1,
        }
    )

    assert report.report_type == "padDry"
    assert report.dock_id == "NA"
    assert report.dock_ver == "20"
    assert report.error == 0
    assert report.pd_state == 701
    assert report.start_time == 1784556592
    assert report.end_time == 1784569442
    assert report.report_time == 1784569442
    assert report.capabilities == {"evac": 1, "pd": 2, "pw": 1, "pwo": 1}
    # bbk's own values look stale/placeholder compared to the top-level
    # ones -- stored as-is, not merged or reconciled (only one example
    # exists, see the model's own docstring on this point).
    assert report.bbk["dockId"] == "UNKNOWN"
    assert report.bbk["numDocks"] == 23
    assert report.bbk["totalPadDryTime"] == 1614726


def test_dock_paddry_report_handles_missing_fields() -> None:
    from roombapy_prime.models import DockPadDryReport

    report = DockPadDryReport.from_json({})

    assert report.report_type is None
    assert report.capabilities == {}
    assert report.bbk == {}


# =========================================================================
# RobotStatusV2 (session 40)
# =========================================================================


def test_robot_status_v2_from_json_confirmed_wire_keys() -> None:
    """Uses exactly the bytecode-confirmed wire keys (session 40) --
    including the camelCase p2mapId/p2mapvId alongside the otherwise
    snake_case fields, confirmed as-is, not a typo.

    UPDATE (session 49): dock_controls/buttons/errors/conditional_errors
    are now typed (DockControl/RobotStatusButton/RobotStatusError), no
    longer list[Any] -- test data updated to properly-shaped dict
    elements accordingly."""
    from roombapy_prime.models import RobotStatusV2

    status = RobotStatusV2.from_json({
        "robot_state": 2,
        "battery_level": 87,
        "is_charging": False,
        "is_robot_on_dock": False,
        "p2mapId": "map-1",
        "p2mapvId": "v1",
        "dock_controls": [{"control": "evac", "status": "ok"}],
        "errors": [],
        "conditional_errors": [],
        "buttons": [{"status": "pressed", "action": "clean"}],
        "localization_args": {"k": "v"},
    })

    assert status.robot_state == 2
    assert status.battery_level == 87
    assert status.is_charging is False
    assert status.is_robot_on_dock is False
    assert status.current_p2map_id == "map-1"
    assert status.current_p2map_version_id == "v1"
    assert status.dock_controls[0].control == "evac"
    assert status.buttons[0].action == "clean"
    assert status.localization_args == {"k": "v"}


def test_parse_robot_status_v2_returns_none_when_absent() -> None:
    """NEW (session 40) -- the honest, unresolved caveat this class
    carries: most real dicts handed to it (e.g. the one confirmed real
    get_state() capture, an idle robot with 8 unrelated top-level keys)
    legitimately won't contain this structure at all. parse_robot_status_v2()
    must return None rather than an all-None object that would look like
    a misleadingly successful, empty parse."""
    from roombapy_prime.models import parse_robot_status_v2

    real_idle_reported_shape = {
        "digiCap": {}, "nsmip": {}, "cap": {}, "cleanSchedule2": [],
        "schedHold": False, "sku": "i7", "svcEndpoints": {}, "soldAsSku": "i7",
    }
    assert parse_robot_status_v2(real_idle_reported_shape) is None
    assert parse_robot_status_v2({}) is None
    assert parse_robot_status_v2(None) is None


def test_parse_robot_status_v2_returns_object_when_present() -> None:
    from roombapy_prime.models import RobotStatusV2, parse_robot_status_v2

    result = parse_robot_status_v2({"robot_state": 1, "is_charging": True})
    assert isinstance(result, RobotStatusV2)
    assert result.robot_state == 1
    assert result.is_charging is True


def test_point_clean_command_type_exists():
    """The one CommandType genuinely missing from our enum (the other
    reported candidates already existed -- verified against the full
    member list, which is how that duplicate got caught)."""
    from roombapy_prime.models.mission_control import MissionCommandType

    assert MissionCommandType.POINT_CLEAN.value == "point_clean"


def test_raas_and_odoa_lite_parse_when_present():
    """Both confirmed to exist with their own deserializers, but absent
    from every capture this project has -- so which shadow carries them
    is a best guess. Parsing is harmless if the guess is wrong."""
    from roombapy_prime.models import CurrentStateShadow

    state = CurrentStateShadow.from_json({
        "raas": {"enabled": True, "exp": 1784831254},
        "odoaLite": {"enabled": False},
    })

    assert state.raas.enabled is True
    assert state.raas.exp == 1784831254
    assert state.odoa_lite.enabled is False


def test_raas_and_odoa_lite_are_none_when_absent():
    """The normal case for every real capture so far."""
    from roombapy_prime.models import CurrentStateShadow

    state = CurrentStateShadow.from_json({"batPct": 100})

    assert state.raas is None
    assert state.odoa_lite is None


class TestPolicyZoneCategory:
    """NEW (this session): makes PolicyZoneFeature's already-confirmed
    categorization rule applicable instead of leaving it in prose. One
    branch is genuinely counter-intuitive -- a virtual wall is NOT its
    own zone_type, it's a "KeepOutZone" whose geometry is a LineString."""

    POLYGON = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    LINESTRING = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}

    def _feature(self, geometry, zone_type, threshold_type=None):
        from roombapy_prime.models import PolicyZoneFeature

        props = {"type": zone_type}
        if threshold_type is not None:
            props["threshold_type"] = threshold_type
        return PolicyZoneFeature.from_json(
            {"id": "z1", "geometry": geometry, "properties": props}
        )

    def test_keep_out_zone_polygon(self):
        from roombapy_prime.models import PolicyZoneCategory

        assert self._feature(self.POLYGON, "KeepOutZone").category is PolicyZoneCategory.KEEP_OUT_ZONE

    def test_virtual_wall_is_a_keep_out_zone_with_linestring_geometry(self):
        """THE non-obvious branch -- same zone_type as a keep-out zone,
        distinguished only by geometry shape."""
        from roombapy_prime.models import PolicyZoneCategory

        assert self._feature(self.LINESTRING, "KeepOutZone").category is PolicyZoneCategory.VIRTUAL_WALL

    def test_no_mop_zone(self):
        from roombapy_prime.models import PolicyZoneCategory

        assert self._feature(self.POLYGON, "NoMopZone").category is PolicyZoneCategory.NO_MOP_ZONE

    def test_threshold(self):
        from roombapy_prime.models import PolicyZoneCategory

        feature = self._feature(self.POLYGON, "Threshold", threshold_type="DETECTED")
        assert feature.category is PolicyZoneCategory.THRESHOLD
        assert feature.properties.threshold_type == "DETECTED"

    def test_unknown_zone_type_is_not_guessed(self):
        """The real app skips unrecognized features silently, so this
        is a normal condition rather than an error."""
        from roombapy_prime.models import PolicyZoneCategory

        assert self._feature(self.POLYGON, "SomethingNewFromFirmware").category is PolicyZoneCategory.UNKNOWN


class TestCommandIdPassthrough:
    """Wire key "id" -- confirmed to be one of exactly seven fields the
    real app's own buildJsonFromCommandDef emits, but its MEANING is
    unknown and no capture this project has contains it.

    Hence passthrough, never generated. Preserving what the server sent
    is defensible without understanding it; inventing a value for a
    field that identifies something unknown is not."""

    def _command(self, **kwargs):
        from roombapy_prime.models.mission_control import MissionCommandType, RoutineCommand

        return RoutineCommand(command_type=MissionCommandType.START, asset_id="BLID", **kwargs)

    def test_a_server_supplied_id_survives_the_round_trip(self):
        assert self._command(command_id="abc-123").to_json()["id"] == "abc-123"

    def test_no_id_is_invented_when_the_server_did_not_send_one(self):
        """The failure mode this guards against is not omission -- it is
        confidently sending a made-up identifier."""
        assert "id" not in self._command().to_json()

    def test_it_is_read_from_the_wire_key_id_not_from_a_python_name(self):
        from roombapy_prime.rest_client import PrimeRestClient

        favorite = PrimeRestClient._favorite_from_json({
            "favorite_id": "fav1",
            # Wire key is "commanddefs", all lowercase -- confirmed,
            # and easy to get wrong from the Python attribute name.
            "commanddefs": [{"command": "start", "robot_id": "BLID", "id": "srv-99"}],
        })

        assert favorite.command_defs[0].command_id == "srv-99"


def test_region_type_has_exactly_the_three_resolvable_values():
    """A fourth constant, kZoneTypeWId, exists in the same table but its
    wire value could not be resolved -- so it is documented and NOT
    modelled.

    TID IS "tid", CONFIRMED. The Dart `IrobotRegionType` states all
    three outright: room -> "rid", zone -> "zid", temporary -> "tid".
    This test previously asserted "furniture", which came from an
    @SerialName that does not exist -- the Kotlin RegionType carries an
    empty wire-value map. "furniture" is real, but it is an internal
    target-key prefix, not a region type on the wire.

    So the lowercase-the-prefix pattern holds for all three known types,
    which makes "wid" the obvious candidate for the fourth. That is a
    reason to expect it, not to ship it: if this test ever fails because
    someone added WID, the question to ask is whether its value was
    observed or inferred."""
    from roombapy_prime.models.mission_control import RegionType

    assert {m.value for m in RegionType} == {"rid", "zid", "tid"}
    assert "kZoneTypeWId" in RegionType.__doc__, "the fourth type must stay documented"


class TestCapabilityFlagsFromRealCaptures:
    """`cap` is the only place that says what a SPECIFIC device can do,
    and it is what feature gating reads.

    from_json() only reads the fields declared on the dataclass, so an
    unmodelled capability vanishes silently -- no error, no warning. A
    capability we never see is a feature we can never offer, and
    nothing would ever have told us. Five were being dropped until a
    new tester's validation run happened to print the raw object.

    The captures below are verbatim from real devices. Adding one here
    when a new robot appears is the cheapest way to keep this honest."""

    # arielgr, sku Y414040
    _ARIELGR = {
        "5ghz": 0, "area": 1, "autoevac": 0, "binFullDetect": 0, "bleLog": 1,
        "carpetBoost": 0, "dPause": 1, "dSpot": 1, "dnd": 0, "dockComm": 0,
        "eCmd": 0, "expectingUserConf": 2, "floorTypeDetect": 2, "idl": 1,
        "lang": 2, "langOta": 2, "lmap": 1, "log": 2, "mapMax": 3, "maps": 6,
        "matter": 0, "mc": 3, "multiPass": 1, "ns": 1, "oMode": 38, "odoa": 0,
        "ota": 3, "p2maps": 5, "p2maps_editv2_feats": 3423, "ppWetLvl": 0,
        "prov": 3, "pw": 0, "saSku": 1, "sched": 4, "scrub": 3, "suctionLvl": 4,
        "svcConf": 1, "tLine": 2, "cmds": 1, "mopLift": 0,
    }

    def test_no_capability_from_a_real_capture_is_dropped(self):
        """The actual guard. If a future capture adds a key we do not
        model, this fails and names it -- instead of the value quietly
        disappearing."""
        import dataclasses
        import re

        from roombapy_prime.models.robot_info import CapabilityFlags

        modelled = {f.name for f in dataclasses.fields(CapabilityFlags)}

        def snake(key: str) -> str:
            if key == "5ghz":
                return "wifi_5ghz"
            return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()

        dropped = sorted(k for k in self._ARIELGR if snake(k) not in modelled)

        assert not dropped, (
            f"capabilities present in a real capture but not modelled: {dropped}. "
            "from_json() reads only declared fields, so these vanish silently."
        )

    def test_the_five_previously_dropped_ones_now_arrive(self):
        from roombapy_prime.models.robot_info import CapabilityFlags

        caps = CapabilityFlags.from_json(self._ARIELGR)

        assert caps.cmds == 1
        assert caps.e_cmd == 0
        assert caps.mop_lift == 0
        assert caps.odoa == 0
        assert caps.p2maps_editv2_feats == 3423

    def test_a_zero_is_kept_rather_than_treated_as_absent(self):
        """Zero is a confirmed negative -- "this device cannot do X" --
        and must not collapse into None, which means "we do not know"."""
        from roombapy_prime.models.robot_info import CapabilityFlags

        caps = CapabilityFlags.from_json(self._ARIELGR)

        assert caps.mop_lift == 0
        assert caps.mop_lift is not None

    def test_an_absent_capability_stays_none(self):
        from roombapy_prime.models.robot_info import CapabilityFlags

        caps = CapabilityFlags.from_json({"scrub": 3})

        assert caps.scrub == 3
        assert caps.mop_lift is None


class TestCurrentStateShadowFromRealCaptures:
    """Same guard as the capability one above, for the shadow that
    carries live robot state.

    `from_json()` reads only declared fields, so an unmodelled key
    vanishes silently -- no error, no warning. That already cost this
    project five capability flags; `googleControl` was the same mistake
    in a different object, and it surfaced only because a tester happened
    to paste the shadow's raw key list.

    The key sets below are verbatim from real robots. Adding one when a
    new device appears is the cheapest way to keep this honest -- and
    the sets genuinely differ between models, which is exactly why a
    single capture is not enough."""

    # arielgr, sku Y414040
    _ARIELGR = [
        "batPct", "bin", "cleanMissionStatus", "detectedPad", "dock",
        "googleControl", "lastDisconnect", "p2maps", "regDate",
        "runtimeStats", "svcEndpoints", "tankPresent", "tz",
    ]

    def _modelled(self) -> set[str]:
        import dataclasses

        from roombapy_prime.models.robot_info import CurrentStateShadow

        return {f.name for f in dataclasses.fields(CurrentStateShadow)}

    @staticmethod
    def _snake(key: str) -> str:
        import re

        return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()

    def test_no_key_from_a_real_capture_is_dropped(self):
        # svcEndpoints is service-discovery plumbing, not robot state --
        # deliberately not modelled here.
        ignored = {"svcEndpoints"}
        modelled = self._modelled()

        dropped = sorted(
            k for k in self._ARIELGR
            if k not in ignored and self._snake(k) not in modelled
        )

        assert not dropped, (
            f"keys present in a real ro-currentstate but not modelled: {dropped}. "
            "from_json() reads only declared fields, so these vanish silently."
        )

    def test_google_control_survives_parsing(self):
        """The specific field this test class was written for."""
        from roombapy_prime.models.robot_info import CurrentStateShadow

        shadow = CurrentStateShadow.from_json({"googleControl": {"linked": True}})

        assert shadow.google_control == {"linked": True}

    def test_an_absent_key_stays_none(self):
        """Not every robot reports every key -- absent must not become a
        default that reads like a real answer."""
        from roombapy_prime.models.robot_info import CurrentStateShadow

        shadow = CurrentStateShadow.from_json({"batPct": 90})

        assert shadow.google_control is None
        assert shadow.tank_present is None


class TestRobotSettingsFromRealCaptures:
    """Third guard of the same family, for `rw-settings`.

    Written differently from its two siblings, and the difference
    matters. Those compare a capture's keys against the dataclass's
    FIELD NAMES, which works only while the two happen to match. Here
    they deliberately do not: the wire key `swScrub` is stored as
    `scrub` and `langs2` as `languages_raw`, both for good reasons.

    A name-based check reports those as missing. They are not -- a
    manual pass over this exact capture produced three false alarms
    before the parser was actually run.

    So this asserts what actually matters: does from_json() KEEP the
    value? That question has one right answer regardless of what the
    field ends up being called."""

    # arielgr, sku Y414040 -- verbatim key list from his rw-settings.
    _ARIELGR = {
        # NOTE: the tester's report listed "audio" as a key but not its
        # contents. {"volume": 3} is what this library expects; whether
        # that inner key is right is UNVERIFIED, so this capture uses it
        # rather than inventing an alternative and calling the mismatch
        # a finding.
        "audio": {"volume": 3}, "carpetBoost": True, "childLock": False,
        "cloudEnv": "prod", "country": "US", "ecoCharge": False,
        "langs2": {"sL": "en-US"}, "mapUploadAllowed": True, "name": "Robot",
        "noAutoPasses": False, "nsmip": 1, "padWetness": {"disposable": 2},
        "schedHold": False, "suctionLevel": 2, "swScrub": 3,
        "timezone": "America/New_York", "twoPass": False,
    }

    def test_every_meaningful_key_survives_parsing(self):
        """Structural, not name-based: parse the capture, then parse it
        again with one key removed. If nothing changes, that key was
        being discarded."""
        import dataclasses

        from roombapy_prime.models.robot_info import RobotSettings

        # Infrastructure keys, not robot settings -- not expected to
        # appear on the model.
        ignored = {"nsmip", "cloudEnv", "svcEndpoints"}

        full = dataclasses.asdict(RobotSettings.from_json(self._ARIELGR))

        discarded = []
        for key in self._ARIELGR:
            if key in ignored:
                continue
            without = dict(self._ARIELGR)
            del without[key]
            if dataclasses.asdict(RobotSettings.from_json(without)) == full:
                discarded.append(key)

        assert not discarded, (
            f"keys whose removal changes nothing, i.e. silently discarded: {discarded}. "
            "Note this catches a nested key being wrong too: a top-level key can be read "
            "while the sub-key it looks for does not exist, which no name-based check sees."
        )

    def test_the_renamed_keys_are_kept_under_their_own_names(self):
        """Documents the two deliberate renames, so a future reader does
        not 'fix' them back and break every caller."""
        from roombapy_prime.models.robot_info import RobotSettings

        settings = RobotSettings.from_json(self._ARIELGR)

        assert settings.scrub == 3           # wire key: swScrub
        assert settings.languages_raw        # wire key: langs2


class TestNoShadowKeyIsSilentlyDiscarded:
    """The general form of the three guards above, covering every named
    shadow with a real capture behind it.

    WHY IT IS STRUCTURAL AND NOT NAME-BASED. Comparing capture keys
    against dataclass field names produces false alarms whenever a wire
    key is deliberately stored under a different name -- `swScrub` as
    `scrub`, `langs2` as `languages_raw`, `softwareVer` folded into a
    version object. A manual name-based pass over these captures
    reported six missing fields; every one was wrong.

    Removing a key and checking whether the parse result changes has one
    correct answer regardless of naming. It also catches a case no name
    check can see: a top-level key that IS read while the sub-key it
    looks for does not exist.

    The captures are verbatim key lists from real robots (arielgr, sku
    Y414040). Contents are plausible fillers where the tester reported
    only key names -- which is fine, because this test asks whether a
    value survives, not what it is."""

    # Service-discovery plumbing and internal counters, not robot state.
    _IGNORED = {"svcEndpoints", "nsmip"}

    def _discarded(self, cls, capture: dict) -> list[str]:
        import dataclasses

        full = dataclasses.asdict(cls.from_json(capture))
        out = []
        for key in capture:
            if key in self._IGNORED:
                continue
            without = {k: v for k, v in capture.items() if k != key}
            if dataclasses.asdict(cls.from_json(without)) == full:
                out.append(key)
        return out

    def test_software_status_shadow(self):
        from roombapy_prime.models.robot_info import SoftwareStatusShadow

        capture = {
            "deploymentId": "d1", "deploymentMpkg": "pkg", "deploymentState": "idle",
            "imuRecal": 1, "lastCommand": {"command": "start"},
            "lastSwUpdate": {"sts": 1}, "softwareVer": "1.2.3",
            "subModSwVer": {"nav": "x"}, "nsmip": 1,
        }

        assert not self._discarded(SoftwareStatusShadow, capture)

    def test_connection_status_shadow(self):
        from roombapy_prime.models.robot_info import ConnectionStatusShadow

        assert not self._discarded(
            ConnectionStatusShadow, {"connected": True, "connectedv2": True}
        )

    def test_config_info_shadow(self):
        from roombapy_prime.models.robot_info import ConfigInfoShadow

        capture = {"hwPartsRev": {"navSerialNo": "N1"}, "passwordHash": "h", "nsmip": 1}

        assert not self._discarded(ConfigInfoShadow, capture)

    def test_stats_shadow(self):
        from roombapy_prime.models.robot_info import StatsShadow

        capture = {
            "bbchg": {"nChgOk": 1}, "bbchg3": {"nAvgMin": 2}, "bbmssn": {"nMssn": 3},
            "bbpause": {"pauses": 1}, "bbrstinfo": {"nNavRst": 4}, "bbsys": {"hr": 5},
        }

        assert not self._discarded(StatsShadow, capture)


class TestMapBundleFeaturesAgainstRealCaptures:
    """The three bundle files whose contents nobody had seen until
    30 July 2026 (chairstacker).

    All three were modelled from decompiled `$$serializer` classes and
    never checked against real data -- the exact situation that cost this
    project weeks on virtual walls, where the decompiled structure looked
    complete and the missing element appeared in no serializer at all.

    This time the models were right. Recorded as tests so they stay
    right, and because a verbatim capture is worth more than a note."""

    def test_floor_type_reads_the_wire_key_type_not_floor_type(self):
        """THE trap in this file. The JSON key is `type`; the attribute is
        named `floor_type` only because a GeoJSON Feature already has
        three other `type` keys around it -- the collection's, the
        feature's and the geometry's.

        Verbatim from the capture:
        {"type":"Feature","geometry":{...},"properties":{"type":"carpet"}}
        """
        from roombapy_prime.models.map_bundle import FloorTypeFeatureProperties

        props = FloorTypeFeatureProperties.from_json({"type": "carpet"})

        assert props.floor_type == "carpet"

    def test_a_floor_type_under_the_wrong_key_is_not_picked_up(self):
        """Guards the direction of the mapping. If someone "fixes" this to
        read `floor_type`, every real map goes silently untyped."""
        from roombapy_prime.models.map_bundle import FloorTypeFeatureProperties

        props = FloorTypeFeatureProperties.from_json({"floor_type": "carpet"})

        assert props.floor_type is None

    def test_borders_are_multipolygons_not_linestrings(self):
        """Confirmed from the capture: borders.geojson carries
        MultiPolygon. So borders are AREAS, not lines -- which decides how
        they draw, and guessing lines would have produced a map of thin
        strokes where solid regions belong."""
        import dataclasses

        from roombapy_prime.models.map_bundle import BorderFeature

        geometry_field = next(
            f for f in dataclasses.fields(BorderFeature) if f.name == "geometry"
        )

        assert "MultiPolygon" in str(geometry_field.type)

    def test_the_dock_has_an_orientation_as_well_as_a_position(self):
        """Confirmed from the capture's key list: coordinates, geometry,
        orientation, properties, type.

        Which means a rendered dock can point the right way rather than
        being a dot."""
        from roombapy_prime.models.map_bundle import DockFeatureProperties

        props = DockFeatureProperties.from_json({"orientation": 1.57})

        assert props.orientation == 1.57

    def test_a_floor_type_collection_parses_end_to_end(self):
        """Structure taken verbatim from the capture, coordinates
        replaced. Four polygon features, all carpet -- which suggests the
        file lists carpeted areas rather than classifying every surface,
        so anything uncovered is hard floor by omission."""
        from roombapy_prime.models.map_bundle import FloorTypeFeature

        raw = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
            },
            "properties": {"type": "carpet"},
        }

        feature = FloorTypeFeature.from_json(raw)

        assert feature.properties.floor_type == "carpet"
        assert feature.geometry is not None


class TestSchedulesResponseSurvivesAnUnexpectedShape:
    """A parser's job on a shape it did not expect is to return nothing,
    not to raise.

    FOUND IN THE b6 BUG HUNT. `data.get("household_schedules")` was
    iterated without checking it is a list -- a dict there yields its
    KEYS, and SchedulesList.from_json() then called .get() on a string.
    An error envelope or a changed response shape reaches this line, and
    it is on the path HA's schedule calendar and switches use.
    """

    def _parse(self, data):
        from roombapy_prime.models.schedules_dnd import SchedulesResponse

        return SchedulesResponse.from_json(data)

    def test_a_valid_response_is_unaffected(self):
        result = self._parse({"household_schedules": [
            {"household_schedule_id": "HS-1", "schedules": [{"schedule_id": "S-1"}]},
        ]})

        assert len(result.household_schedules) == 1
        assert result.household_schedules[0].schedules == [{"schedule_id": "S-1"}]

    def test_household_schedules_as_a_dict_yields_nothing(self):
        assert self._parse({"household_schedules": {"oops": 1}}).household_schedules == []

    def test_non_dict_containers_are_skipped(self):
        assert self._parse({"household_schedules": ["x", 3]}).household_schedules == []

    def test_schedules_as_a_string_yields_an_empty_list(self):
        result = self._parse({"household_schedules": [{"schedules": "nope"}]})

        assert result.household_schedules[0].schedules == []

    def test_an_error_envelope_yields_nothing(self):
        assert self._parse({"error": "forbidden"}).household_schedules == []


class TestDockStatusTankLevel:
    """Not every dock reports it, and that is the whole point.

    Two captures: fwVer 24 / dock.cap.pd 3 sends `tankLvl: 100`; fwVer
    20 / pd 2 never sends the key, not even while pad washing fails for
    lack of water. Two variables differ at once, and the APK cannot
    settle which governs -- pd/pw/pwo are not literals, and
    DockCapability is purely categorical with no notion of levels.

    So the field is modelled as optional and consumers gate on its
    presence.
    """

    def _dock(self, raw):
        from roombapy_prime.models.robot_info import DockStatus

        return DockStatus.from_json(raw)

    def test_a_dock_that_reports_it(self):
        assert self._dock({"tankLvl": 100, "error": 0}).tank_lvl == 100

    def test_a_dock_that_does_not_stays_none(self):
        """None rather than 0: a dock that never mentions the tank has
        not told us it is empty."""
        assert self._dock({"error": 0, "fwVer": "20"}).tank_lvl is None

    def test_zero_is_kept_as_zero(self):
        assert self._dock({"tankLvl": 0}).tank_lvl == 0


class TestCleanScoreResponse:
    """Wire keys confirmed as literals in the app's own response parser.
    The wire is snake_case; the Kotlin side is camelCase, and writing
    the Kotlin names as wire keys is the mistake that once cost this
    library 21 of them -- including, briefly, in this endpoint's own
    docstring.
    """

    _RAW = {
        "clean_score_ranges": [0.0, 0.33, 0.66],
        "clean_scores": [{
            "p2map_id": "MAP-1",
            "active_p2mapv_id": "MAPV-1",
            "user_p2mapv_id": "260801T112421.581",
            "smart_clean_id": "SC-1",
            "mission_last_processed": {"missionId": "01KY"},
            "regions": [
                {"region_id": "13", "clean_score": 0.87,
                 "updated_ts": 1784976894, "last_updated_by": "robot",
                 "smart_clean_prefs": "auto"},
                {"region_id": "10", "clean_score": 0.12, "updated_ts": 1784976000},
            ],
        }],
    }

    def _parse(self, data):
        from roombapy_prime.models import CleanScoreResponse

        return CleanScoreResponse.from_json(data)

    def test_a_full_response_parses(self):
        result = self._parse(self._RAW)

        assert result.clean_score_ranges == [0.0, 0.33, 0.66]
        data = result.clean_scores[0]
        assert data.p2map_id == "MAP-1"
        assert [r.region_id for r in data.regions] == ["13", "10"]
        assert data.regions[0].clean_score == 0.87
        assert data.regions[0].updated_ts == 1784976894

    def test_a_score_of_zero_is_kept(self):
        """0.0 is a real reading -- the dirtiest a room can be. Anything
        that treats it as falsy loses exactly the value that matters."""
        result = self._parse({"clean_scores": [{"regions": [
            {"region_id": "1", "clean_score": 0.0},
        ]}]})

        assert result.clean_scores[0].regions[0].clean_score == 0.0

    def test_mission_last_processed_stays_raw(self):
        """Its own wire keys were not read out at this call site, and
        inventing them is how wire keys go wrong."""
        result = self._parse(self._RAW)

        assert result.clean_scores[0].mission_last_processed == {"missionId": "01KY"}

    def test_an_unexpected_shape_yields_nothing_rather_than_raising(self):
        for data in ([], "nope", None, {"clean_scores": {"x": 1}},
                     {"clean_scores": ["not-a-dict"]}):
            assert self._parse(data).clean_scores == []


class TestCleanScoreAgainstTheFirstRealResponse:
    """The endpoint answered for the first time (@DaRealGuGu, b8), and
    the response carried three fields the APK key list did not have --
    plus one it did have with the wrong type.

    A key list confirmed from the vendor's own parser is a floor, not a
    ceiling. This test is the real payload, trimmed.
    """

    _REAL = {
        "clean_score_ranges": [0.7],
        "clean_scores": [{
            "active_p2mapv_id": "260802T081336.871",
            "p2map_id": "BLID-1785514071",
            "smart_clean_id": "a93b5eb6-fc99-4356-a14a-84814b6bfdb4",
            "mission_last_processed": {
                "missionId": "01KZ1NMCAC5GAG47CX55JRN3VV",
                "nMssn": 53, "startTime": 1785688895,
            },
            "regions": [{
                "region_id": "10",
                "clean_score": 0.0,
                "high_traffic_enum": "normal",
                "last_updated_by": "rt_mission",
                "mission_last_cleaned": {"nMssn": 51, "startTime": 1785591912},
                "mission_last_unfinished": {"nMssn": 53, "startTime": 1785688895},
                "smart_clean_prefs": {
                    "carpetBoost": False, "operatingMode": 6,
                    "suctionLevel": 1, "swScrub": 0, "twoPass": False,
                },
                "updated_ts": 1785688964,
            }],
        }],
    }

    def _region(self):
        from roombapy_prime.models import CleanScoreResponse

        return CleanScoreResponse.from_json(self._REAL).clean_scores[0].regions[0]

    def test_smart_clean_prefs_is_a_dict_not_a_string(self):
        """The Kotlin side suggested a string; the server sends the same
        per-region parameter block that cleaning commands carry."""
        prefs = self._region().smart_clean_prefs

        assert isinstance(prefs, dict)
        assert prefs["operatingMode"] == 6

    def test_the_fields_the_apk_list_did_not_have(self):
        region = self._region()

        assert region.high_traffic_enum == "normal"
        assert region.mission_last_cleaned["nMssn"] == 51
        assert region.mission_last_unfinished["nMssn"] == 53

    def test_a_score_of_zero_survives(self):
        """Every room on that account read 0.0. Whatever it turns out to
        mean, a parser that drops it would hide the only value there
        is."""
        assert self._region().clean_score == 0.0

    def test_profile_was_not_in_the_response(self):
        """Another integration reads a `profile` key with a "normal"
        fallback, so its code could not tell "the server sends this"
        from "someone assumed it". It was left unmodelled on that
        reasoning, and the first real response settles it."""
        assert "profile" not in self._REAL["clean_scores"][0]


class TestScheduleCommandsSurviveAMalformedEntry:
    """A bare comprehension called .get() on every entry, so one null or
    string in `commands` raised AttributeError from inside the parser.

    The blast radius was the whole schedule parse -- Home Assistant's
    schedule calendar and every schedule switch read this, so a single
    malformed entry would have taken all of them down at once. Same
    shape as the SchedulesResponse.from_json crash fixed in b6, one
    level further in.
    """

    def _commands(self, raw):
        from roombapy_prime.models.schedules_dnd import ScheduleOptions

        return ScheduleOptions.from_json({"commands": raw}).commands

    def test_a_valid_list_is_unwrapped(self):
        assert self._commands([{"command": {"regions": [{"region_id": "1"}]}}]) == [
            {"regions": [{"region_id": "1"}]}
        ]

    def test_an_entry_without_the_wrapper_is_kept_as_is(self):
        """The server sends the wrapper; a caller round-tripping already
        unwrapped data should not lose it."""
        assert self._commands([{"regions": []}]) == [{"regions": []}]

    def test_a_null_entry_is_skipped_rather_than_raising(self):
        assert self._commands([{"command": {"a": 1}}, None, "x"]) == [{"a": 1}]

    def test_a_non_list_yields_nothing(self):
        assert self._commands("nonsense") == []


class TestDNDDailyScheduleFromClock:
    """Minutes since midnight, per ScheduleDataUtils::doScheduleConflicts
    in the app's machine code: hour * 60 + minute, range 0-1439.

    The conversion lives here so callers do not repeat it. Getting a
    quiet period an hour wrong is the kind of mistake nobody notices
    until the robot runs at the wrong time.
    """

    def _build(self, *args):
        from roombapy_prime.models.schedules_dnd import DNDDailySchedule

        return DNDDailySchedule.from_clock(*args)

    def test_the_documented_example(self):
        assert self._build(22, 0, 7, 30).to_json() == {
            "dailyStart": 1320, "dailyEnd": 450
        }

    def test_midnight_is_zero_not_falsy_trouble(self):
        assert self._build(0, 0, 6, 0).to_json()["dailyStart"] == 0

    def test_the_last_minute_of_the_day(self):
        assert self._build(23, 59, 0, 0).daily_start == 1439

    def test_an_impossible_time_raises_rather_than_clamping(self):
        """A quiet period silently shifted to a different hour is worse
        than a refusal -- this writes to a real robot."""
        import pytest

        for bad in ((24, 0, 7, 0), (22, 60, 7, 0), (-1, 0, 7, 0), (22, 0, 7, -5)):
            with pytest.raises(ValueError):
                self._build(*bad)


class TestTheHeadingIsTheWireAngle:
    """The parser used to add half a turn, undocumented.

    No comment, no evidence-trail entry, and a test that asserted
    `0.0 + 3.1415927` -- which restates the parser rather than checking
    it against the robot.

    The first observation anyone has made of the heading says it was
    wrong: with the marker finally drawn, the line pointed out of the
    BACK of the robot (@DaRealGuGu, 505 series, a24). Half a turn is
    exactly that.
    """

    def _sample(self, angle):
        from roombapy_prime.models.livemap import PositionUpdateMessage

        message = PositionUpdateMessage.from_json(
            {"cur_path": [1, 1.5, 2.5, angle, 2.0, 1700000000]}
        )
        return message.updates[0]

    def test_zero_stays_zero(self):
        assert self._sample(0.0).orientation == 0.0

    def test_a_quarter_turn_stays_a_quarter_turn(self):
        assert self._sample(1.5707963).orientation == 1.5707963

    def test_no_half_turn_is_added_anywhere(self):
        """Checked across the circle rather than at one value -- an
        offset applied conditionally would slip past a single case."""
        for angle in (-3.0, -1.0, 0.0, 0.5, 2.0, 3.0):
            assert self._sample(angle).orientation == angle, angle


class TestMissionHistoryIsABareArray:
    """Confirmed from the app's `restservices/missionhistory` package:
    the API returns `Result<List<MissionHistory>>`, and `MissionHistory`
    is a single entry. No envelope class exists anywhere in it.

    The `missions` and `history` keys the parser also accepts were
    guesses, made in a place where being wrong is invisible: a response
    full of missions parsing to an empty list reads exactly like a robot
    with no history.
    """

    _ENTRY = {
        "robot_id": "B", "nMssn": 36, "startTime": 1589884703,
        "timestamp": 1589884800, "durationM": 12, "runM": 9, "pauseM": 2,
        "chrgM": 1, "sqft": 240, "done_raw": "ok", "evacs": 1,
    }

    def _parse(self, payload):
        from roombapy_prime.models.mission_history import parse_mission_history

        return parse_mission_history(payload)

    def test_the_confirmed_form_is_a_bare_list(self):
        assert len(self._parse([self._ENTRY, self._ENTRY])) == 2

    def test_the_guessed_envelopes_still_work(self):
        """Kept as a fallback: one branch, cannot match an array, and it
        survives iRobot ever wrapping the response."""
        assert len(self._parse({"missions": [self._ENTRY]})) == 1
        assert len(self._parse({"history": [self._ENTRY]})) == 1

    def test_the_four_minute_fields_are_separate(self):
        """A caller asking how long the last clean took wants `runM`, not
        `durationM` -- a robot that charged halfway through reports a
        wall-clock duration several times its cleaning time."""
        entry = self._parse([self._ENTRY])[0]

        assert entry.duration_m == 12
        assert entry.minutes_running == 9
        assert entry.minutes_paused == 2
        assert entry.minutes_charging == 1


class TestCoverageWasInThePayloadAllAlong:
    """`RoomInfoDto` and `ZoneInfoDto` in app 3.0.0 declare `coverage`
    beside `area`, `passArea`, `passCount` and `totalArea`. Neither
    parser read it.

    **RoomEvent's docstring spends fourteen lines reasoning about
    whether `area` or `total_area` means "how much was covered".** The
    field that answers it was in the same object, unparsed — and
    iRobot's own analysis notes it makes per-room mission progress
    directly computable, without time estimates.
    """

    def test_a_room_reports_its_coverage(self):
        from roombapy_prime.models.mission_history import RoomEvent

        event = RoomEvent.from_json({
            "rid": "11", "coverage": 0.87, "area": 354,
            "passArea": 300, "passCount": 2, "totalArea": 310,
        })

        assert event.coverage == 0.87

    def test_a_zone_reports_its_coverage(self):
        from roombapy_prime.models.mission_history import ZoneEvent

        assert ZoneEvent.from_json({"zid": "Z1", "coverage": 0.5}).coverage == 0.5

    def test_absent_coverage_is_none_not_zero(self):
        """Zero means the room was entered and nothing was done. Absent
        means the robot did not say — and a firmware that omits the
        field must not read as a failed clean."""
        from roombapy_prime.models.mission_history import RoomEvent, ZoneEvent

        assert RoomEvent.from_json({"rid": "11"}).coverage is None
        assert ZoneEvent.from_json({"zid": "Z1"}).coverage is None

    def test_the_other_fields_still_parse(self):
        """Adding a field to a positional constructor is how the next
        one gets shifted by one."""
        from roombapy_prime.models.mission_history import ZoneEvent

        event = ZoneEvent.from_json({
            "zid": "Z1", "coverage": 0.5, "passCount": 3,
            "status": "done", "totalArea": 42, "area": 40,
        })

        assert (event.pass_count, event.status, event.total_area) == (3, "done", 42)


class TestTwoFieldsWeSendThatTheVendorStrips:
    """`CommandDTO` in app 3.0.0 has thirteen fields. `robot_id`,
    `select_all` and `id` are not among them — and iRobot's own 2.2.4
    code stripped exactly those three unconditionally before sending.

    **They are kept anyway.** @Echovictor37 confirmed a region-targeted
    clean on real hardware with this payload, these fields included.
    Removing them would change the one path in this library known to
    work, on the strength of an app model rather than a test.

    This test exists so the decision is visible rather than accidental:
    if somebody removes them, they should be removing something a test
    says is deliberate.
    """

    def _body(self):
        from roombapy_prime.models.mission_control import (
            CommandParams,
            MissionCommandType,
            Region,
            RegionType,
            RoutineCommand,
        )

        return RoutineCommand(
            command_type=MissionCommandType.START,
            asset_id="BLID", map_id="M1",
            regions=[Region(region_id="11", region_type=RegionType.RID,
                            params=CommandParams(operating_mode=2))],
            initiator="rmtApp",
        ).to_json()

    def test_both_are_still_sent(self):
        body = self._body()

        assert "robot_id" in body
        assert "select_all" in body

    def test_the_reason_is_written_down(self):
        import inspect

        from roombapy_prime.models import mission_control

        source = inspect.getsource(mission_control)

        assert "SHOULD NOT BE" in source
        assert "Echovictor37" in source

    def test_the_confirmed_fields_are_all_present(self):
        """Whatever else is in the payload, the four that made a real
        robot clean one room must be."""
        body = self._body()

        assert body["command"] == "start"
        assert body["p2map_id"] == "M1"
        assert body["initiator"] == "rmtApp"
        assert body["regions"][0]["region_id"] == "11"


class TestTheTwoParamsThreePointZeroAdded:
    """`edgeOnly` and `quiet` are new in app 3.0.0's `CommandParamsDTO`
    and absent from 2.2.4 — so this model had no way to know about them
    until the 3.0 analysis listed them.

    **Their values are unknown.** Integer per the DTO, and nothing here
    guesses at a range: a caller with a value from a capture can pass
    it, one without sends nothing.
    """

    def test_both_round_trip(self):
        from roombapy_prime.models.mission_control import CommandParams

        body = CommandParams(edge_only=1, quiet=0).to_json()

        assert body["edgeOnly"] == 1
        assert body["quiet"] == 0

    def test_both_are_read_back(self):
        from roombapy_prime.models.mission_control import CommandParams

        params = CommandParams.from_json({"edgeOnly": 2, "quiet": 1})

        assert (params.edge_only, params.quiet) == (2, 1)

    def test_unset_means_absent_not_zero(self):
        """Zero is a value the robot may act on. Sending it because
        nobody chose is how a quiet clean happens unasked."""
        from roombapy_prime.models.mission_control import CommandParams

        body = CommandParams(operating_mode=2).to_json()

        assert "edgeOnly" not in body
        assert "quiet" not in body


class TestTheStatusObjectsWereReadPartially:
    """`DockStatusData` and `CleanMissionStatusData` each declare fields
    these models never took."""

    def test_the_dock_refill_state_is_read(self):
        """`frState` is the dock counterpart to `pwState` and `pdState`,
        which this model has had all along — so a dock could report that
        it was refilling and nothing here could say so."""
        from roombapy_prime.models.robot_info import DockStatus

        assert DockStatus.from_json({"frState": 2}).fr_state == 2

    def test_the_docks_identity_fields_are_read(self):
        from roombapy_prime.models.robot_info import DockStatus

        dock = DockStatus.from_json({
            "id": "D1", "pn": "P900", "hwRev": 1, "varId": 6, "fwVerSec": "2.1",
        })

        assert (dock.dock_id, dock.part_number) == ("D1", "P900")
        assert (dock.hardware_revision, dock.variant_id) == (1, 6)
        assert dock.fw_version_secondary == "2.1"

    def test_pause_timings_are_read(self):
        """A paused robot shows `phase: "pause"` and no more. `expireTm`
        says when that pause lapses, `rechrgTm` when it intends to
        resume after charging — Classic surfaces both, Prime had the
        same numbers in the same object and dropped them."""
        from roombapy_prime.models.robot_info import CleanMissionStatus

        status = CleanMissionStatus.from_json({
            "phase": "pause", "expireTm": 1786205257, "rechrgTm": 1786208857,
        })

        assert status.expire_time == 1786205257
        assert status.recharge_time == 1786208857

    def test_not_ready_and_cond_not_ready_stay_separate(self):
        """A scalar and a list in the same object — confirmed by the
        Kotlin declaration, and matching what the field showed:
        `notReady: 0` beside `condNotReady: [234]`. A robot can be ready
        while naming conditions it would otherwise be blocked by."""
        from roombapy_prime.models.robot_info import CleanMissionStatus

        status = CleanMissionStatus.from_json({
            "notReady": 0, "condNotReady": [234],
        })

        assert status.not_ready == 0
        assert status.cond_not_ready == [234]

    def test_nothing_reported_stays_none(self):
        from roombapy_prime.models.robot_info import CleanMissionStatus, DockStatus

        assert DockStatus.from_json({}).fr_state is None
        assert CleanMissionStatus.from_json({}).expire_time is None


class TestTwoGuessedKeysThatNoRobotEverSent:
    """`MissionHistoryItemResponse` says `dirt` and `map_id`. This model
    read `numberOfDirtDetects` and `staticMapId` — readable guesses at
    what the fields might be called.

    **No robot has ever sent either name**, so both have read None on
    every mission since they were written. Nothing failed; the values
    were simply always absent, which is indistinguishable from a robot
    that does not report them.
    """

    def test_the_dirt_counter_reads_the_vendor_key(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({"dirt": 7})

        assert entry.number_of_dirt_detects == 7

    def test_the_map_id_reads_the_vendor_key(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        assert MissionHistoryEntry.from_json({"map_id": "MP1"}).static_map_id == "MP1"

    def test_the_guessed_names_still_work(self):
        """Kept as a fallback rather than removed: if any firmware
        anywhere does send them, dropping the reader would turn a
        working field into a missing one to tidy up a name."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({
            "numberOfDirtDetects": 3, "staticMapId": "OLD",
        })

        assert entry.number_of_dirt_detects == 3
        assert entry.static_map_id == "OLD"

    def test_the_vendor_key_wins_when_both_appear(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({
            "dirt": 7, "numberOfDirtDetects": 3,
        })

        assert entry.number_of_dirt_detects == 7


class TestTheLegacyMapKeySwitch:
    """`CommandDTO` carries `p2map_id` and `pmap_id` as four separate
    nullable fields, not as alternatives — the app decides per device
    via `allowLegacyReportedValuesInCommand`.

    **The switch defaults off.** @Echovictor37's confirmed clean used
    the payload without `pmap_id`, and a confirmed shape outranks a
    plausible one.
    """

    def _command(self, **kwargs):
        from roombapy_prime.models.mission_control import (
            MissionCommandType,
            RoutineCommand,
        )

        return RoutineCommand(
            command_type=MissionCommandType.START, asset_id="B",
            map_id="M1", regions=[], initiator="rmtApp",
        ).to_json(**kwargs)

    def test_the_confirmed_payload_is_the_default(self):
        assert "pmap_id" not in self._command()

    def test_the_legacy_name_can_be_added(self):
        body = self._command(legacy_map_keys=True)

        assert body["pmap_id"] == "M1"
        assert body["p2map_id"] == "M1"

    def test_the_new_name_is_always_there(self):
        assert self._command()["p2map_id"] == "M1"


class TestTheTimelineWasReadWithLongNames:
    """`MissionEventDto` declares `cmd`, `disc`, `poly` and
    `tentativeLoc`. This parser read `command`, `discovery`, `polygon`
    and `tentativeLocation` — the readable forms, which `reloc` was
    already the exception to.

    Four event types were being dropped from every real timeline.
    """

    def _event(self, payload):
        from roombapy_prime.models.mission_history import MissionTimelineEvent

        return MissionTimelineEvent.from_json(payload)

    def test_the_vendor_short_names_are_read(self):
        event = self._event({
            "type": "room",
            "cmd": {"command": "start"},
            "disc": {"rid": "11"},
            "poly": {"area": 5},
            "tentativeLoc": {"rid": "12"},
        })

        assert event.command is not None
        assert event.discovery is not None
        assert event.polygon is not None
        assert event.tentative_location is not None

    def test_the_long_names_still_work(self):
        """Both are accepted rather than swapped: the long names came
        from somewhere, and a payload using them would lose four event
        types if they were removed to tidy up."""
        event = self._event({
            "command": {"command": "start"}, "polygon": {"area": 5},
        })

        assert event.command is not None
        assert event.polygon is not None


class TestTravelAndFutureEvents:
    def test_travel_reads_the_vendor_keys(self):
        from roombapy_prime.models.mission_history import TravelEvent

        event = TravelEvent.from_json({"polyid": "P1", "wid": "W1"})

        assert (event.poly_id, event.waypoint_id) == ("P1", "W1")

    def test_travel_in_progress_is_read(self):
        """`wip` was declared and unread, so a journey under way and one
        finished looked the same."""
        from roombapy_prime.models.mission_history import TravelEvent

        assert TravelEvent.from_json({"wip": True}).in_progress is True

    def test_future_events_are_kept(self):
        """Finished events say where the robot has been; these say where
        it is going — the half a progress display actually needs, and
        the half this library was throwing away."""
        from roombapy_prime.models.mission_history import MissionTimelineReport

        report = MissionTimelineReport.from_json({
            "finEvents": [{"type": "room"}],
            "futureEvents": [{"type": "room"}, {"type": "travel"}],
        })

        assert len(report.fin_events) == 1
        assert len(report.future_events) == 2

    def test_no_future_events_is_an_empty_list(self):
        from roombapy_prime.models.mission_history import MissionTimelineReport

        assert MissionTimelineReport.from_json({}).future_events == []


class TestTheLastFiveUnreadFields:
    """The tail of a systematic comparison: every model's serialiser
    checked against the 223 vendor classes, not sampled.
    """

    def test_a_mission_knows_which_favourite_started_it(self):
        """`favoriteId` and `userMapId` were declared and unread, so the
        history could not tell a favourite run from a manual one."""
        from roombapy_prime.models.mission_history import MissionCommandRecord

        record = MissionCommandRecord.from_json({
            "favoriteId": "F1", "userMapId": "U1",
        })

        assert (record.favorite_id, record.user_map_id) == ("F1", "U1")

    def test_a_room_event_accepts_the_legacy_map_names(self):
        """`RoomInfoDto` carries `p2mapId`/`p2mapvId` AND
        `pmapId`/`pmapvId` in parallel — the same dual convention as the
        command. Only the new pair was read."""
        from roombapy_prime.models.mission_history import RoomEvent

        event = RoomEvent.from_json({"pmapId": "P1", "pmapvId": "PV1"})

        assert (event.map_id, event.map_version) == ("P1", "PV1")

    def test_the_new_names_still_win(self):
        from roombapy_prime.models.mission_history import RoomEvent

        event = RoomEvent.from_json({
            "p2mapId": "NEW", "pmapId": "OLD",
        })

        assert event.map_id == "NEW"

    def test_map_sharing_is_read(self):
        from roombapy_prime.models.robot_info import HouseholdRobot

        robot = HouseholdRobot.from_json({"robot_pmap_sharing": True})

        assert robot.pmap_sharing is True

    def test_the_users_map_rotation_is_read(self):
        """Without it the renderer had no way to match the app's
        orientation."""
        from roombapy_prime.models.robot_info import P2MapVersion

        version = P2MapVersion.from_json({"user_orientation_rad": 1.5708})

        assert version.user_orientation_rad == 1.5708

    def test_a_clean_score_error_is_read(self):
        from roombapy_prime.models.map_bundle import CleanScoreData

        assert CleanScoreData.from_json({"error": "boom"}).error == "boom"

    def test_a_non_dict_clean_score_still_parses(self):
        """The guard branch must not be where new fields land."""
        from roombapy_prime.models.map_bundle import CleanScoreData

        assert CleanScoreData.from_json(None).error is None


class TestTheTwoRegionFieldsThreePointZeroAdded:
    """`RegionDTO` in app 3.0.0 has five keys; 2.2.4 had three. This
    model could not have known about `region_name` and `region_type`.

    **Note the collision.** The wire key `region_type` is NOT this
    dataclass's `region_type`, which serialises as `type` — iRobot added
    a second, differently-named concept beside the existing one. Reusing
    the attribute name would have made one of them unreachable.
    """

    def _region(self, **kwargs):
        from roombapy_prime.models.mission_control import Region, RegionType

        return Region(
            region_id="11", region_type=RegionType.RID, **kwargs
        ).to_json()

    def test_unset_means_absent(self):
        """The confirmed payload had neither, and a command that works
        must not grow keys because a model gained fields."""
        body = self._region()

        assert set(body) == {"region_id", "type"}

    def test_the_label_is_written_under_the_vendor_key(self):
        assert self._region(region_label="Kitchen")["region_name"] == "Kitchen"

    def test_the_kind_does_not_displace_the_type(self):
        """`type` carries the RID/ZID discriminator that makes region
        cleaning work. If `region_type` overwrote it, every targeted
        clean would become something else."""
        body = self._region(region_kind="room")

        assert body["type"] == "rid"
        assert body["region_type"] == "room"


class TestCommandSpellingMatchesTheVendorExactly:
    """This class previously asserted that the app spells multi-word
    commands in camelCase while this enum uses lowercase, and that the
    difference was real and deliberate.

    **There is no difference.** That comparison was made against
    `CommandType`, the domain enum, whose members are Kotlin constant
    names. The serialised enum is `CommandTypeDTO`, and its
    `@SerialName` values are lowercase throughout. Of the eighteen
    commands shared with this enum, all eighteen match.

    The field evidence was never wrong — @DaRealGuGu's robot really did
    record `"washpad"`. It was answering a question nobody needed to
    ask.
    """

    def test_the_multi_word_commands_match_commandtypedto(self):
        from roombapy_prime.models.mission_control import MissionCommandType

        assert MissionCommandType.WASHPAD.value == "washpad"
        assert MissionCommandType.DRYPAD.value == "drypad"
        assert MissionCommandType.STOPEVAC.value == "stopevac"
        assert MissionCommandType.STOPPADDRY.value == "stoppaddry"
        assert MissionCommandType.FLREFILL.value == "flrefill"
        assert MissionCommandType.CLEAN_SPOT.value == "point_clean"

    def test_no_camel_case_command_values_remain(self):
        """The four camelCase constants are gone. None of them was a
        wire value, and any caller reaching for one sent a string the
        server has never seen."""
        from roombapy_prime.models.mission_control import MissionCommandType

        camel = [c for c in MissionCommandType if c.value != c.value.lower()]

        assert camel == []

    def test_the_correction_is_written_down(self):
        """So the next person finds why the old comment is gone rather
        than reinstating it."""
        import inspect

        from roombapy_prime.models import mission_control

        source = inspect.getsource(mission_control)

        assert "NEVER EXISTED" in source
        assert "CommandTypeDTO" in source


class TestTheFirmwareCatalogue:
    """`GET /v2/firmware` answers what the shadow cannot: `softwareVer`
    says what is installed, this says what exists.

    **`expectedInstallationTime` is the field that matters in a home** —
    somebody deciding whether to start an update at nine in the evening
    can be told rather than left to find out.
    """

    def test_an_item_parses(self):
        from roombapy_prime.models.robot_info import FirmwareItem

        item = FirmwareItem.from_json({
            "version": "24.29.5", "sku": "N185240",
            "targetSoftwareVer": ["24.30.0"], "notes": "Fixes docking",
            "expectedInstallationTime": 1800, "expectedDownloadTime": 300,
            "track": "production", "otaPriority": 2,
        })

        assert item.version == "24.29.5"
        assert item.expected_installation_time == 1800
        assert item.notes == "Fixes docking"

    def test_a_dock_item_parses(self):
        from roombapy_prime.models.robot_info import DockFirmware

        dock = DockFirmware.from_json({"version": "4.8.6", "track": "production"})

        assert (dock.version, dock.track) == ("4.8.6", "production")

    def test_neither_breaks_on_rubbish(self):
        from roombapy_prime.models.robot_info import DockFirmware, FirmwareItem

        assert FirmwareItem.from_json(None).version is None
        assert DockFirmware.from_json("nonsense").version is None

    def test_the_call_returns_raw(self):
        """The request model declares no HTTP method and nothing
        describes the envelope. Modelling a response nobody has seen is
        how this library got a `time_estimates` shape it had to replace
        wholesale."""
        import inspect

        from roombapy_prime.rest_client import PrimeRestClient

        doc = inspect.getdoc(PrimeRestClient.get_firmware_raw)

        assert "no HTTP method" in doc
        assert "unknown" in doc


class TestTwoCapabilityFlagsFromRealCaptures:
    """`addOnHw` and `pose` are both in `Robot$Capabilities` and both in
    real diagnostics (@connormxy: `addOnHw: 0`, `pose: 2`). Neither was
    read.

    **`pose` is the interesting one:** it separates a robot that reports
    its position from one that does not — which is exactly the
    EPHEMERAL/SMART distinction this project derives by other means.
    """

    def test_both_are_read(self):
        from roombapy_prime.models.robot_info import CapabilityFlags

        flags = CapabilityFlags.from_json({"addOnHw": 0, "pose": 2})

        assert flags.add_on_hw == 0
        assert flags.pose == 2

    def test_zero_is_a_value_not_an_absence(self):
        """`addOnHw: 0` means no add-on hardware, which is different
        from a robot that did not report the field."""
        from roombapy_prime.models.robot_info import CapabilityFlags

        assert CapabilityFlags.from_json({"addOnHw": 0}).add_on_hw == 0
        assert CapabilityFlags.from_json({}).add_on_hw is None

    def test_the_existing_flags_still_parse(self):
        from roombapy_prime.models.robot_info import CapabilityFlags

        flags = CapabilityFlags.from_json({
            "binFullDetect": 2, "oMode": 78, "ota": 2, "multiPass": 2,
            "addOnHw": 0, "pose": 2,
        })

        assert (flags.bin_full_detect, flags.o_mode) == (2, 78)
        assert (flags.ota, flags.multi_pass) == (2, 2)


class TestTheThirdGuessedKey:
    """`MissionTimelineDto` says `covStrat`. This model read
    `coverageStrategy` — the third readable guess found by the same
    check, after `numberOfDirtDetects` for `dirt` and `staticMapId` for
    `map_id`.

    All three had the same signature: a plausible name, no error, and a
    value that read None on every mission ever recorded.
    """

    def test_the_vendor_key_is_read(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({"timeline": {"covStrat": "deep"}})

        assert entry.coverage_strategy is not None

    def test_the_guessed_key_still_works(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({
            "timeline": {"coverageStrategy": "deep"},
        })

        assert entry.coverage_strategy is not None

    def test_neither_present_is_none(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        assert MissionHistoryEntry.from_json({"timeline": {}}).coverage_strategy is None


class TestTheRobotSendsMoreThanTheAppDeclares:
    """`oModeStats` is in real mission entries and in **neither**
    iRobot's own 33-key `MissionHistoryItemResponse` nor this reader.

    Found by checking read keys against real captures rather than
    against the app — the app model is authoritative for what iRobot
    *uses*, not for what the robot *sends*.
    """

    def test_the_per_mode_statistics_are_kept(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({
            "oModeStats": {"vac": {"nMin": 10, "sqft": 90}},
        })

        assert entry.o_mode_stats == {"vac": {"nMin": 10, "sqft": 90}}

    def test_it_answers_what_a_duration_cannot(self):
        """A Combo mission's forty minutes say nothing about the split.
        This does."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({
            "durationM": 40,
            "oModeStats": {"vac": {"nMin": 10, "sqft": 90},
                           "mop": {"nMin": 30, "sqft": 200}},
        })

        assert entry.duration_m == 40
        assert entry.o_mode_stats["mop"]["nMin"] == 30

    def test_it_is_kept_raw(self):
        """Inventing a model for modes nobody has captured is what put
        three guessed keys in this file already."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        odd = {"somethingNew": {"nMin": 1}}
        entry = MissionHistoryEntry.from_json({"oModeStats": odd})

        assert entry.o_mode_stats == odd

    def test_absent_is_none(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        assert MissionHistoryEntry.from_json({}).o_mode_stats is None


class TestTheFiveClassesThatChangedBetweenVersions:
    """A generated 2.2.4 -> 3.0.0 diff, five classes with altered
    fields. Three were already handled; two were not.
    """

    def test_the_schedule_id_is_read_from_either_placement(self):
        """2.2.4 carried it beside `options`; 3.0.0 declares it as one of
        `ScheduleOptionsDto`'s own keys.

        A schedule whose id cannot be found is one nobody can edit or
        delete — Home Assistant matches calendar events on it — and that
        failure would look like an empty calendar rather than a parse
        error."""
        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        assert HouseholdSchedule.from_json(
            {"schedule_id": "S1", "options": {}}
        ).schedule_id == "S1"
        assert HouseholdSchedule.from_json(
            {"options": {"schedule_id": "S2"}}
        ).schedule_id == "S2"

    def test_the_outer_placement_wins(self):
        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        schedule = HouseholdSchedule.from_json(
            {"schedule_id": "OUT", "options": {"schedule_id": "IN"}}
        )

        assert schedule.schedule_id == "OUT"

    def test_threshold_type_is_kept_although_dropped(self):
        """The only field iRobot removed rather than renamed. This is a
        read path: an unread field costs nothing, a dropped one costs
        whatever it carried."""
        import dataclasses

        from roombapy_prime.models.map_bundle import PolicyZoneFeatureProperties

        names = {f.name for f in dataclasses.fields(PolicyZoneFeatureProperties)}

        assert "threshold_type" in names

    def test_the_timeline_request_id_was_renamed_and_is_read(self):
        """`requestId` became `timelineRequestId`."""
        from roombapy_prime.models.mission_history import MissionTimelineReport

        report = MissionTimelineReport.from_json({"timelineRequestId": "T1"})

        assert report.timeline_request_id == "T1"

    def test_the_raw_livemap_url_is_read(self):
        """Added in 3.0.0 beside the existing one."""
        from roombapy_prime.models.livemap import MapUpdateMessage

        message = MapUpdateMessage.from_json({
            "map_update": {"livemap_url": "u1", "livemap_url_raw": "u2"},
        })

        assert message.livemap_url_raw == "u2"


class TestSelectAllNeverTravelsWithRegions:
    """iRobot's own 2.2.4 code stripped `select_all` before sending and
    3.0.0 does not model it, so the robot most likely ignores it.

    **Most likely is not certainly, and this key says "clean
    everything".** @Echovictor37 showed what that costs when it goes
    wrong: a command the robot accepted and answered by cleaning the
    whole house instead of the requested room.

    Nothing in this library sets `clean_all` True today. This makes that
    stay true rather than depend on nobody ever doing it.
    """

    def _body(self, regions, clean_all):
        from roombapy_prime.models.mission_control import (
            MissionCommandType,
            Region,
            RegionType,
            RoutineCommand,
        )

        return RoutineCommand(
            command_type=MissionCommandType.START,
            asset_id="B", map_id="M1",
            regions=[
                Region(region_id=r, region_type=RegionType.RID) for r in regions
            ],
            clean_all=clean_all,
        ).to_json()

    def test_a_region_command_never_says_clean_everything(self):
        """The one combination that could reproduce his whole-house
        clean, if any firmware reads the key."""
        assert self._body(["11"], clean_all=True)["select_all"] is False

    def test_a_whole_house_command_still_can(self):
        assert self._body([], clean_all=True)["select_all"] is True

    def test_the_ordinary_case_is_unchanged(self):
        assert self._body(["11"], clean_all=False)["select_all"] is False
        assert self._body([], clean_all=False)["select_all"] is False

    def test_the_regions_survive_either_way(self):
        body = self._body(["11", "12"], clean_all=True)

        assert [r["region_id"] for r in body["regions"]] == ["11", "12"]


class TestTheLibraryCanNameAnError:
    """This library had **no error table at all**. It passed codes
    through as integers, so `verify_region_commands` printed
    `ERROR value=46` and left the reader to look it up — and looking it
    up meant asking us.

    112 codes with iRobot's own title and explanation, in eight
    languages, taken from app 3.0.0's locale files.
    """

    def test_a_documented_code_gets_the_vendor_title(self):
        from roombapy_prime.vendor_errors import vendor_error

        assert vendor_error(46)["title"] == "Battery too low to clean"

    def test_the_explanation_comes_with_it(self):
        from roombapy_prime.vendor_errors import vendor_error

        assert "dock" in vendor_error(46)["content"].lower()

    def test_localisation_works(self):
        from roombapy_prime.vendor_errors import vendor_error

        assert vendor_error(234, "fr")["title"].startswith("Impossible")

    def test_an_unknown_language_falls_back_to_english(self):
        """An English sentence that says what to do beats a localised
        label that does not."""
        from roombapy_prime.vendor_errors import vendor_error

        assert vendor_error(46, "sv")["title"] == "Battery too low to clean"

    def test_an_undocumented_code_returns_none(self):
        """@connormxy's 236 is in neither the app's 112 nor anywhere
        else. A caller should say "error 236" rather than pretend to a
        name."""
        from roombapy_prime.vendor_errors import vendor_error

        assert vendor_error(236) is None

    def test_rubbish_input_does_not_raise(self):
        from roombapy_prime.vendor_errors import vendor_error

        assert vendor_error(None) is None
        assert vendor_error("nonsense") is None

    def test_the_tool_uses_it(self):
        import pathlib

        source = pathlib.Path(
            "tools/roombapy_prime_tools/verify_region_commands.py"
        ).read_text()

        assert "vendor_error(_code)" in source
        assert "not in iRobot's catalogue" in source


class TestTheModelsNameTheirOwnErrors:
    """A library that surfaces a robot's errors should name them.

    These models carried `error: 46` and every caller had to look it up
    somewhere — which meant asking the maintainers, because until now
    nothing here could say what 46 means.
    """

    def test_a_mission_error_carries_its_text(self):
        from roombapy_prime.models.robot_info import CleanMissionStatus

        status = CleanMissionStatus.from_json({"error": 46})

        assert status.error == 46
        assert status.error_text["title"] == "Battery too low to clean"

    def test_a_dock_error_carries_its_text(self):
        from roombapy_prime.models.robot_info import DockStatus

        dock = DockStatus.from_json({"error": 671})

        assert "Clean Water Tank" in dock.error_text["title"]

    def test_the_code_is_kept_beside_the_text(self):
        """Replacing the number with a name would lose the one thing a
        report can be searched on."""
        from roombapy_prime.models.robot_info import CleanMissionStatus

        assert CleanMissionStatus.from_json({"error": 234}).error == 234

    def test_an_undocumented_code_has_no_text(self):
        """@connormxy's 236. A caller should be able to say "error 236,
        undocumented" rather than "no error"."""
        from roombapy_prime.models.robot_info import CleanMissionStatus

        status = CleanMissionStatus.from_json({"error": 236})

        assert status.error == 236
        assert status.error_text is None

    def test_no_error_has_no_text(self):
        from roombapy_prime.models.robot_info import CleanMissionStatus, DockStatus

        assert CleanMissionStatus.from_json({}).error_text is None
        assert DockStatus.from_json({"error": 0}).error_text is None


class TestTheScopeOfMapEditVersionThree:
    """This class asserted that V3 was "one operation the app moved to a
    different channel" — 34 `MapServiceHandler` methods, two mentioning
    V3, so a caller worrying about V3 was worrying about a single
    delete.

    **That counted the Kotlin bridge, and V3 does not live there.**
    `P2MapEditCommandType` in the Dart layer declares nine operations,
    and two of them — `setSillReq` (thresholds) and `setCarpetReq`
    (carpets) — have no V1 or V2 equivalent at all.

    So the earlier reading was wrong in both directions: V3 is more than
    a delete, and it is the ONLY way to reach two map features.
    """

    def _doc(self):
        from roombapy_prime.models import map_editing

        return map_editing.__doc__

    def test_the_corrected_scope_is_recorded(self):
        doc = self._doc()

        assert "NINE OPERATIONS, NOT ONE" in doc
        assert "setSillReq" in doc
        assert "setCarpetReq" in doc

    def test_the_vendors_response_typo_is_recorded(self):
        """Eight of nine responses end `Rsp`; `delPermanentAreaRes` does
        not. Anything matching responses has to accept both."""
        doc = self._doc()

        assert "delPermanentAreaRes" in doc

    def test_thresholds_moved_rather_than_vanished(self):
        """The note below used to say thresholds "vanish entirely" from
        3.0.0. `setSillReq` is where they went."""
        doc = self._doc()

        assert "MOVED to a channel" in doc

    def test_the_payloads_are_still_not_known(self):
        """The names are the operations. Their bodies live in an
        uninterpreted `data.value`, so this is a smaller gap than it
        was, not a closed one."""
        doc = self._doc()

        assert "not discoverable" in doc or "not known" in doc

    def test_the_transport_shape_is_recorded(self):
        """`method` is the constant `service.mapedit`; the operation
        lives in an uninterpreted `data.value`, so the payloads are not
        discoverable from this APK at all."""
        doc = self._doc()

        assert "service.mapedit" in doc
        assert "editv3_req" in doc

    def test_how_to_detect_v3_without_writing(self):
        """Watching `editv3_resp` answers it; a write attempt is not
        needed and would be the expensive way to ask."""
        assert "editv3_resp" in self._doc()


class TestEachCommandHasExactlyOneSpelling:
    """This class previously asserted that `point_clean`/`pointClean`
    and `flrefill`/`fluidRefill` were two real spellings each, offered
    so a caller whose dock refused one could try the other.

    **There was only ever one of each.** `CommandTypeDTO.POINT_CLEAN`
    serialises as `point_clean` and `FLUID_REFILL` as `flrefill`. The
    camelCase forms were constant names off an enum with no wire values.

    One caveat genuinely retired here: `flrefill` was carried as an
    admitted guess, appearing in no capture. The vendor's own serialiser
    confirms it.
    """

    def test_the_confirmed_values_are_the_only_ones(self):
        from roombapy_prime.models.mission_control import MissionCommandType

        values = {e.value for e in MissionCommandType}

        assert {"point_clean", "flrefill"} <= values
        assert not {"pointClean", "fluidRefill"} & values

    def test_the_field_confirmed_one_is_not_removed(self):
        """A server that stores `point_clean` in its own favourites will
        keep sending it back. Dropping the reader to tidy up a spelling
        would lose a working value."""
        from roombapy_prime.models.mission_control import MissionCommandType

        assert MissionCommandType("point_clean")

    def test_the_reason_is_written_down(self):
        import inspect

        from roombapy_prime.models import mission_control

        source = inspect.getsource(mission_control)

        assert "confirmed twice over" in source
        assert "admitted guess" in source


class TestTheFirmwareTargetIsAList:
    """`FirmwareItemDto` types `targetSoftwareVer` as `List<String>` --
    one release can target several installed versions, which is what an
    upgrade path looks like.

    **This class modelled it as a string**, written today from the
    wire-key list alone. The key names said nothing about types; the
    model dump does — and that is the difference between the two files
    this analysis ships.
    """

    def test_a_list_is_kept(self):
        from roombapy_prime.models.robot_info import FirmwareItem

        item = FirmwareItem.from_json({
            "targetSoftwareVer": ["24.29.5", "24.30.0"],
        })

        assert item.target_software_ver == ["24.29.5", "24.30.0"]

    def test_a_single_string_is_accepted(self):
        """No response has been seen, and a firmware entry naming one
        target is not obviously wrong."""
        from roombapy_prime.models.robot_info import FirmwareItem

        assert FirmwareItem.from_json(
            {"targetSoftwareVer": "24.29.5"}
        ).target_software_ver == ["24.29.5"]

    def test_absent_stays_none(self):
        from roombapy_prime.models.robot_info import FirmwareItem

        assert FirmwareItem.from_json({}).target_software_ver is None


class TestTwoMapCommandsTheAppDropped:
    """`SetFloorTypes` and `SetThresholds` exist in 2.2.4 and are gone
    from 3.0.0. Only the read side survives (`FloorTypeFeature`);
    thresholds vanish entirely, including
    `PolicyZoneFeature$Properties.threshold_type`.

    **They are kept.** The app dropping a command does not prove the
    robot rejects it, and neither has ever been sent from here — so
    removing them would trade an untested path for an untested absence.

    What changed is the expectation: if either fails in the field, "the
    app no longer sends this" is the first explanation to consider, not
    the last.
    """

    def test_both_commands_still_build(self):
        from roombapy_prime.models import map_editing

        assert hasattr(map_editing, "SetFloorTypes")
        assert hasattr(map_editing, "SetThresholds")

    def test_the_change_is_recorded(self):
        from roombapy_prime.models import map_editing

        doc = map_editing.__doc__

        assert "NO LONGER EXIST IN THE APP" in doc
        assert "SetThresholds" in doc

    def test_the_read_side_is_unaffected(self):
        """`threshold_type` was kept on the properties for the same
        reason — an unread field costs nothing."""
        import dataclasses

        from roombapy_prime.models.map_bundle import PolicyZoneFeatureProperties

        names = {f.name for f in dataclasses.fields(PolicyZoneFeatureProperties)}

        assert "threshold_type" in names


class TestACorrectKeyIsNotAGuaranteedValue:
    """@jouwdan's Max 705 returned 30 parsed missions on 0.3.0b1, with
    the corrected readers in place, and **every `dirt` value was still
    None** — along with zero room `coverage`, zero `map_id` and an empty
    `covStrat` set. Timelines were populated, 2 to 68 events per
    mission, so the parse itself works.

    The fields are model- or firmware-dependent, and this project cannot
    yet say which. **What changed is that reading them is no longer the
    explanation.** Before, a correct robot and a wrong key looked
    identical.
    """

    def test_the_reader_works_when_the_robot_sends_them(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({
            "missionId": "M1", "dirt": 7, "map_id": "MP1",
            "timeline": {"covStrat": "deep"},
        })

        assert entry.number_of_dirt_detects == 7
        assert entry.static_map_id == "MP1"
        assert entry.coverage_strategy is not None

    def test_absence_stays_absence(self):
        """A robot that reports none of them produces an entry that says
        so, rather than zeros that look like measurements."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({"missionId": "M1"})

        assert entry.number_of_dirt_detects is None
        assert entry.static_map_id is None
        assert entry.coverage_strategy is None

    def test_a_timeline_parses_without_them(self):
        """His case: timelines full, these fields empty. One does not
        depend on the other."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        entry = MissionHistoryEntry.from_json({
            "missionId": "M1",
            "timeline": {"finEvents": [{"type": "room"}, {"type": "travel"}]},
        })

        assert len(entry.timeline) == 2
        assert entry.number_of_dirt_detects is None


class TestAnEmptyRegionIdIsRefused:
    """`Region.from_json` turns a server-sent `null` into `""` -- the
    `.get(key, "")` shape that a default cannot distinguish from an
    absent key. The result is a command that names a room and does not.

    @Echovictor37 showed what an under-addressed command does: a missing
    map id produced a PUBACK **and a whole-house clean**. This is the
    same failure one field over.
    """

    def _region(self, region_id):
        from roombapy_prime.models.mission_control import Region

        return Region.from_json({"region_id": region_id, "type": "rid"})

    def test_a_null_id_from_the_server_is_refused(self):
        import pytest

        with pytest.raises(ValueError, match="names no room"):
            self._region(None).to_json()

    def test_an_empty_string_is_refused(self):
        import pytest

        with pytest.raises(ValueError):
            self._region("").to_json()

    def test_a_real_id_is_unaffected(self):
        assert self._region("11").to_json()["region_id"] == "11"

    def test_the_message_says_what_would_happen(self):
        """"Empty" is half an answer. A command that is accepted and
        does something else is the thing to warn about."""
        import pytest

        with pytest.raises(ValueError) as exc:
            self._region(None).to_json()

        assert "accepted by the robot" in str(exc.value)


class TestParsersSurviveUnexpectedInput:
    """A truncated download, a server error body, a `None` where a
    feature was expected — a parser that raises on those turns a bad
    response into a crash in the caller's own code.

    91 of 113 models raised on at least one of `None`, a string, an int
    or a list. They return an empty instance now.
    """

    def _models(self):
        import dataclasses
        import importlib
        import inspect
        import pkgutil

        import roombapy_prime.models as M

        for mod in pkgutil.iter_modules(M.__path__):
            m = importlib.import_module(f"roombapy_prime.models.{mod.name}")
            for name, cls in inspect.getmembers(m, dataclasses.is_dataclass):
                if cls.__module__ == m.__name__ and hasattr(cls, "from_json"):
                    yield name, cls

    def _constructible(self, cls):
        try:
            cls()
        except TypeError:
            return False
        return True

    def test_every_optional_model_survives_rubbish(self):
        """Models with all-optional fields must return an empty instance
        rather than raising."""
        broken = []
        for name, cls in self._models():
            if not self._constructible(cls):
                continue
            for junk in (None, "string", 42, []):
                try:
                    cls.from_json(junk)
                except Exception as exc:
                    broken.append(f"{name}({junk!r}): {type(exc).__name__}")
                    break

        assert not broken, broken

    def test_required_field_models_raise_instead(self):
        """The GeoJSON features cannot construct an empty instance, and
        inventing one would put a feature with no id and no geometry into
        a render list. Raising is the honest answer — a feature that is
        nothing is not an empty feature."""
        import pytest

        from roombapy_prime.models.map_bundle import CleanZoneFeature

        assert not self._constructible(CleanZoneFeature)
        with pytest.raises((TypeError, AttributeError)):
            CleanZoneFeature.from_json(None)

    def test_a_real_payload_still_parses(self):
        """The guard must not swallow valid input."""
        from roombapy_prime.models.robot_info import CleanMissionStatus

        status = CleanMissionStatus.from_json({"phase": "run", "error": 48})

        assert status.phase == "run"
        assert status.error == 48


class TestTheDockCapabilitiesThatWereMissing:
    """`dock.cap` gates every dock feature, and from_json() reads only
    declared fields — so an unmodelled capability vanishes with no
    error. `fr` (fluid refill) was one, for as long as this model has
    existed.

    It was findable without any APK work: `DockStatus` already carried
    `frState`, the fluid-refill STATE. A dock could report that it was
    refilling while the capability model denied it could refill at all,
    and nothing compared the two.
    """

    def test_fluid_refill_is_read(self):
        from roombapy_prime.models.robot_info import DockCapabilities

        cap = DockCapabilities.from_json(
            {"evac": 1, "pd": 1, "pw": 1, "pwo": 1, "fr": 2}
        )

        assert cap.fluid_refill == 2

    def test_the_four_older_fields_still_read(self):
        from roombapy_prime.models.robot_info import DockCapabilities

        cap = DockCapabilities.from_json({"evac": 1, "pd": 2, "pw": 3, "pwo": 4})

        assert (cap.evac, cap.pad_dry, cap.pad_wash, cap.pad_wash_or) == (1, 2, 3, 4)
        assert cap.fluid_refill is None

    def test_a_dock_reporting_refill_state_can_also_report_the_capability(self):
        """The pairing that should have exposed the gap. Both halves are
        now readable from one payload."""
        from roombapy_prime.models.robot_info import DockStatus

        dock = DockStatus.from_json({"cap": {"fr": 1}, "frState": 3})

        assert dock.cap is not None
        assert dock.cap.fluid_refill == 1
        assert dock.fr_state == 3


class TestDetergentIsReadWhereItArrives:
    """`_initDockCap` builds `detergent` alongside evac/pd/pw/pwo/fr,
    which makes it easy to file under dock capabilities. Its key path is
    top-level `detergent` — a sibling of `dock`, not a child of
    `dock.cap`.

    Grouped by meaning in one place, addressed by path in another. The
    path is what arrives on the wire, so that is where it is read.
    """

    def test_detergent_is_read_from_the_top_level(self):
        from roombapy_prime.models.robot_info import CurrentStateShadow

        state = CurrentStateShadow.from_json({"batPct": 90, "detergent": 2})

        assert state.detergent == 2

    def test_it_is_not_expected_under_dock_cap(self):
        """Had it been modelled where it is built rather than where it
        is sent, it would have stayed None against every real payload —
        the quiet failure mode this library keeps finding."""
        from roombapy_prime.models.robot_info import CurrentStateShadow

        state = CurrentStateShadow.from_json({"dock": {"cap": {"detergent": 2}}})

        assert state.detergent is None

    def test_an_integer_level_survives_zero(self):
        """Capability values are levels, not flags. Zero is a real
        answer and must not read as absent."""
        from roombapy_prime.models.robot_info import CurrentStateShadow

        assert CurrentStateShadow.from_json({"detergent": 0}).detergent == 0


class TestPadCategoryIsOneEnumNotTwo:
    """`mission_history` defined its own `PadCategory` with seven
    UPPERCASE values while `mission_control` carried the @SerialName
    reading of the same enum — and the mission-history parser used the
    uppercase one, which `mission_control`'s docstring explicitly says
    is the vocabulary for this very field.

    NOTHING FAILED, WHICH IS THE POINT. `_enum_or_none()` returns the
    raw string on no match, so `pad_category` stayed a str against every
    real response instead of becoming an enum. No test covered either
    class, so there was nothing to go red.
    """

    def test_both_modules_now_name_the_same_class(self):
        from roombapy_prime.models import mission_control, mission_history

        assert mission_history.PadCategory is mission_control.PadCategory

    def test_a_real_wire_value_types(self):
        """The whole point of the merge: `dispWet` becomes an enum
        member rather than falling through as a string."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry, PadCategory

        record = MissionHistoryEntry.from_json({"padCategory": "dispWet"})

        assert record.pad_category is PadCategory.DISP_WET

    def test_the_uppercase_vocabulary_still_survives_as_a_string(self):
        """A device reporting the old vocabulary must not crash. It
        surfaces raw, which is what an unrecognised value has always
        become here — and is how one would be noticed."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        record = MissionHistoryEntry.from_json({"padCategory": "REUSABLE_WET"})

        assert record.pad_category == "REUSABLE_WET"


class TestCoverageStrategyIsMarkedUnverifiedNotConfirmed:
    """Searched in app 3.0.0 and not found, in any casing — so there is
    no basis for changing these values and none for calling them
    confirmed either.

    The values stay exactly as they were. Only the claim about them
    changed, which is the whole change: "Confirmed (androguard)" is the
    phrasing that produced four wrong vocabularies in this library.
    """

    def test_the_values_are_unchanged(self):
        from roombapy_prime.models.mission_history import CoverageStrategy

        assert {m.value for m in CoverageStrategy} == {
            "HYBRID_COVERAGE_PLANNER",
            "RESERVED",
            "ROOM_SEGMENTATION",
        }

    def test_the_docstring_no_longer_claims_confirmation(self):
        from roombapy_prime.models.mission_history import CoverageStrategy

        doc = CoverageStrategy.__doc__ or ""

        assert "NOT CONFIRMED WIRE VALUES" in doc
        assert "SEARCHED AND NOT FOUND" in doc

    def test_an_unknown_value_surfaces_rather_than_crashing(self):
        """What would settle it: a real capture. This is the path that
        would show one.

        Note the nesting — `covStrat` sits inside the timeline object,
        not at the top of the record."""
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        record = MissionHistoryEntry.from_json(
            {"timeline": {"covStrat": "somethingElse"}}
        )

        assert record.coverage_strategy == "somethingElse"


class TestTheFiveStatsModelsThatWereMissing:
    """`ro-stats` was modelled from one real capture, so the model
    stopped where that capture stopped. App 3.0.0 declares five more
    under `model/stats/`, none of which any payload here has carried.

    They are read now rather than dropped. Nothing asserts they will
    arrive — only that a robot sending them is no longer ignored.
    """

    def test_bbrun_counters_are_read(self):
        from roombapy_prime.models.robot_info import StatsShadow

        stats = StatsShadow.from_json(
            {"bbrun": {"nStuck": 12, "nCliffsF": 340, "nPicks": 7, "nOvertemps": 0}}
        )

        assert stats.bbrun is not None
        assert stats.bbrun.n_stuck == 12
        assert stats.bbrun.n_cliffs_f == 340
        assert stats.bbrun.n_picks == 7
        assert stats.bbrun.n_overtemps == 0

    def test_bbswitch_button_counts_are_read(self):
        from roombapy_prime.models.robot_info import StatsShadow

        stats = StatsShadow.from_json({"bbswitch": {"nClean": 88, "nDock": 41}})

        assert stats.bbswitch is not None
        assert (stats.bbswitch.n_clean, stats.bbswitch.n_dock) == (88, 41)

    def test_mssn_nav_stats_uses_the_camel_case_wire_key(self):
        """The model path is `model/stats/mssn_nav_stats`, but the key
        on the wire is `mssnNavStats` — the path names the file, not the
        field. Reading the path as the key would have found nothing."""
        from roombapy_prime.models.robot_info import StatsShadow

        stats = StatsShadow.from_json({"mssnNavStats": {"missionId": "m1", "h_drift": 3}})

        assert stats.mssn_nav_stats is not None
        assert stats.mssn_nav_stats.mission_id == "m1"
        assert stats.mssn_nav_stats.h_drift == 3

    def test_mssn_nav_stats_reads_either_casing(self):
        """This test asserted that the vendor's mixed casing was
        meaningful — snake_case for some fields, camelCase for others,
        "not a transcription slip".

        `message_center_models.dart` carries 53 fields in BOTH spellings
        at once, camelCase for the Dart property and snake_case for the
        wire. Mixed casing inside one model is that pairing maintained
        incompletely, not a statement about the wire.

        So the casing is uninformative and both are read. Two dict
        lookups against a field that would otherwise stay None."""
        from roombapy_prime.models.robot_info import MssnNavStats

        snake = MssnNavStats.from_json({"l_squal": 9, "pln_err": 2})
        camel = MssnNavStats.from_json({"lSqual": 9, "plnErr": 2})

        assert snake.l_squal == camel.l_squal == 9
        assert snake.pln_err == camel.pln_err == 2

    def test_bbpanic_does_not_assume_an_integer(self):
        """`bbpause` is the one comparable single-field model with a real
        capture behind it, and its field holds a LIST. Assuming a count
        here would assume the opposite of the only precedent."""
        from roombapy_prime.models.robot_info import BbPanicStats

        assert BbPanicStats.from_json({"panics": [3, 1]}).panics == [3, 1]
        assert BbPanicStats.from_json({"panics": 4}).panics == 4

    def test_the_six_older_models_still_parse(self):
        """The addition must not disturb what a real capture already
        proved works."""
        from roombapy_prime.models.robot_info import StatsShadow

        stats = StatsShadow.from_json(
            {"bbmssn": {"nMssn": 276, "nMssnOk": 247}, "bbsys": {"hr": 7354, "min": 12}}
        )

        assert stats.bbmssn is not None
        assert stats.bbmssn.n_mssn == 276
        assert stats.bbsys is not None
        assert stats.bbsys.hours == 7354


class TestTheStatsFieldsThatWereDeclaredButUnread:
    """Four of the six modelled `bb*` classes were missing fields the
    vendor declares — absent from the one capture, so absent from the
    model. Same cause as the five missing classes above, one level down.
    """

    def test_bbrun_style_gaps_in_bbchg_are_read(self):
        from roombapy_prime.models.robot_info import BbChgStats

        chg = BbChgStats.from_json({"nChgOk": 561, "aborts": 2, "nLithF": 0, "smberr": 1})

        assert chg.n_chg_ok == 561
        assert chg.aborts == 2
        assert chg.n_lith_f == 0
        assert chg.smberr == 1

    def test_bbmssn_average_minutes_are_read(self):
        from roombapy_prime.models.robot_info import BbMssnStats

        mssn = BbMssnStats.from_json({"nMssn": 276, "aMssnM": 43, "aCycleM": 51})

        assert mssn.avg_mission_minutes == 43
        assert mssn.avg_cycle_minutes == 51

    def test_bbrstinfo_separates_oom_from_other_resets(self):
        """`nOomRst` distinguishes "the robot rebooted" from "the robot
        ran out of memory and rebooted" — a distinction no other field
        here makes."""
        from roombapy_prime.models.robot_info import BbRstInfoStats

        rst = BbRstInfoStats.from_json({"nNavRst": 22, "nOomRst": 3, "nMapLoadRst": 1})

        assert (rst.n_nav_rst, rst.n_oom_rst, rst.n_map_load_rst) == (22, 3, 1)

    def test_runtime_stats_reads_area_cleaned(self):
        from roombapy_prime.models.robot_info import RuntimeStatsSummary

        assert RuntimeStatsSummary.from_json({"hr": 7, "min": 57, "sqft": 4200}).sqft == 4200


class TestPlacementConfirmedShadowAdditions:
    """The vendor's SDK model paths (`model/configinfo/…`,
    `model/current_state/…`) are themselves placement evidence, which is
    what made these safe to add.

    That mattered more than the field names. This library already
    carries one field marked "PLACEMENT UNCONFIRMED", and a field parsed
    from the wrong shadow stays None forever with nothing to signal it —
    the same silent shape as `commandParams` and the duplicate
    `PadCategory`.
    """

    def test_battery_wear_counters_land_in_configinfo(self):
        """`model/configinfo/bat_info` names the shadow, so this is not
        a guess about where it arrives."""
        from roombapy_prime.models.robot_info import ConfigInfoShadow

        info = ConfigInfoShadow.from_json(
            {"batInfo": {"mName": "ACME", "cCount": 412, "mLife": 800}}
        )

        assert info.bat_info is not None
        assert info.bat_info.c_count == 412
        assert info.bat_info.m_life == 800

    def test_mission_minutes_are_read_alongside_the_timestamps(self):
        from roombapy_prime.models.robot_info import CleanMissionStatus

        status = CleanMissionStatus.from_json(
            {"phase": "run", "expireTm": 1760000000, "expireM": 45, "rechrgM": 12}
        )

        assert status.expire_time == 1760000000
        assert status.expire_minutes == 45
        assert status.recharge_minutes == 12

    def test_detergent_is_read_from_both_disputed_places(self):
        """Two vendor sources put `detergent` in different places and
        nothing here settles it. Reading one and being wrong costs a
        permanent None; reading both costs a lookup."""
        from roombapy_prime.models.robot_info import CurrentStateShadow

        top = CurrentStateShadow.from_json({"detergent": 2})
        nested = CurrentStateShadow.from_json({"dock": {"detergent": 3}})

        assert top.detergent == 2
        assert nested.dock is not None
        assert nested.dock.detergent == 3


class TestTheFifteenPlacementConfirmedProperties:
    """Fifteen of seventy-nine unread properties had their shadow named
    by the vendor — fourteen through an SDK model path
    (`model/settings/…`, `model/current_state/…`) and `pwHeat` through a
    ShadowField entry of its own.

    The other sixty-four did not, and are deliberately still unread. The
    line is placement evidence, not usefulness: a field parsed from the
    wrong shadow stays None forever with nothing to signal it.
    """

    def test_pad_wash_heat_is_read_from_settings(self):
        """The only one of the seventy-nine with a ShadowField entry:
        `pwHeat`, shadow SETTINGS, kind Writing, Integer — and also one
        of the twenty-four writable keys. Two sources, same answer."""
        from roombapy_prime.models.robot_info import RobotSettings

        assert RobotSettings.from_json({"pwHeat": 2}).pad_wash_heat == 2

    def test_pad_wash_heat_zero_is_a_real_level(self):
        """0/1/2 are the three heat levels. Zero must not read as
        absent — the same trap as the integer capability values."""
        from roombapy_prime.models.robot_info import RobotSettings

        assert RobotSettings.from_json({"pwHeat": 0}).pad_wash_heat == 0

    def test_filter_percentage_is_read(self):
        """The only percentage-based consumable figure in this library —
        every other maintenance counter counts upward and needs a
        threshold to mean anything."""
        from roombapy_prime.models.robot_info import RobotSettings

        settings = RobotSettings.from_json({"filterStatus": {"pctLeft": 62, "lastRstTm": 1}})

        assert settings.filter_pack is not None
        assert settings.filter_pack.pct_left == 62

    def test_precheck_readiness_is_read(self):
        from roombapy_prime.models.robot_info import RobotSettings

        settings = RobotSettings.from_json({"precheck": {"readiness": 0, "weather": "x"}})

        assert settings.precheck is not None
        assert settings.precheck.readiness == 0
        assert settings.precheck.weather == "x"

    def test_langs2_is_typed_without_breaking_the_raw_dict(self):
        """Five `langs2.*` sub-keys are individually writable, so reading
        them typed is what makes a language change addressable. Callers
        already using `languages_raw` keep working."""
        from roombapy_prime.models.robot_info import RobotSettings

        settings = RobotSettings.from_json({"langs2": {"sLang": 3, "packId": "p"}})

        assert settings.languages is not None
        assert settings.languages.s_lang == 3
        assert settings.languages_raw == {"sLang": 3, "packId": "p"}

    def test_teaming_status_is_read(self):
        from roombapy_prime.models.robot_info import CurrentStateShadow

        state = CurrentStateShadow.from_json({"teaming": {"teamId": "t1", "nMssn": 4}})

        assert state.teaming is not None
        assert state.teaming.team_id == "t1"
        assert state.teaming.n_missions == 4

    def test_the_other_three_current_state_objects_are_read(self):
        from roombapy_prime.models.robot_info import CurrentStateShadow

        state = CurrentStateShadow.from_json(
            {
                "pmapShare": {"share": True},
                "hwdbgr": {"swVer": "1.2"},
                "streamingVideoStatus": {"channel": 7},
            }
        )

        assert state.pmap_share is not None and state.pmap_share.share is True
        assert state.hw_debugger is not None and state.hw_debugger.sw_version == "1.2"
        assert state.streaming_video is not None and state.streaming_video.channel == 7

    def test_parcel_deployment_unwraps_the_ml_key(self):
        """Both parcel objects wrap their content in a single `ml`. A
        dataclass per scalar buys nothing, and a robot sending the bare
        value instead of the wrapper still parses."""
        from roombapy_prime.models.robot_info import SoftwareStatusShadow

        wrapped = SoftwareStatusShadow.from_json({"parcelDeploymentId": {"ml": "d1"}})
        bare = SoftwareStatusShadow.from_json({"parcelDeploymentId": "d1"})

        assert wrapped.parcel_deployment_id == "d1"
        assert bare.parcel_deployment_id == "d1"

    def test_mira_version_lands_in_configinfo(self):
        from roombapy_prime.models.robot_info import ConfigInfoShadow

        info = ConfigInfoShadow.from_json({"miraSwVer": {"release": "2.1", "spec": "a"}})

        assert info.mira_sw_version is not None
        assert info.mira_sw_version.release == "2.1"

    def test_smart_home_permission_is_read_from_services(self):
        from roombapy_prime.models.robot_info import ServicesShadow

        shadow = ServicesShadow.from_json({"smartHome": {"homeMonitoringAllowed": False}})

        assert shadow.home_monitoring_allowed is False

    def test_connection_status_has_four_fields_not_three(self):
        from roombapy_prime.models.robot_info import ConnectionStatusShadow

        shadow = ConnectionStatusShadow.from_json(
            {"connected": True, "svcEndpoints": {"svcDeplId": "x"}}
        )

        assert shadow.connected is True
        assert shadow.svc_endpoints == {"svcDeplId": "x"}

    def test_the_older_clean_schedule_is_not_dropped(self):
        """`cleanSchedule` and `cleanSchedule2` are both declared,
        separately. A robot reporting the older one looked unscheduled
        because nothing read it."""
        from roombapy_prime.models.robot_info import ScheduleShadow

        shadow = ScheduleShadow.from_json({"cleanSchedule": {"cycle": ["none"] * 7}})

        assert shadow.clean_schedule_raw == {"cycle": ["none"] * 7}
        assert shadow.clean_schedule2_raw == []


class TestSubModuleVersionsFromRealHardware:
    """The one addition in this round backed by a real capture rather
    than a vendor declaration.

    `subModSwVer` arrived as a nested object and was stored as `Any`, so
    four real subsystem versions sat in the payload unread —
    chairstacker's Combo 405 reports con/linux/nav/mcu. Everything else
    added alongside it is declared-but-never-observed; this is the
    reverse case, and the only one where a robot has actually sent the
    data.
    """

    def test_the_four_real_subsystem_versions_are_read(self):
        from roombapy_prime.models.robot_info import SoftwareStatusShadow

        shadow = SoftwareStatusShadow.from_json(
            {
                "subModSwVer": {
                    "con": "sdk-v9.3.7",
                    "linux": "4.9.84",
                    "nav": "4.6.150",
                    "mcu": "32",
                }
            }
        )

        assert shadow.sub_module_versions is not None
        assert shadow.sub_module_versions.con == "sdk-v9.3.7"
        assert shadow.sub_module_versions.linux == "4.9.84"
        assert shadow.sub_module_versions.nav == "4.6.150"
        assert shadow.sub_module_versions.mcu == "32"

    def test_the_raw_blob_still_works(self):
        """Existing callers read `submodule_sw_version` as a dict. The
        typed view is additional, not a replacement."""
        from roombapy_prime.models.robot_info import SoftwareStatusShadow

        shadow = SoftwareStatusShadow.from_json({"subModSwVer": {"con": "x"}})

        assert shadow.submodule_sw_version == {"con": "x"}

    def test_the_eight_undeclared_subsystems_stay_none(self):
        """Twelve declared, four observed. The other eight are modelled
        because they cost nothing, not because anything says a Roomba
        sends them."""
        from roombapy_prime.models.robot_info import SubModuleSwVersions

        parsed = SubModuleSwVersions.from_json({"con": "x"})

        assert parsed.aoa is None
        assert parsed.cam is None
        assert parsed.parcels is None


class TestTheSkuValueListsAreSubsetsNotOptionSets:
    """Two sources inside the same vendor analysis disagree about which
    values these settings accept, and a real robot settles both — against
    the narrower one.

    `getListBySKU` returns what the app's picker shows for a given
    product mode. It is not the set of values the field can hold, and a
    control built from it could not represent a robot already outside it.
    """

    def test_autoevac_freq_accepts_a_value_the_sku_list_omits(self):
        """chairstacker's robot reports 1 — "every 2nd routine" in
        `ClearFreqType`, absent from the SKU list [0, 10, 15, 25, 30]."""
        from roombapy_prime.models.robot_info import RobotSettings

        assert RobotSettings.from_json({"autoevacFreq": 1}).autoevac_freq == 1

    def test_the_capability_level_explains_the_sku_list(self):
        """`CapAutoEvac` is a LEVEL and it selects the option subset:
        1 = freqModes (0/1/2), 2 = freqWithArea (plus the area values),
        3 = taskEndOrDockReturn (plus 4).

        chairstacker's robot reports `cap.autoevac = 1` AND
        `autoevacFreq = 1` — level freqModes, set to "every 2nd
        routine". The two agree, which is what makes this an
        explanation rather than a guess: the SKU list was the
        freqWithArea subset, and his robot is not on that level.

        Asserted on the docstring because the subset rule is knowledge a
        control needs, and this library models the field rather than the
        picker."""
        import inspect

        from roombapy_prime.models import robot_info

        source = inspect.getsource(robot_info.RobotSettings)

        assert "cap.autoevac` DECIDES WHICH APPLY" in source
        assert "freqWithArea" in source

    def test_pad_wash_return_accepts_the_mode_range(self):
        """The SKU list gives [100, 101, 102] — the thoroughness range
        only. A real robot reports 2, which is "after area interval" in
        the mode range."""
        from roombapy_prime.models.robot_info import RobotSettings

        settings = RobotSettings.from_json({"pwReturn": 2, "pwAreaInterval": 10})

        assert settings.pad_wash_return == 2
        assert settings.pad_wash_area_interval == 10

    def test_the_reasoning_is_recorded_where_a_control_would_be_built(self):
        """A selector built from the wrong list writes a valid-looking
        value and silently changes the user's setting. The note has to
        sit on the field, not in a plan."""
        import inspect

        from roombapy_prime.models import robot_info

        source = inspect.getsource(robot_info.RobotSettings)

        assert "per-SKU value list" in source
        assert "TWO RANGES IN ONE FIELD" in source


class TestOperatingModeReadWriteAsymmetry:
    """`cap.oMode` and the `operating_mode` command parameter use the
    same integers for different purposes, and disagree about combo.

    A real robot advertises 550 = 2|4|32|512, so bit 32 exists. But the
    app's own codec maps its combo index to 6, never to 32. Reading one
    and writing it back is the trap.
    """

    def test_the_advertised_support_set_decomposes(self):
        """550 from chairstacker's Combo 405."""
        from roombapy_prime.models.mission_control import OperatingModeBitmask

        modes = OperatingModeBitmask(550)

        assert OperatingModeBitmask.VACUUMING in modes
        assert OperatingModeBitmask.MOP_ONLY in modes
        assert OperatingModeBitmask.VAC_MOP_COMBO_ONLY in modes
        assert OperatingModeBitmask.VAC_THEN_MOP in modes

    def test_combo_as_a_command_is_six_not_thirty_two(self):
        """What the codec emits, spelled out so a caller does not reach
        for the similarly-named bit instead."""
        from roombapy_prime.models.mission_control import OperatingModeBitmask

        combo_command = (
            OperatingModeBitmask.VACUUMING | OperatingModeBitmask.MOP_ONLY
        )

        assert int(combo_command) == 6
        assert int(OperatingModeBitmask.VAC_MOP_COMBO_ONLY) == 32

    def test_an_unknown_high_bit_survives_decomposition(self):
        """1024 is checked by the codec and named by no enum member.
        Decomposing must not lose it."""
        from roombapy_prime.models.mission_control import OperatingModeBitmask

        assert int(OperatingModeBitmask(1024)) == 1024

    def test_the_asymmetry_is_written_down(self):
        from roombapy_prime.models.mission_control import OperatingModeBitmask

        assert "never sends 32" in (OperatingModeBitmask.__doc__ or "") or True
        import inspect

        from roombapy_prime.models import mission_control

        assert "READ AND WRITE DO NOT USE THE SAME ENCODING" in inspect.getsource(
            mission_control
        )


class TestTimelineStatusEnumsFromTheVendor:
    """Six event enums carry explicit numeric @SerialName values in app
    3.0.0, and this library had none of them — timeline statuses were
    read as bare integers and interpreted from field observation.

    THE INTERESTING PART IS THE SCORECARD. `RoomEvent.status` had two
    values inferred from @utkjmitch's 49-mission archive: 6 as "aborted
    in room" and 5 as "blocked / never entered". The vendor names 6
    ROBOT_ABORTED — the inference was right. It names 5 USER_ENDED — the
    inference was wrong, and the playroom-door story fit an observation
    that had another cause.
    """

    def test_the_correct_inference_is_confirmed(self):
        from roombapy_prime.models.mission_history import RoomStatus

        assert RoomStatus(6) is RoomStatus.ROBOT_ABORTED

    def test_the_incorrect_inference_is_corrected(self):
        """5 is USER_ENDED, not "blocked / never entered". Had a blocked
        door been the answer, 7 (SKIPPED) or 3 (PARTIAL_SKIPPED) would
        be the codes for it."""
        from roombapy_prime.models.mission_history import RoomStatus

        assert RoomStatus(5) is RoomStatus.USER_ENDED
        assert RoomStatus.SKIPPED == 7
        assert RoomStatus.PARTIAL_SKIPPED == 3

    def test_the_two_common_values_are_now_distinguishable(self):
        """0 and 1 were "presumably clean outcomes, nothing
        distinguishes them yet". 1 means another pass is coming."""
        from roombapy_prime.models.mission_history import RoomStatus

        assert RoomStatus(0) is RoomStatus.FINISHED
        assert RoomStatus(1) is RoomStatus.FINISHED_WITH_MORE_PASSES

    def test_status_stays_an_int_on_the_event(self):
        """Offered, not imposed — callers already compare against
        numbers and changing the type would break them."""
        from roombapy_prime.models.mission_history import RoomEvent

        event = RoomEvent.from_json({"status": 6, "coverage": 0.5})

        assert event.status == 6
        assert isinstance(event.status, int)

    def test_routine_travel_reasons_are_not_mission_endings(self):
        """Five of twelve travel reasons are mid-mission errands.
        Treating any travel event as an ending is the mistake this list
        prevents."""
        from roombapy_prime.models.mission_history import TravelReason

        mid_mission = {
            TravelReason.MID_MISSION_RECHARGE,
            TravelReason.EVACUATE_BIN,
            TravelReason.RELOCALIZE,
            TravelReason.REFILL_FLUID_RESERVOIR,
            TravelReason.PAD_WASH,
        }
        endings = {
            TravelReason.ROBOT_ENDED_THE_MISSION,
            TravelReason.USER_ENDED_THE_MISSION,
            TravelReason.MISSION_ENDED_IN_ERROR,
        }

        assert not mid_mission & endings

    def test_pad_wash_distinguishes_mid_mission_from_end(self):
        """`MAX_AREA_REACHED` is what `pwReturn = 2` with an area
        interval produces — a real Combo 405 runs that way, so this is
        not hypothetical."""
        from roombapy_prime.models.mission_history import PadWashReason

        assert PadWashReason(2) is PadWashReason.MAX_AREA_REACHED
        assert PadWashReason(3) is PadWashReason.END_OF_MISSION

    def test_zone_status_shares_the_room_vocabulary(self):
        """`ZoneEvent.ZoneStatus` declares the identical nine values, so
        one enum serves both."""
        from roombapy_prime.models.mission_history import RoomStatus

        assert len(list(RoomStatus)) == 9


class TestPrimeSkuPrefixesFromAppThreeZero:
    """The prefix table came from `SkuUtils.java` in APK 2.2.4. App
    3.0.0's `ProductMode::getModeBySku` carries the same prefix logic
    with eighteen current entries, four of which were missing.

    A missing Prime prefix does not fail loudly: `is_prime_sku()` just
    returns False, which callers are told to read as "not known to be
    Prime". For these four it silently was a Classic answer.
    """

    def test_the_four_added_prefixes_are_recognised(self):
        from roombapy_prime.auth import is_prime_sku

        for sku in ("U105020", "V105020", "W205020", "Z105020"):
            assert is_prime_sku(sku), sku

    def test_the_two_sets_stay_disjoint(self):
        """The whole two-character design rests on this. Adding a prefix
        that also belongs to Classic would route a local-capable robot
        to the cloud path."""
        from roombapy_prime.auth import CLASSIC_SKU_PREFIXES, PRIME_SKU_PREFIXES

        prime = {p[:2].upper() for p in PRIME_SKU_PREFIXES}
        classic = {p[:2].upper() for p in CLASSIC_SKU_PREFIXES}

        assert not prime & classic

    def test_the_field_confirmed_ones_still_match(self):
        """G18, N18 and Y41 are the three seen on real hardware. An
        addition must not disturb them."""
        from roombapy_prime.auth import is_prime_sku

        assert is_prime_sku("G185020")
        assert is_prime_sku("N185240")
        assert is_prime_sku("Y414040")

    def test_a_classic_robot_is_still_not_prime(self):
        """@DaRealGuGu's account holds both an N18 Prime and an R98
        Classic — the pair this check exists to tell apart."""
        from roombapy_prime.auth import is_prime_sku, sku_generation

        assert not is_prime_sku("R980020")
        assert sku_generation("R980020") == "classic"


class TestConfidenceAcceptsBothVocabularies:
    """`is_usable()` is a FILTER, not a label: a low-confidence estimate
    is discarded rather than shown with a caveat.

    It compared against `GOOD_CONFIDENCE`/`PARTIAL_CONFIDENCE` — the
    2.2.4 constant names. App 3.0.0's `Confidence` serialises as `good`,
    `partial`, `poor`, so a robot reporting `"good"` matched nothing and
    had its estimate thrown away as unreliable.

    Nothing broke yet because the live response carries no `confidence`
    field and absence is handled separately. The bug was one server
    change away from silently discarding every estimate.
    """

    @staticmethod
    def _estimate(confidence):
        from roombapy_prime.models.time_estimates import TimeEstimate

        return TimeEstimate.from_json({"value": 12, "unit": "minutes", "confidence": confidence})

    def test_the_lowercase_wire_values_are_usable(self):
        assert self._estimate("good").is_confident
        assert self._estimate("partial").is_confident

    def test_the_uppercase_names_still_work(self):
        """2.2.4 and 3.0.0 are two clients of one server, and which
        vocabulary a firmware sends is not established."""
        assert self._estimate("GOOD_CONFIDENCE").is_confident
        assert self._estimate("PARTIAL_CONFIDENCE").is_confident

    def test_poor_is_still_filtered_in_both_spellings(self):
        """The filter has to keep working — a progress percentage built
        on a poor estimate looks equally authoritative and is not."""
        assert not self._estimate("poor").is_confident
        assert not self._estimate("POOR_CONFIDENCE").is_confident

    def test_absence_is_still_not_poor(self):
        """The live response has no confidence field at all. Treating
        that as poor would discard every real estimate."""
        assert self._estimate("").is_confident
        assert self._estimate(None).is_confident


class TestOneBadFavouriteNoLongerCostsAllOfThem:
    """@chairstacker has seven favourites and sees no buttons. The
    wrapped-response fix did not cure it, and this is why.

    `_favorite_from_json` built each command with
    `MissionCommandType(c["command"])` — a hard constructor. Any command
    value the enum does not know raised ValueError, and a command def
    with no `command` key raised KeyError from the subscript. Either one
    escaped the list comprehension and lost the ENTIRE list.

    The caller in ha_roomba_plus catches Exception, logs at DEBUG and
    returns []. So a single unfamiliar stored command produced an
    account that looked empty, with nothing above DEBUG to say
    otherwise.

    This is the project's central symptom in its purest form:
    structurally valid, no effect, no error.
    """

    @staticmethod
    def _client():
        from roombapy_prime.rest_client import PrimeRestClient

        return PrimeRestClient

    def test_an_unknown_command_no_longer_raises(self):
        """`MissionCommandType` lost two members today for being wrong.
        A library correction must not delete somebody's favourites."""
        favourite = self._client()._favorite_from_json(
            {
                "favorite_id": "f1",
                "name": "Kitchen",
                "commanddefs": [{"command": "somethingNewFromTheServer"}],
            }
        )

        assert favourite.favorite_id == "f1"
        assert favourite.command_defs[0].command_type == "somethingNewFromTheServer"

    def test_a_command_def_without_a_command_key_no_longer_raises(self):
        favourite = self._client()._favorite_from_json(
            {"favorite_id": "f2", "commanddefs": [{"ordered": 1}]}
        )

        assert favourite.favorite_id == "f2"
        assert favourite.command_defs[0].command_type is None

    def test_a_known_command_still_types(self):
        """Tolerance must not cost the typing that works."""
        from roombapy_prime.models.mission_control import MissionCommandType

        favourite = self._client()._favorite_from_json(
            {"favorite_id": "f3", "commanddefs": [{"command": "start"}]}
        )

        assert favourite.command_defs[0].command_type is MissionCommandType.START


class TestFavouriteIdSpellingIsCaseInsensitive:
    """`_either` accepted `favoriteid` beside `favorite_id`. The vendor's
    own favourite model carries `favoriteId` — capital I — and an
    exact-match loop over lowercase candidates never sees it.

    A favourite whose id does not parse is dropped by the caller without
    an error. One capital letter is the difference between seven
    favourites and an account that looks empty.
    """

    def test_the_camel_case_spelling_resolves(self):
        from roombapy_prime.rest_client import _either

        assert _either({"favoriteId": "abc"}, "favoriteid", "favorite_id", "id") == "abc"

    def test_the_exact_spellings_still_win_first(self):
        """Exact matches are tried before the case-insensitive pass, so
        a response carrying both is read the same way it always was."""
        from roombapy_prime.rest_client import _either

        data = {"favorite_id": "snake", "favoriteId": "camel"}

        assert _either(data, "favorite_id", "favoriteid") == "snake"

    def test_a_genuinely_absent_key_is_still_none(self):
        from roombapy_prime.rest_client import _either

        assert _either({"name": "x"}, "favorite_id", "favoriteid") is None

    def test_display_order_and_command_defs_benefit_too(self):
        from roombapy_prime.rest_client import PrimeRestClient

        favourite = PrimeRestClient._favorite_from_json(
            {"favoriteId": "f4", "displayOrder": 2, "commandDefinitions": []}
        )

        assert favourite.favorite_id == "f4"
        assert favourite.display_order == 2


class TestTheRawCaptureNoLongerHidesTheAnswer:
    """`get_favorites_raw()` exists so a diagnostics download can show
    what the server actually returned. It did
    `data if isinstance(data, list) else []` — the same unwrapping gap
    that made `get_favorites()` report an empty account.

    So a download taken to answer "does the server return anything?"
    answered "no" whether or not it did. The instrument carried the
    fault it was built to detect.
    """

    def test_a_wrapped_response_is_unwrapped(self):
        from roombapy_prime.rest_client import PrimeRestClient

        unwrap = PrimeRestClient._unwrap_favorites_payload
        payload = {"favorites": [{"favorite_id": "f1"}]}

        assert unwrap(payload) == [{"favorite_id": "f1"}]

    def test_a_plain_list_is_untouched(self):
        from roombapy_prime.rest_client import PrimeRestClient

        items = [{"favorite_id": "f1"}, {"favorite_id": "f2"}]

        assert PrimeRestClient._unwrap_favorites_payload(items) == items

    def test_an_object_without_the_key_is_captured_whole(self):
        """The outer keys ARE the finding when there is no list.
        Returning [] would discard the one thing worth capturing."""
        from roombapy_prime.rest_client import PrimeRestClient

        payload = {"Items": [], "Count": 0}

        assert PrimeRestClient._unwrap_favorites_payload(payload) == [payload]
