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


def test_room_info_holds_fields() -> None:
    from roombapy_prime.models import RoomInfo

    poly = Polygon(coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]])
    room = RoomInfo(room_id="r1", geometry=poly, name="Kitchen", room_type=RoomType.KITCHEN)

    assert room.room_id == "r1"
    assert room.name == "Kitchen"
    assert room.room_type == RoomType.KITCHEN
    assert room.adjacent_room_ids == []


def test_furniture_info_read_has_fields_the_edit_command_lacks() -> None:
    """Confirms the corrected understanding: orientation/cleaning_area
    belong to the READ model, not the edit command (see module
    docstring for the earlier mistake this corrects)."""
    from roombapy_prime.models import FurnitureInfoRead

    poly = Polygon(coordinates=[[(0.0, 0.0)]])
    info = FurnitureInfoRead(
        furniture_id="f1", geometry=poly, furniture_type=FurnitureType.SOFA,
        user_edited=True, orientation=1.57, cleaning_area=poly,
    )

    assert info.orientation == 1.57
    assert info.cleaning_area is poly

    # the edit-side Furniture dataclass genuinely has no such fields
    from roombapy_prime.models import Furniture
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


def test_dock_info_uses_point_not_polygon() -> None:
    from roombapy_prime.models import DockInfo

    dock = DockInfo(geometry=(1.0, 2.0), orientation=0.5)
    assert dock.geometry == (1.0, 2.0)
    assert dock.orientation == 0.5


# --- mission commands (CLEAN/START/STOP/PAUSE/DOCK/etc.) -----------------

def test_mission_command_type_values_match_serialname_annotations() -> None:
    """Werte sind die tatsaechlichen @SerialName-Wire-Strings, nicht die
    Kotlin-Enum-Konstantennamen -- diese zwei sind bewusst unterschiedlich
    geprueft, da sie im Quellcode auch unterschiedlich waren."""
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
    """Bestaetigt aus CommandWrapper.java's @SerialName("cmd")."""
    from roombapy_prime.models import MissionCommandType, RoutineCommand

    cmd = RoutineCommand(command_type=MissionCommandType.STOP, asset_id="BLID123")
    desired = cmd.to_shadow_desired()

    assert set(desired.keys()) == {"cmd"}
    assert desired["cmd"]["command"] == "stop"


def test_command_params_to_json_omits_none_fields() -> None:
    """Bestaetigt (androguard): alle 37 Felder optional, nur gesetzte
    Werte landen im JSON."""
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
    """Bestaetigt (androguard): id, name, params, type."""
    from roombapy_prime.models import CommandParams, Region, RegionType

    region = Region(region_id="r1", region_type=RegionType.RID, name="Kitchen", params=CommandParams(speed=2))
    body = region.to_json()

    assert body == {"id": "r1", "type": "RID", "name": "Kitchen", "params": {"speed": 2}}


def test_command_polygon_to_json() -> None:
    """Bestaetigt (androguard): id, metadata (furnitureId), poly."""
    from roombapy_prime.models import CommandPolygon, CommandPolygonMetadata

    polygon = CommandPolygon(
        polygon_id="poly1", poly=[(0.0, 0.0), (1.0, 1.0)], metadata=CommandPolygonMetadata(furniture_id=5)
    )
    body = polygon.to_json()

    assert body == {"id": "poly1", "poly": [[0.0, 0.0], [1.0, 1.0]], "metadata": {"furnitureId": 5}}


def test_routine_command_with_typed_regions_and_params() -> None:
    """NEU (11. Juli, achte Sitzung) -- RoutineCommand.regions/params
    akzeptieren jetzt die typisierten Modelle statt nur rohe dicts."""
    from roombapy_prime.models import CommandParams, MissionCommandType, Region, RegionType, RoutineCommand

    cmd = RoutineCommand(
        command_type=MissionCommandType.CLEAN,
        asset_id="BLID123",
        regions=[Region(region_id="r1", region_type=RegionType.RID)],
        params=CommandParams(suction_level=2),
    )
    body = cmd.to_json()

    assert body["regions"] == [{"id": "r1", "type": "RID"}]
    assert body["params"] == {"suctionLevel": 2}


def test_routine_command_still_accepts_raw_dicts_for_backward_compat() -> None:
    """Abwaertskompatibilitaet: raw dicts funktionieren weiterhin neben
    den neuen typisierten Modellen."""
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
    """NEU (11. Juli, neunte Sitzung) -- Top-Level-Felder bestaetigt aus
    MissionHistory (androguard)."""
    from roombapy_prime.models import DoneCode, parse_mission_history

    raw = {
        "missions": [
            {
                "missionId": "m1",
                "robotId": "BLID123",
                "startTime": 1000,
                "durationM": 45,
                "doneCode": "OK",
                "squareFeetCovered": 500,
                "command": {"command": "clean", "robotId": "BLID123", "cleanAll": True},
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
    """Server kann neue doneCode-Werte einfuehren -- soll nicht crashen."""
    from roombapy_prime.models import parse_mission_history

    entries = parse_mission_history([{"missionId": "m1", "doneCode": "SOME_NEW_CODE"}])
    assert entries[0].done_code == "SOME_NEW_CODE"


def test_command_params_from_json_roundtrip() -> None:
    """NEU (11. Juli, neunte Sitzung) -- from_json ist die Kehrfunktion
    zu to_json."""
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
    """Bestaetigt (androguard): profile, commandParams, regions."""
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


def test_household_setting_from_json() -> None:
    from roombapy_prime.models import HouseholdSetting

    setting = HouseholdSetting.from_json({"settingId": "s1", "settingType": "dnd", "options": {"foo": "bar"}})

    assert setting.setting_id == "s1"
    assert setting.setting_type == "dnd"
    assert setting.options == {"foo": "bar"}


def test_parse_default_routines() -> None:
    """Bestaetigt (androguard, routines/datamodels/Routine)."""
    from roombapy_prime.models import parse_default_routines

    routines = parse_default_routines(
        {"routines": [{"name": "Whole Home", "commandDefs": [{"command": "clean"}], "timeEstimate": 30}]}
    )

    assert len(routines) == 1
    assert routines[0].name == "Whole Home"
    assert routines[0].time_estimate == 30
    assert routines[0].command_defs == [{"command": "clean"}]


# =========================================================================
# MissionTimelineEvent -- alle 20 Unterereignistypen (18. Sitzung)
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
    """Bestaetigt (androguard, jadx hatte diese Klasse uebersprungen) --
    'ordered' hier eine Intra-Event-Eigenschaft, siehe Docstring."""
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
    from roombapy_prime.models import TravelDestination, TravelEvent

    e = TravelEvent.from_json(
        {
            "destination": "DOCK",
            "mapId": "m1",
            "mapVersion": "v1",
            "polyId": "p1",
            "reason": 0,
            "regionId": "r1",
            "status": 1,
            "waypointId": "w1",
            "zoneId": "z1",
        }
    )
    assert e.destination == TravelDestination.DOCK
    assert e.waypoint_id == "w1"


def test_traversal_event_from_json() -> None:
    from roombapy_prime.models import TraversalEvent, TraversalType

    e = TraversalEvent.from_json({"mapId": "m1", "mapVersion": "v1", "regionId": "r1", "type": "ZONE", "zoneId": "z1"})
    assert e.traversal_type == TraversalType.ZONE
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
    """Nur EIN Unterfeld sollte gesetzt sein, passend zum 'type'-Wert --
    alle anderen 19 bleiben None."""
    from roombapy_prime.models import MissionTimelineEvent

    e = MissionTimelineEvent.from_json(
        {"startTime": 100, "endTime": 200, "type": "zone", "zone": {"zoneId": "z1", "area": 10}}
    )
    assert e.event_type == "zone"
    assert e.zone is not None
    assert e.zone.zone_id == "z1"
    # alle anderen 19 Unterfelder muessen None bleiben
    other_fields = [
        e.command, e.discovery, e.error, e.evac, e.live_view, e.pad_dry, e.pad_wash,
        e.panorama, e.plan, e.polygon, e.refill, e.relocalizing, e.room, e.sub_room,
        e.tentative_location, e.travel, e.traversal, e.waypoint, e.wet_out,
    ]
    assert all(f is None for f in other_fields)


def test_mission_timeline_event_relocalizing_and_tentative_location_share_type() -> None:
    """Bestaetigt (androguard): beide Felder nutzen denselben Typ
    TentativeLocationEvent, aber sind unabhaengige Felder."""
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
    """Integrationstest: MissionHistoryEntry.from_json() befuellt jetzt
    .timeline mit typisierten MissionTimelineEvent-Objekten statt rohem
    JSON (18. Sitzung -- schliesst die fruehere Aufwandsgrenze)."""
    from roombapy_prime.models import MissionHistoryEntry

    entry = MissionHistoryEntry.from_json(
        {
            "missionId": "m1",
            "timeline": {
                "coverageStrategy": "ROOM_SEGMENTATION",
                "events": [
                    {"type": "room", "room": {"regionId": "r1", "status": 1}},
                    {"type": "zone", "zone": {"zoneId": "z1", "status": 1}},
                ],
            },
        }
    )
    assert len(entry.timeline) == 2
    assert entry.timeline[0].room.region_id == "r1"
    assert entry.timeline[1].zone.zone_id == "z1"
