"""Tests for roombapy_prime.models.

Command-body and geometry shapes are SYNTHETIC checks against the
Java-source-confirmed structure documented in FINDINGS_2026-07-11.md --
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
    assert sample.orientation == 0.0 + 3.1415927
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


def test_parse_livemap_unrecognized_shape_raises() -> None:
    import pytest

    payload = json.dumps({"something_else": True}).encode()
    with pytest.raises(ValueError, match="Unrecognized"):
        parse_livemap_message(payload)


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
    """NEW (session 47) -- confirms PolicyZoneFeature is the single,
    now-confirmed type covering what used to be three separate,
    unconfirmed guesses (NoMopZoneInfo/KeepOutZoneInfoRead/
    VirtualWallInfo), discriminated by zone_type/threshold_type."""
    from roombapy_prime.models import PolicyZoneFeature

    zone = PolicyZoneFeature.from_json({
        "type": "Feature", "id": "z1",
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
        "properties": {"type": "no_mop", "threshold_type": "soft"},
    })
    assert zone.properties.zone_type == "no_mop"
    assert zone.properties.threshold_type == "soft"


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
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$RenameRoom$$serializer -- room_id/
    room_name, not the previously-guessed id/name."""
    from roombapy_prime.models import RenameRoomV1

    body = RenameRoomV1(room_id="r1", name="Kitchen").to_v1_command_body()
    assert body == {"type": "RenameRoom", "room_id": "r1", "room_name": "Kitchen"}


def test_split_room_v1_confirmed_field_names() -> None:
    from roombapy_prime.models import SplitRoomV1

    body = SplitRoomV1(room_id="r1", split_points=[(0.0, 0.0), (1.0, 1.0)]).to_v1_command_body()
    assert body == {"type": "SplitRoom", "room_id": "r1", "split_points": [[0.0, 0.0], [1.0, 1.0]]}


def test_merge_rooms_v1_confirmed_field_name() -> None:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$MergeRooms$$serializer -- room_ids, not
    the previously-guessed ids."""
    from roombapy_prime.models import MergeRoomsV1

    body = MergeRoomsV1(ids=["r1", "r2"]).to_v1_command_body()
    assert body == {"type": "MergeRooms", "room_ids": ["r1", "r2"]}


def test_set_room_type_v1_confirmed_field_names() -> None:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$SetRoomType$$serializer -- room_id/
    type_id, not the previously-guessed id/type."""
    from roombapy_prime.models import RoomType, SetRoomTypeV1

    body = SetRoomTypeV1(room_id="r1", room_type=RoomType.KITCHEN).to_v1_command_body()
    assert body == {"type": "SetRoomType", "room_id": "r1", "type_id": int(RoomType.KITCHEN)}


def test_set_permanent_areas_v1_confirmed_field_name() -> None:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$SetPermanentAreas$$serializer --
    area_points (snake_case), not the previously-guessed areaPoints."""
    from roombapy_prime.models import PermanentAreaV1, SetPermanentAreasV1

    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0)]])
    area = PermanentAreaV1(area_id="a1", name="Zone", geometry=poly)
    body = SetPermanentAreasV1(areas=[area]).to_v1_command_body()

    assert body["type"] == "SetPermanentAreas"
    assert "area_points" in body
    assert "areaPoints" not in body
    assert body["area_points"][0]["id"] == "a1"


def test_delete_permanent_areas_v1_confirmed_field_name() -> None:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$DeletePermanentAreas$$serializer --
    area_ids (snake_case), not the previously-guessed areaIDs."""
    from roombapy_prime.models import DeletePermanentAreasV1

    body = DeletePermanentAreasV1(area_ids=["a1", "a2"]).to_v1_command_body()
    assert body == {"type": "DeletePermanentAreas", "area_ids": ["a1", "a2"]}


def test_set_virtual_walls_v1_confirmed_field_name() -> None:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$SetVirtualWalls$$serializer -- the
    unusual abbreviation `virwall`, not the previously-guessed
    `walls`. The internal Linear/Rectangle/NoMopZone discriminator
    remains unconfirmed (custom serializer, see the module docstring)."""
    from roombapy_prime.models import SetVirtualWallsV1, VirtualWallLinearV1

    wall = VirtualWallLinearV1(wall_id="w1", from_pos=(0.0, 0.0), to_pos=(1.0, 1.0))
    body = SetVirtualWallsV1(walls=[wall]).to_v1_command_body()

    assert body["type"] == "SetVirtualWalls"
    assert "virwall" in body
    assert "walls" not in body


def test_adjust_furniture_v1_confirmed_field_names() -> None:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$AdjustFurniture$$serializer --
    furniture_list/package (snake_case, and "package" not
    "packageInfo"), not the previously-guessed furnitureList/
    packageInfo. timestamp was already correct."""
    from roombapy_prime.models import AdjustFurnitureV1, FurnitureItemV1

    poly = Polygon(coordinates=[[(0.0, 0.0)]])
    item = FurnitureItemV1(furniture_id="f1", furniture_type=FurnitureType.SOFA, geometry=poly)
    body = AdjustFurnitureV1(furniture_list=[item], timestamp=123).to_v1_command_body()

    assert body["type"] == "AdjustFurniture"
    assert body["timestamp"] == 123
    assert "furniture_list" in body
    assert "package" in body
    assert "furnitureList" not in body
    assert "packageInfo" not in body


# --- mission commands (CLEAN/START/STOP/PAUSE/DOCK/etc.) -----------------

def test_mission_command_type_values_match_serialname_annotations() -> None:
    """Values are the actual @SerialName wire strings, not the Kotlin
    enum constant names -- these two are deliberately checked
    separately, since they also differed in the source code."""
    from roombapy_prime.models import MissionCommandType

    assert MissionCommandType.CLEAN_SPOT.value == "point_clean"
    assert MissionCommandType.TIDY.value == "tidy"
    assert MissionCommandType.START.value == "start"
    assert len(list(MissionCommandType)) == 30


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


def test_command_params_to_json_omits_none_fields() -> None:
    """Confirmed (androguard): all 37 fields optional, only set
    values end up in the JSON."""
    from roombapy_prime.models import CommandParams

    params = CommandParams(suction_level=3, room_confine=True)
    body = params.to_json()

    assert body == {"suctionLevel": 3, "roomConfine": True}


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

    assert body == {"id": "r1", "type": "rid", "name": "Kitchen", "params": {"speed": 2}}


def test_command_polygon_to_json() -> None:
    """Confirmed (androguard): id, metadata (furnitureId), poly."""
    from roombapy_prime.models import CommandPolygon, CommandPolygonMetadata

    polygon = CommandPolygon(
        polygon_id="poly1", poly=[(0.0, 0.0), (1.0, 1.0)], metadata=CommandPolygonMetadata(furniture_id=5)
    )
    body = polygon.to_json()

    assert body == {"id": "poly1", "poly": [[0.0, 0.0], [1.0, 1.0]], "metadata": {"furnitureId": 5}}


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

    assert body["regions"] == [{"id": "r1", "type": "rid"}]
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
    """Confirmed (androguard): profile, commandParams, regions."""
    from roombapy_prime.models import CleaningProfile, CleaningProfileType

    profile = CleaningProfile.from_json(
        {"profile": "DEEP", "commandParams": {"suctionLevel": 3}, "regions": [{"id": "r1"}]}
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
    created_time (not createdTime), force_cloud (not forceCloud)."""
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
    assert body["end_commands"] == [end_cmd.to_json()]
    assert body["created_time"] == "2026-07-15T00:00:00Z"
    assert body["force_cloud"] is True
    assert "assetId" not in body
    assert "endCommands" not in body
    assert "createdTime" not in body
    assert "forceCloud" not in body


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
    the older parse_default_routines() helper never captured at all."""
    from roombapy_prime.models import RoutinesDefaultsResponse

    response = RoutinesDefaultsResponse.from_json({
        "routines": [{"name": "Whole Home"}],
        "routine_builder_defaults": {
            "regions": [
                {
                    "type": "room",
                    "operating_mode": "vacuum",
                    "by_operating_mode": {"vacuum": {"params": {"suction": 3}, "profile_type": "standard"}},
                }
            ]
        },
    })

    assert len(response.routines) == 1
    assert response.routines[0].name == "Whole Home"
    assert response.routine_builder_defaults is not None
    region = response.routine_builder_defaults.regions[0]
    assert region.region_type == "room"
    assert region.by_operating_mode["vacuum"].profile_type == "standard"


def test_routines_defaults_response_handles_missing_builder_defaults() -> None:
    from roombapy_prime.models import RoutinesDefaultsResponse

    response = RoutinesDefaultsResponse.from_json({"routines": []})
    assert response.routine_builder_defaults is None


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
    from roombapy_prime.models import PolygonEvent

    e = PolygonEvent.from_json(
        {"area": 10, "areaCleaned": 8, "mapId": "m1", "mapVersion": "v1", "poly": [[0, 0]], "polyId": "p1", "regionId": "r1"}
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
    assert s.svc_deployment_id is None
    assert s.languages_raw is None


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
