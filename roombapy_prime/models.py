"""State/command payload types for roombapy-prime.

STATUS: Draft. Based on Java/Kotlin source code analysis of the
`irobotdata` layer used by the Prime app (see
docs/FINDINGS_2026-07-11.md) -- NOT live-verified against a real V4
account. Wire shapes here are as accurate as the analysis allows, but
untested against real server responses.

Contains:
  - Geometry primitives (Position/Point/LineString/Polygon) -- confirmed
    pure GeoJSON (see GeometrySerializer.java: Polygon.getRawValue()
    returns List<List<List<Double>>>, exactly GeoJSON polygon nesting)
  - RoomType, FurnitureType -- int enums, values from Java source code
  - The 10 confirmed p2maps edit commands (POST /v2/p2maps/{id}/versions)
  - Live map/position response models (GET /v1/p2maps/livemap)
"""
from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from io import BytesIO
from typing import Any


# --- Geometry (GeoJSON) -------------------------------------------------
#
# Confirmed in com.irobot.irobotdata.maps.domainmodels.geometry.*:
# Position is a flat [x, y] (optional [x, y, z]) array (Position there
# even extends ArrayList<Double> directly). Point/LineString/Polygon
# are standard GeoJSON with a "type" field. LinearRing is just an
# internal Kotlin marker -- on the wire, Polygon.coordinates is a pure
# [[[x,y],...]] nesting with no LinearRing object wrapper.

Position = tuple[float, float]  # (x, y) -- z isn't used anywhere so far


def _position_to_raw(pos: Position) -> list[float]:
    return [pos[0], pos[1]]


@dataclass(frozen=True)
class Point:
    coordinates: Position

    def to_geojson(self) -> dict[str, Any]:
        return {"type": "Point", "coordinates": _position_to_raw(self.coordinates)}


@dataclass(frozen=True)
class LineString:
    coordinates: list[Position]

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "LineString",
            "coordinates": [_position_to_raw(p) for p in self.coordinates],
        }


@dataclass(frozen=True)
class Polygon:
    """coordinates: list of rings, each ring a list of Position.
    First ring = outer boundary, further ones = holes (standard
    GeoJSON, never observed here in the wild with more than one
    ring)."""

    coordinates: list[list[Position]]

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "Polygon",
            "coordinates": [[_position_to_raw(p) for p in ring] for ring in self.coordinates],
        }


@dataclass(frozen=True)
class MultiPolygon:
    """coordinates: list of Polygon -- confirmed in
    MultiPolygon.java (extends Geometry, type="MultiPolygon",
    coordinates: List<Polygon>). Only needed for read models
    (BorderInfo, CoverageInfo) -- no edit command uses this so far."""

    coordinates: list[Polygon]

    def to_geojson(self) -> dict[str, Any]:
        return {"type": "MultiPolygon", "coordinates": [p.to_geojson()["coordinates"] for p in self.coordinates]}


# --- RoomType / FurnitureType -------------------------------------------
#
# Values taken literally from EditMapV2Request$RoomType (int enum) and
# P2MapFurnitureInfo$FurnitureType (int enum). set_room_type is
# @Deprecated per the source code in favor of set_room_metadata --
# still modeled here anyway, since the command technically still
# exists.

class RoomType(IntEnum):
    NOT_RECOGNIZED = 2100
    BEDROOM = 2101
    DINING_ROOM = 2102
    BATHROOM = 2103
    HALLWAY = 2104
    KITCHEN = 2105
    LIVING_ROOM = 2106
    BALCONY = 2107
    OTHER = 2120


class FurnitureType(IntEnum):
    UNKNOWN = 0
    BED = 1
    SOFA = 2
    DINING_TABLE = 3
    COFFEE_TABLE = 4
    TOILET = 5
    LIVING_CHAIR = 6
    LEFT_L_SOFA = 7
    RIGHT_L_SOFA = 8
    CABINET = 9
    REFRIGERATOR = 10
    SIDETABLE = 11
    TVCABINET = 12
    WASHINGMACHINEORDRYER = 13
    LITTER_BOX = 14
    PET_BOWL = 15
    PET_BED = 16
    PET_FEEDER = 17
    CAT_TOWER = 18


# --- p2maps edit commands (POST /v2/p2maps/{id}/versions) --------------
#
# Body envelope for all commands: {"command": "<cmd>", "params": {...}}.
# Every command class here has a to_command_body() method that produces
# exactly this envelope. Field names (snake_case JSON keys) are taken
# from the Kotlin @SerialName annotations, see
# docs/FINDINGS_2026-07-11.md for the full derivation.
#
# IMPORTANT: not a single one of these 10 commands has been live-tested
# against a real server -- only the Java serialization logic is
# confirmed. Treated as a draft until real responses are available.


@dataclass(frozen=True)
class SetRoomMetadata:
    room_id: str
    name: str | None = None
    room_type: RoomType | None = None

    def to_command_body(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if self.name is not None:
            metadata["name"] = self.name
        if self.room_type is not None:
            metadata["type_id"] = int(self.room_type)
        return {
            "command": "set_room_metadata",
            "params": {"id": self.room_id, "metadata": metadata},
        }


@dataclass(frozen=True)
class MergeRooms:
    room_ids: list[str]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "merge_rooms", "params": {"ids": self.room_ids}}


@dataclass(frozen=True)
class SplitRoom:
    room_id: str
    split_line: LineString

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "split_room",
            "params": {"id": self.room_id, "split_line": self.split_line.to_geojson()},
        }

    @classmethod
    def from_two_points(cls, room_id: str, from_pos: Position, to_pos: Position) -> "SplitRoom":
        return cls(room_id=room_id, split_line=LineString([from_pos, to_pos]))


@dataclass(frozen=True)
class SetRoomType:
    """@Deprecated in the Kotlin source code in favor of
    SetRoomMetadata -- still modeled here anyway, since the command
    still exists."""

    room_id: str
    room_type: RoomType

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_room_type",
            "params": {"room_id": self.room_id, "type_id": int(self.room_type)},
        }


@dataclass(frozen=True)
class KeepOutZone:
    """Covers both linear and rectangular keep-out zones -- depending
    on whether a LineString or a Polygon is passed."""

    geometry: LineString | Polygon
    zone_id: str | None = None

    def to_geojson(self) -> dict[str, Any]:
        payload = self.geometry.to_geojson()
        if self.zone_id is not None:
            return {"id": self.zone_id, "geometry": payload}
        return {"geometry": payload}


@dataclass(frozen=True)
class SetKeepOutZones:
    keep_out_zones: list[KeepOutZone] = field(default_factory=list)
    no_mop_zones: list[KeepOutZone] = field(default_factory=list)
    virtual_walls: list[KeepOutZone] = field(default_factory=list)

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_keep_out_zones",
            "params": {
                "keep_out_zones": [z.to_geojson() for z in self.keep_out_zones],
                "no_mop_zones": [z.to_geojson() for z in self.no_mop_zones],
                "virtual_walls": [z.to_geojson() for z in self.virtual_walls],
            },
        }


@dataclass(frozen=True)
class CleanZone:
    name: str
    geometry: Polygon
    zone_id: str | None = None

    def to_geojson(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "geometry": self.geometry.to_geojson()}
        if self.zone_id is not None:
            payload["id"] = self.zone_id
        return payload


@dataclass(frozen=True)
class AddCleanZones:
    zones: list[CleanZone]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "add_clean_zones", "params": {"zones": [z.to_geojson() for z in self.zones]}}


@dataclass(frozen=True)
class DeleteCleanZones:
    zone_ids: list[str]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "delete_clean_zones", "params": {"ids": self.zone_ids}}


@dataclass(frozen=True)
class Furniture:
    furniture_type: FurnitureType
    geometry: Polygon
    furniture_id: str | None = None
    user_modified: bool = True

    def to_geojson(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user_modified": self.user_modified,
            "geometry": self.geometry.to_geojson(),
            "type": self.furniture_type.name.lower(),
        }
        if self.furniture_id is not None:
            payload["id"] = self.furniture_id
        return payload


@dataclass(frozen=True)
class SetFurniture:
    furniture: list[Furniture]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "set_furniture", "params": {"furniture": [f.to_geojson() for f in self.furniture]}}


@dataclass(frozen=True)
class RevertUserEdits:
    def to_command_body(self) -> dict[str, Any]:
        return {"command": "revert_user_edits", "params": {}}


@dataclass(frozen=True)
class FloorTypeEntry:
    """Two variants in the source code (WithGeometry / WithRoomId) --
    exactly one of geometry/room_id must be set, not both."""

    floor_type_id: str
    type_name: str
    name: str
    enabled: bool
    user_modified: bool = True
    geometry: Polygon | None = None
    room_id: str | None = None

    def to_geojson(self) -> dict[str, Any]:
        if (self.geometry is None) == (self.room_id is None):
            msg = "FloorTypeEntry needs exactly one of geometry or room_id"
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "id": self.floor_type_id,
            "type": self.type_name,
            "user_modified": self.user_modified,
            "name": self.name,
            "enabled": self.enabled,
        }
        if self.geometry is not None:
            payload["geometry"] = self.geometry.to_geojson()
        else:
            payload["room_id"] = self.room_id
        return payload


@dataclass(frozen=True)
class SetFloorTypes:
    floor_types: list[FloorTypeEntry]

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_floor_types",
            "params": {"floor_types": [f.to_geojson() for f in self.floor_types]},
        }


@dataclass(frozen=True)
class ThresholdEntry:
    threshold_id: str
    status: str
    geometry: Polygon

    def to_geojson(self) -> dict[str, Any]:
        return {"id": self.threshold_id, "status": self.status, "geometry": self.geometry.to_geojson()}


@dataclass(frozen=True)
class SetThresholds:
    thresholds: list[ThresholdEntry]

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_thresholds",
            "params": {"thresholds": [t.to_geojson() for t in self.thresholds]},
        }


MapEditCommand = (
    SetRoomMetadata
    | MergeRooms
    | SplitRoom
    | SetRoomType
    | SetKeepOutZones
    | AddCleanZones
    | DeleteCleanZones
    | SetFurniture
    | RevertUserEdits
    | SetFloorTypes
    | SetThresholds
)


# --- Live map/position (GET /v1/p2maps/livemap) ----------------------
#
# See docs/FINDINGS_2026-07-11.md section 2 for the full derivation.
# cur_path is a flat JSON array:
# [seq_nr, x1,y1,orient1,mode1, x2,y2,orient2,mode2, ..., epoch_ts]


@dataclass(frozen=True)
class LiveMapStreamInit:
    """Response to GET /v1/p2maps/livemap?robotId={blid}."""

    mqtt_topic: str
    initial_map_url: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LiveMapStreamInit":
        return cls(mqtt_topic=data["mqtt_topic"], initial_map_url=data.get("livemap_url"))


@dataclass(frozen=True)
class PositionSample:
    point: Position
    orientation: float
    operating_modes: int


@dataclass(frozen=True)
class PositionUpdateMessage:
    """A message on the livemap topic with position data. Multiple
    points per message are normal (trajectory-like, see FINDINGS)."""

    sequence_number: int
    updates: list[PositionSample]
    last_update_timestamp: datetime

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PositionUpdateMessage":
        """data is the "pos_update" envelope including cur_path.

        cur_path length must be (2 + 4*n) for n position points --
        exactly as checked in PositionUpdatesSerializer.deserialize().
        Orientation is shifted by +pi, same as in the original -- the
        reason for this convention wasn't further investigated.
        """
        cur_path = data["cur_path"]
        if (len(cur_path) - 2) % 4 != 0:
            msg = f"cur_path unexpected size: {len(cur_path)}"
            raise ValueError(msg)

        sequence_number = int(cur_path[0])
        epoch_ts = cur_path[-1]
        point_values = cur_path[1:-1]

        updates = [
            PositionSample(
                point=(point_values[i], point_values[i + 1]),
                orientation=point_values[i + 2] + 3.1415927,
                operating_modes=int(point_values[i + 3]),
            )
            for i in range(0, len(point_values), 4)
        ]

        return cls(
            sequence_number=sequence_number,
            updates=updates,
            last_update_timestamp=datetime.fromtimestamp(epoch_ts, tz=timezone.utc),
        )


@dataclass(frozen=True)
class MapUpdateMessage:
    """The other message shape on the livemap topic: a new map image
    is available, not a position update."""

    livemap_url: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MapUpdateMessage":
        return cls(livemap_url=data["map_update"]["livemap_url"])


def parse_livemap_message_data(data: dict[str, Any]) -> PositionUpdateMessage | MapUpdateMessage:
    """Core logic, operates on already-parsed JSON (dict). For
    parse_livemap_message() (raw bytes) AND for prime_robot.py's
    watch_live_map() (already gets the payload as a dict from
    mqtt_client.py's ShadowResponse -- re-serializing would be
    unnecessary)."""
    if "pos_update" in data:
        return PositionUpdateMessage.from_json(data["pos_update"])
    if "map_update" in data:
        return MapUpdateMessage.from_json(data)
    msg = f"Unrecognized livemap message shape: keys={list(data.keys())}"
    raise ValueError(msg)


def parse_livemap_message(raw_payload: bytes) -> PositionUpdateMessage | MapUpdateMessage:
    """Decides based on the keys present which of the two message
    shapes this is (see FINDINGS section 2, point 3)."""
    return parse_livemap_message_data(json.loads(raw_payload))


# =========================================================================
# Read models: what's actually IN a map
# =========================================================================
#
# STATUS: New, notably LESS CERTAIN than the edit commands above.
#
# The edit commands (SetRoomMetadata, SplitRoom, etc.) are
# @Serializable Kotlin classes with explicit JsonObjectBuilder
# serializers -- the wire JSON format there was directly readable
# from the serialization code.
#
# These read models here (com.irobot.irobotdata.maps.domainmodels.
# p2maps.bundlecontents.*) are PLAIN Kotlin data classes WITHOUT
# visible @Serializable/@SerialName annotations -- they're presumably
# populated via a separate bundle-unpacking mechanism
# (P2MapBundleContentHolder / P2MapInfoFactory) from a raw format
# whose exact wire structure was NOT part of today's analysis. The
# field names here are the Kotlin property names -- a plausible, but
# NOT JSON-level confirmed, assumption for the actual keys.

#
# IMPORTANT, DELIBERATELY OPEN ITEM: these classes are individually
# modeled, but there's still NO parser that breaks a complete
# get_map_metadata()/fetchPersistentMap() response down into these
# types -- the overall envelope format (how P2MapBundleContentHolder
# combines the individual "infoType" discriminators like "rooms",
# "borders", "hazard", "trajectories", "coverage", "dockPoses",
# "furniture", "adHocCleanZones", "cleanZones" into one response)
# wasn't investigated today. get_map_metadata() in rest_client.py
# still returns raw, unparsed JSON.


class RoomTypeSource(str, Enum):
    """Confirmed from P2MapRoomInfo$RoomType$Source -- HOW a room type
    came about (detected vs. set by the user). Exact string values not
    confirmed 1:1 (enum names yes, wire string serialization not
    explicitly seen in the code) -- filled in here as a placeholder
    with the enum names themselves, not as confirmed wire strings."""

    DETECTED = "DETECTED"
    USER_SET = "USER_SET"


@dataclass(frozen=True)
class RoomInfo:
    """Confirmed from P2MapRoomInfo (read model -- not to be confused
    with SetRoomMetadata, the edit command above)."""

    room_id: str
    geometry: Polygon
    name: str | None = None
    simplified_geometry: Polygon | None = None
    room_type: RoomType | None = None
    adjacent_room_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BorderInfo:
    """Confirmed from P2MapBorderInfo: just a MultiPolygon geometry,
    no id field."""

    geometry: MultiPolygon


@dataclass(frozen=True)
class TrajectoryInfo:
    """Confirmed from P2MapTrajectoryInfo. operating_modes: raw values
    from P2MapOperatingModes.OperatingMode -- its exact values weren't
    found today, so passed through as raw strings/ints instead of a
    dedicated enum."""

    geometry: LineString
    index: int | None = None
    operating_modes: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class CoverageInfo:
    """Confirmed from P2MapCoverageInfo."""

    geometry: MultiPolygon
    operating_modes: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class DockInfo:
    """Confirmed from P2MapDockInfo -- position as Point, not Polygon."""

    geometry: Point
    orientation: float | None = None


class HazardType(str, Enum):
    """Confirmed from P2MapHazardInfo$HazardType, complete list."""

    UNKNOWN = "UNKNOWN"
    BAR_STOOL = "BAR_STOOL"
    BLANKET = "BLANKET"
    CABLES = "CABLES"
    CAT = "CAT"
    DOG = "DOG"
    DRY_DEBRIS = "DRY_DEBRIS"
    LIQUID = "LIQUID"
    OTHER_TOYS = "OTHER_TOYS"
    PERSON = "PERSON"
    PET_WASTE = "PET_WASTE"
    PURSE = "PURSE"
    SHOES = "SHOES"
    SOCKS = "SOCKS"
    TRASH_CAN = "TRASH_CAN"
    WEIGHING_SCALE = "WEIGHING_SCALE"


@dataclass(frozen=True)
class HazardInfo:
    """Confirmed from P2MapHazardInfo -- position as Point."""

    hazard_id: str
    hazard_type: HazardType
    geometry: Point


@dataclass(frozen=True)
class NoMopZoneInfo:
    """Confirmed from P2MapNoMopZoneInfo: just geometry + id."""

    zone_id: str
    geometry: Polygon


@dataclass(frozen=True)
class AdHocCleanZoneInfo:
    """Confirmed from P2MapAdHocCleanZoneInfo: just geometry + id."""

    zone_id: str
    geometry: Polygon


@dataclass(frozen=True)
class KeepOutZoneInfoRead:
    """Confirmed from P2MapKeepOutZoneInfo (read model). Deliberately
    named ...Read to avoid confusion with the identically-named edit
    concept (KeepOutZone above, part of SetKeepOutZones) -- there a
    linear/rectangle distinction, here just geometry + id."""

    zone_id: str
    geometry: Polygon


@dataclass(frozen=True)
class VirtualWallInfo:
    """Confirmed from P2MapVirtualWallInfo: LineString instead of
    Polygon."""

    wall_id: str
    geometry: LineString


@dataclass(frozen=True)
class CleanZoneInfoRead:
    """Confirmed from P2MapCleanZoneInfo (read model, with name --
    unlike the other simple zones). Named ...Read for the same reason
    as KeepOutZoneInfoRead."""

    zone_id: str
    name: str | None
    geometry: Polygon


@dataclass(frozen=True)
class FurnitureInfoRead:
    """Confirmed from P2MapFurnitureInfo (read model) -- has TWO more
    fields than the edit command SetFurniture/Furniture above
    (orientation, cleaning_area). This was a mistake in an earlier
    version of this analysis: wrongly reported there as missing from
    the edit command -- these fields actually only belong here, in the
    read model (confirmed against EditMapV2Request.Furniture's
    serializer, which really only sends id/type/userModified/
    geometry)."""

    furniture_id: str
    geometry: Polygon
    furniture_type: FurnitureType
    user_edited: bool
    orientation: float
    cleaning_area: Polygon | None = None


# =========================================================================
# Mission control (CLEAN/START/STOP/PAUSE/DOCK/etc.)
# =========================================================================
#
# STATUS: NEW (July 11, second session). Previously classified as a
# "structurally hard native boundary" (see PRIME_APP_GAP_ANALYSIS_2026-07-11.md
# point C1) -- that was only half right. The DISPATCH mechanism
# (core::CommandTierAgentImpl::postCommand()) is indeed native and
# stays invisible. But the actual PAYLOAD (RoutineCommand) is a
# completely normal, @Serializable Kotlin class with explicit
# @SerialName annotations -- so the same confidence level as the
# p2maps edit commands above, NOT like a native mystery.
#
# Transport confirmed via native disassembly (aarch64-objdump):
# liblegacyCore.so literally contains the format string
# "$aws/things/%s/shadow/update" (address 0xde2a3a) -- commands go
# through the already-implemented shadow update() path
# (mqtt_client.py), NOT via a separate "cmd" topic (that had already
# been confirmed as a dead end in earlier sessions -- consistent).
#
# Payload envelope confirmed from CommandWrapper.java (@Serializable,
# exactly one field, @SerialName("cmd")): state.desired.cmd = RoutineCommand.
#
# STILL OPEN: the native postCommand() path itself wasn't traced all
# the way to the actual MQTT publish call (several levels of
# indirection through non-exported static functions with no symbol
# names -- not economically resolvable further with the available
# tools (objdump, no real decompiler like Ghidra/IDA)). The envelope
# documented HERE (shadow update, "cmd" key) is a combination of two
# independent facts that were never confirmed TOGETHER live -- never
# sent to a real server.


class MissionCommandType(str, Enum):
    """Confirmed from com.irobot.data.missioncommand.datamodels.
    CommandType -- values are the actual @SerialName strings, NOT the
    Kotlin enum constant names (e.g. CLEAN_SPOT serializes as
    "point_clean", not "clean_spot")."""

    CLEAN = "clean"
    QUICK = "quick"
    SPOT = "spot"
    DOCK = "dock"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    WAKE = "wake"
    RESET = "reset"
    FIND = "find"
    WIPE = "wipe"
    IPDONE = "ipdone"
    PROVDONE = "provdone"
    RECHRG = "rechrg"
    TRAIN = "train"
    EVAC = "evac"
    STOPEVAC = "stopevac"
    QUERYDOCK = "querydock"
    TIDY = "tidy"
    VIEWPOINT = "viewpoint"
    STARTLOG = "startlog"
    SKIP = "skip"
    FLREFILL = "flrefill"
    WASHPAD = "washpad"
    DRYPAD = "drypad"
    STOPPADDRY = "stoppaddry"
    FLUSHSLUICE = "flushsluice"
    CLEAN_SPOT = "point_clean"
    START_CLEAN = "start_clean"


@dataclass(frozen=True)
class RoutineCommand:
    """Confirmed from com.irobot.data.missioncommand.datamodels.
    RoutineCommand (@Serializable). Field name mapping taken 1:1 from
    the @SerialName annotations in the source code, NOT guessed:
      type -> "command", assetId -> "robot_id", mapId -> "p2map_id",
      cleanAll -> "select_all", idMultipolys -> "id_multipolys",
      pmapVersionId -> "user_p2mapv_id", spotGeometry -> "geom",
      favoriteId -> "favorite_id". ordered/params/regions have NO
      dedicated @SerialName -- they serialize under their property
      name.

    CORRECTED (eleventh session, via cross-checking with
    ha_roomba_plus): "ordered" is NOT an indication of sequencing
    multiple separately-sent RoutineCommand objects (e.g. from a
    FavoriteV1/Routine.commandDefs list). ha_roomba_plus (verified
    against real Classic devices in production for years) uses
    "ordered" as an INTRA-command property alongside "regions" within
    the same command object: whether the regions WITHIN this one
    command should be visited in listed order, or the robot itself is
    allowed to optimize. Whether multiple commandDefs entries are
    actually sent as separate, sequential commands thus remains
    UNRESOLVED -- "ordered" is not evidence for that.

    params/regions/id_multipolys passed through as raw dicts -- their
    nested structure (CommandParams/Region/CommandPolygon) wasn't
    modeled in detail today."""

    command_type: MissionCommandType
    asset_id: str
    map_id: str | None = None
    ordered: int = 0
    """Intra-command property (see class docstring): 1 = visit regions
    in listed order, 0 (presumably) = robot is allowed to optimize.
    Confirmed from ha_roomba_plus' production Classic code, not from
    Prime's own sources."""
    id_multipolys: list["CommandPolygon"] | list[dict[str, Any]] | None = None
    params: "CommandParams | dict[str, Any] | None" = None
    regions: list["Region"] | list[dict[str, Any]] | None = None
    pmap_version_id: str | None = None
    clean_all: bool = False
    spot_geometry: dict[str, Any] | None = None
    favorite_id: str | None = None
    initiator: str | None = None
    """NEW (session 25) -- confirmed from real mission history
    (chairstacker): wire key "initiator", observed values "cloud"
    (schedule-triggered) and "rmtApp" (manually triggered via the
    app). No @SerialName found -- property name directly. Left as
    optional/None instead of a guessed default value, since it's
    unclear what the server assumes when the field is missing."""

    def to_json(self) -> dict[str, Any]:
        """NEW (July 11, eighth session): id_multipolys/params/regions
        now accept either the bytecode-confirmed types
        (CommandPolygon/CommandParams/Region, see below in the module)
        or still raw dicts (backward compatibility/escape hatch for
        cases not covered by the typed models)."""
        body: dict[str, Any] = {
            "command": self.command_type.value,
            "robot_id": self.asset_id,
            "ordered": self.ordered,
            "select_all": self.clean_all,
        }
        if self.map_id is not None:
            body["p2map_id"] = self.map_id
        if self.id_multipolys is not None:
            body["id_multipolys"] = [
                p.to_json() if hasattr(p, "to_json") else p for p in self.id_multipolys
            ]
        if self.params is not None:
            body["params"] = self.params.to_json() if hasattr(self.params, "to_json") else self.params
        if self.regions is not None:
            body["regions"] = [r.to_json() if hasattr(r, "to_json") else r for r in self.regions]
        if self.pmap_version_id is not None:
            body["user_p2mapv_id"] = self.pmap_version_id
        if self.spot_geometry is not None:
            body["geom"] = self.spot_geometry
        if self.favorite_id is not None:
            body["favorite_id"] = self.favorite_id
        if self.initiator is not None:
            body["initiator"] = self.initiator
        return body

    def to_shadow_desired(self) -> dict[str, Any]:
        """Confirmed from CommandWrapper.java (@Serializable, one
        field, @SerialName("cmd")): this is what should end up in
        state.desired.cmd, if the envelope assumption (see module
        docstring) is correct -- NEVER confirmed live."""
        return {"cmd": self.to_json()}


# =========================================================================
# V1 edit commands (POST /v1/p2maps/{id}/versions) -- ACTUALLY THE
# ACTIVE PATH, not the V2 commands modeled above
# =========================================================================
#
# STATUS: NEW (July 11, fourth session, after a full re-decompilation
# of the app -- see PRIME_APP_GAP_ANALYSIS). Confirmed: EVERY single
# edit operation in the app code (room, zone, furniture, virtual wall)
# calls requestEditV1(). requestEditV2() is called NOT A SINGLE TIME
# anywhere in the entire app code -- only referenced in signatures.
# The V2 commands modeled above (SplitRoom, MergeRooms, etc. with
# to_command_body(), endpoint /v2/p2maps/{id}/versions) are built for
# a path the app itself doesn't use. They stay in the code, since
# /v2/... does at least exist (a dead path, not a made-up one), but
# edit_map() in rest_client.py uses V1 from now on.
#
# Field names confirmed via androguard bytecode inspection directly
# from the DEX (jadx failed on exactly this one class family -- all
# 56 of 56 decompilation errors of the ENTIRE app are located exactly
# here, no other single error in over 24,000 classes).
#
# IMPORTANT UNCERTAINTY: the exact envelope format (how the "command"
# discriminator gets onto the wire) is NOT confirmed.
# EditMapV1Request$Command$CommandSerializer is its own, custom
# serializer (not standard sealed-class polymorphism via
# kotlinx.serialization), whose logic couldn't be decompiled (not even
# via androguard -- that would need bytecode disassembly of the
# serializer method itself, not just field lists). The
# to_v1_command_body() shape built here ({"command": "<Name>",
# ...fields directly, no "params" nesting...}) is an analogy
# assumption from V2's confirmed pattern (there, however, "params" IS
# nested!) -- NOT a confirmed fact for V1. No @SerialName on a single
# one of the found fields -- wire keys presumably identical to the
# Kotlin property names, but that too could theoretically be
# overridden by the custom serializer.


@dataclass(frozen=True)
class RenameRoomV1:
    """Confirmed (fields) from EditMapV1Request$Command$RenameRoom via
    androguard: id (String), name (String)."""

    room_id: str
    name: str

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "RenameRoom", "id": self.room_id, "name": self.name}


@dataclass(frozen=True)
class SplitRoomV1:
    """Confirmed: id (String), splitPoints (List) -- unlike V2's
    SplitRoom (which takes a LineString geometry), a simple point list
    here. The exact meaning of "splitPoints" (two endpoints like V2?
    or more?) not further confirmed."""

    room_id: str
    split_points: list[Position]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "SplitRoom",
            "id": self.room_id,
            "splitPoints": [list(p) for p in self.split_points],
        }


@dataclass(frozen=True)
class MergeRoomsV1:
    """Confirmed: ids (List) -- field name "ids", not "roomIds"."""

    ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "MergeRooms", "ids": self.ids}


@dataclass(frozen=True)
class SetRoomTypeV1:
    """Confirmed: id (String), type (V1's OWN RoomType enum class --
    separate from the RoomType defined above, but with the same
    numeric values: NOT_RECOGNIZED, BEDROOM, DINING_ROOM, BATHROOM,
    HALLWAY, KITCHEN, LIVING_ROOM, BALCONY, OTHER -- the already-
    existing RoomType int enum is reused here)."""

    room_id: str
    room_type: RoomType

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "SetRoomType", "id": self.room_id, "type": int(self.room_type)}


@dataclass(frozen=True)
class SetRoomMetadataV1:
    """Confirmed: only field "metadata": P2MapRoomMetadata (the read
    model type, see the RoomInfo section above) -- calls the same
    structure as set_room_metadata (V2), but without "params" nesting
    and with the entire object under "metadata" instead of separate
    id/metadata fields."""

    room_id: str
    name: str | None = None
    room_type: RoomType | None = None

    def to_v1_command_body(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"id": self.room_id}
        if self.name is not None:
            metadata["name"] = self.name
        if self.room_type is not None:
            metadata["type"] = int(self.room_type)
        return {"command": "SetRoomMetadata", "metadata": metadata}


@dataclass(frozen=True)
class PermanentAreaV1:
    """Confirmed from EditMapV1Request$PermanentArea: geometry
    (Polygon), id (String), name (String)."""

    area_id: str
    name: str
    geometry: Polygon

    def to_json(self) -> dict[str, Any]:
        return {"id": self.area_id, "name": self.name, "geometry": self.geometry.to_geojson()}


@dataclass(frozen=True)
class SetPermanentAreasV1:
    """Confirmed: only field "areaPoints" (List) -- the name suggests
    point lists, but the field type (List<PermanentArea> vs. pure
    position lists) wasn't resolved on the bytecode side. Modeled here
    as a list of PermanentAreaV1 objects (the most plausible reading
    given the separately existing PermanentArea class), NOT confirmed."""

    areas: list[PermanentAreaV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "SetPermanentAreas", "areaPoints": [a.to_json() for a in self.areas]}


@dataclass(frozen=True)
class DeletePermanentAreasV1:
    """Confirmed: areaIDs (List)."""

    area_ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "DeletePermanentAreas", "areaIDs": self.area_ids}


@dataclass(frozen=True)
class VirtualWallLinearV1:
    """Confirmed from EditMapV1Request$VirtualWall$Linear: from/to
    (Position), id (String) -- a line segment, not a polygon."""

    wall_id: str
    from_pos: Position
    to_pos: Position

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "Linear",
            "id": self.wall_id,
            "from": list(self.from_pos),
            "to": list(self.to_pos),
        }


@dataclass(frozen=True)
class VirtualWallRectangleV1:
    """Confirmed from EditMapV1Request$VirtualWall$Rectangle: id
    (String), polygon (Polygon) -- despite the name "Rectangle",
    stored as a general polygon, no dedicated rectangle structure."""

    wall_id: str
    polygon: Polygon

    def to_json(self) -> dict[str, Any]:
        return {"type": "Rectangle", "id": self.wall_id, "polygon": self.polygon.to_geojson()}


@dataclass(frozen=True)
class VirtualWallNoMopZoneV1:
    """Confirmed from EditMapV1Request$VirtualWall$NoMopZone: id
    (String), polygon (Polygon). IMPORTANT FINDING: no-mop zones go
    through the same command type as virtual walls in V1
    (SetVirtualWalls), not through a dedicated command."""

    wall_id: str
    polygon: Polygon

    def to_json(self) -> dict[str, Any]:
        return {"type": "NoMopZone", "id": self.wall_id, "polygon": self.polygon.to_geojson()}


VirtualWallV1 = VirtualWallLinearV1 | VirtualWallRectangleV1 | VirtualWallNoMopZoneV1


@dataclass(frozen=True)
class SetVirtualWallsV1:
    """Confirmed: only field "walls" (list of VirtualWall subtypes).
    How the "type" discriminator of the three subtypes (Linear/
    Rectangle/NoMopZone) actually gets onto the wire is NOT confirmed
    (a dedicated VirtualWallSerializer was found, whose logic couldn't
    be decompiled) -- "type" key used here as the most plausible
    assumption."""

    walls: list[VirtualWallV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "SetVirtualWalls", "walls": [w.to_json() for w in self.walls]}


@dataclass(frozen=True)
class FurnitureItemV1:
    """Confirmed from EditMapV1Request$Furniture: geometry (Polygon), id
    (String), type (Int -- NOT String/Enum like V2's Furniture!),
    userModified (bool). Uses the existing FurnitureType int enum for
    the int value."""

    furniture_id: str
    furniture_type: FurnitureType
    geometry: Polygon
    user_modified: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.furniture_id,
            "type": int(self.furniture_type),
            "geometry": self.geometry.to_geojson(),
            "userModified": self.user_modified,
        }


@dataclass(frozen=True)
class AdjustFurnitureV1:
    """Confirmed from EditMapV1Request$Command$AdjustFurniture:
    furnitureList (List), packageInfo (List), timeStamp (long). A
    BATCH operation (multiple furniture items at once), unlike V2's
    SetFurniture (one item per call). Meaning of "packageInfo" not
    confirmed -- passed through here as a raw list."""

    furniture_list: list[FurnitureItemV1]
    package_info: list[dict[str, Any]] = field(default_factory=list)
    timestamp: int = 0

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "AdjustFurniture",
            "furnitureList": [f.to_json() for f in self.furniture_list],
            "packageInfo": self.package_info,
            "timeStamp": self.timestamp,
        }


MapEditCommandV1 = (
    RenameRoomV1
    | SplitRoomV1
    | MergeRoomsV1
    | SetRoomTypeV1
    | SetRoomMetadataV1
    | SetPermanentAreasV1
    | DeletePermanentAreasV1
    | SetVirtualWallsV1
    | AdjustFurnitureV1
)


# =========================================================================
# Favorites (FavoriteV1) -- POST/GET/PUT/DELETE /v1/user/favorites
# =========================================================================
#
# STATUS: NEW (July 11, fourth session). Field names and @SerialName
# values confirmed from com.irobot.data.restservices.favorites.
# datamodels.FavoriteV1 (@Serializable, cleanly decompiled). Important
# finding: commandDefs is a List<RoutineCommand> -- a favorite is
# therefore structurally nothing other than a named, stored list of
# mission commands (see RoutineCommand above). This matches the
# long-known "len(commanddefs) > 1" observation from the HA
# integration (FavoriteButton.async_press()).
#
# REST endpoints confirmed from FavoriteCommonRequest.java (base URL)
# and the three separately existing subclasses (Delete/Fetch/Order --
# HTTP method explicitly set there), plus CreateFavoriteRequest/
# UpdateFavoriteRequest (found later, eighth session -- see
# rest_client.py's create_favorite()/update_favorite() docstrings):
#   GET    /v1/user/favorites?app_edition=1                    (fetch, CONFIRMED)
#   POST   /v1/user/favorites?app_edition=1                     (create, CONFIRMED)
#   PUT    /v1/user/favorites/{favoriteId}?app_edition=1        (update, CONFIRMED)
#   DELETE /v1/user/favorites/{favoriteId}?app_edition=1        (delete, CONFIRMED)
#   PUT    /v1/user/favorites/{favoriteId}/order?app_edition=1  (order, CONFIRMED)
#
# app_edition=1 is a fixed query parameter (NotificationCenterConsts
# .NOTIFICATION_HELP_CONTENT_VERSION1 = "1"), not a user value.


class TimeEstimateConfidence(str, Enum):
    """Confirmed from TimeEstimateConfidence, complete list."""

    GOOD_CONFIDENCE = "GOOD_CONFIDENCE"
    POOR_CONFIDENCE = "POOR_CONFIDENCE"
    PARTIAL_CONFIDENCE = "PARTIAL_CONFIDENCE"


class TimeEstimateTimeUnit(str, Enum):
    """Confirmed from TimeEstimateTimeUnit -- both singular and plural
    forms exist as their own values (not an error correction on my
    part, that's how it is in the source code)."""

    HOUR = "hour"
    HOURS = "hours"
    MINUTE = "minute"
    MINUTES = "minutes"
    SECOND = "second"
    SECONDS = "seconds"


@dataclass(frozen=True)
class FavoriteTimeEstimate:
    """Confirmed via androguard bytecode inspection (the base class
    itself wasn't emitted by jadx, no error reported for it -- a
    similar silent failure as with the createFavorite/updateFavorite
    lambdas): confidence (TimeEstimateConfidence), estimate (double),
    unit (TimeEstimateTimeUnit). No @SerialName deviation found for
    the field names themselves -- presumably serialized directly
    under their property name."""

    estimate: float
    unit: TimeEstimateTimeUnit
    confidence: TimeEstimateConfidence

    def to_json(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "unit": self.unit.value,
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class FavoriteV1:
    """Confirmed from FavoriteV1.java (@Serializable, cleanly
    decompiled). Field name mapping from the @SerialName annotations:
      commandDefs -> "commanddefs" (List<RoutineCommand> -- see
      above), creationTimestamp -> "creation_timestamp",
      displayOrder -> "display_order", favoriteId -> "favorite_id",
      lastModified -> "last_modified",
      lastUserModified -> "last_user_modified",
      modificationSecs -> "modification_secs",
      timeEstimates -> "time_estimates", isDefault -> "default",
      isDeleted -> "deleted", isHidden -> "hidden". color/icon/name/
      order/version have NO dedicated @SerialName -- property name
      taken directly."""

    name: str | None = None
    color: str | None = None
    icon: str | None = None
    order: str | None = None
    display_order: int | None = None
    is_default: bool = False
    is_deleted: bool = False
    is_hidden: bool = False
    modification_secs: str | None = None
    version: str | None = None
    command_defs: list[RoutineCommand] = field(default_factory=list)
    creation_timestamp: int | None = None
    last_user_modified: int | None = None
    last_modified: int | None = None
    time_estimates: list[FavoriteTimeEstimate] | None = None
    favorite_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "default": self.is_default,
            "deleted": self.is_deleted,
            "hidden": self.is_hidden,
            "commanddefs": [c.to_json() for c in self.command_defs],
        }
        if self.name is not None:
            body["name"] = self.name
        if self.color is not None:
            body["color"] = self.color
        if self.icon is not None:
            body["icon"] = self.icon
        if self.order is not None:
            body["order"] = self.order
        if self.display_order is not None:
            body["display_order"] = self.display_order
        if self.modification_secs is not None:
            body["modification_secs"] = self.modification_secs
        if self.version is not None:
            body["version"] = self.version
        if self.creation_timestamp is not None:
            body["creation_timestamp"] = self.creation_timestamp
        if self.last_user_modified is not None:
            body["last_user_modified"] = self.last_user_modified
        if self.last_modified is not None:
            body["last_modified"] = self.last_modified
        if self.time_estimates is not None:
            body["time_estimates"] = [t.to_json() for t in self.time_estimates]
        return body


# =========================================================================
# Unpacking map bundles (tar.gz -> raw per-type JSON lists)
# =========================================================================
#
# STATUS: NEW (July 11, fifth session). Closes part of C2/C3 (see
# PRIME_APP_GAP_ANALYSIS): previously there was a way to get the
# presigned download URL (get_map_geojson_link()) and a way to
# download the bytes (download_map_bundle()), but nothing that
# actually unpacks the tar.gz archive and assigns meaning to the
# individual files.
#
# 11 of 15 known info-type discriminators confirmed (P2MapInfoType
# constants from the source code): "rooms", "borders", "floorPlan",
# "dockPoses", "floorTypes", "coverage", "cleanZones", "hazard",
# "trajectories", "adHocCleanZones", "furniture". FOUR ARE MISSING
# ("keepOutZones"/"noMopZones"/"virtualWalls"/"thresholds" have no
# dedicated P2MapInfoType field found in the corresponding classes --
# presumably embedded differently, e.g. under a shared "zones"
# discriminator, not further investigated).
#
# IMPORTANT UNCERTAINTY: what the files are actually NAMED inside the
# tar.gz (e.g. "rooms.json" vs. "rooms" vs. something completely
# different) is NOT confirmed -- P2MapBundleContentHolder/
# P2MapInfoFactory (the classes that open the archive) weren't
# examined in detail. parse_map_bundle() below assumes the filename
# (without extension) directly matches one of the discriminators
# above -- a plausible, but untested, assumption. Never tried against
# a real archive.


KNOWN_BUNDLE_INFO_TYPES = frozenset({
    "rooms", "borders", "floorPlan", "dockPoses", "floorTypes",
    "coverage", "cleanZones", "hazard", "trajectories",
    "adHocCleanZones", "furniture",
})


def parse_map_bundle(data: bytes) -> dict[str, Any]:
    """Unpacks a tar.gz archive loaded via download_map_bundle().

    Returns {filename_without_extension: parsed_content} --
    parsed_content is raw JSON (dict or list) if the file was readable
    as JSON, otherwise the raw text, otherwise the raw bytes (if
    neither text nor JSON -- e.g. an image or binary format inside the
    archive that wasn't further investigated).

    Deliberately NO automatic conversion into the RoomInfo/BorderInfo/
    etc. dataclasses above -- the exact JSON field format within each
    file isn't confirmed (only the Kotlin class fields are), an
    automatic mapping could silently make wrong assumptions. Callers
    who want access to the typed models need to convert the raw dicts
    here into RoomInfo(**...) or similar themselves, keeping their own
    uncertainty in mind."""
    result: dict[str, Any] = {}
    with tarfile.open(fileobj=BytesIO(data), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            raw = extracted.read()
            # filename without directory path and without extension as the key
            key = member.name.rsplit("/", 1)[-1]
            if "." in key:
                key = key.rsplit(".", 1)[0]
            try:
                result[key] = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                try:
                    result[key] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    result[key] = raw
    return result


# =========================================================================
# Schedules (households/settings/schedule)
# =========================================================================
#
# STATUS: NEW (July 11, seventh session). ScheduleOptions/HouseholdSchedule/
# HouseholdScheduleUpdate/ScheduleTime do NOT exist in the jadx output tree
# -- as with EditMapV1Request and the Favorite create/update lambdas,
# jadx silently skipped them, WITHOUT showing this in the error count.
# All fields below confirmed directly from the DEX via androguard.
# ScheduleDateEntry and ScheduleFrequency, in contrast, decompiled
# normally (jadx source, @SerialName directly visible).


class ScheduleFrequency(str, Enum):
    """Confirmed (jadx source, @SerialName per value, identical to the
    enum name): only 4 values, no DAILY."""

    BI_WEEKLY = "BI_WEEKLY"
    MONTHLY = "MONTHLY"
    ONCE = "ONCE"
    WEEKLY = "WEEKLY"


@dataclass(frozen=True)
class ScheduleTime:
    """Confirmed (androguard): day (List -- weekdays, the list content
    type not resolvable via the bytecode field signature, presumably
    int or string abbreviation like "MO"/"TU"), hour (Integer), min
    (Integer)."""

    day: list[Any] = field(default_factory=list)
    hour: int | None = None
    min: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"day": self.day}
        if self.hour is not None:
            body["hour"] = self.hour
        if self.min is not None:
            body["min"] = self.min
        return body


@dataclass(frozen=True)
class ScheduleDateEntry:
    """Confirmed (jadx source, @SerialName per field, identical to the
    property name): dayOfMonth, hour, min, month, year -- used for
    ScheduleOptions.after/until (start/end date of a schedule)."""

    day_of_month: int | None = None
    hour: int | None = None
    min: int | None = None
    month: int | None = None
    year: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.day_of_month is not None:
            body["dayOfMonth"] = self.day_of_month
        if self.hour is not None:
            body["hour"] = self.hour
        if self.min is not None:
            body["min"] = self.min
        if self.month is not None:
            body["month"] = self.month
        if self.year is not None:
            body["year"] = self.year
        return body


@dataclass(frozen=True)
class ScheduleOptions:
    """Confirmed (androguard, all 17 fields -- no @SerialName found,
    wire keys presumably = Kotlin property name directly): assetId,
    name, frequency, start/end (ScheduleTime), after/until
    (ScheduleDateEntry), commands/endCommands/append/exclude (lists),
    createdTime, deleted, enabled, forceCloud, reminder.

    UNCERTAINTY: commands/endCommands are only recognizable as "List"
    via the raw bytecode field signature (Java generics type erasure
    at runtime) -- modeled here as List[RoutineCommand], in strong
    analogy to FavoriteV1.command_defs (the same pattern: a schedule
    triggers a RoutineCommand when it fires), but NOT directly
    confirmed via a generic signature. append/exclude similarly
    uncertain (content unknown, left here as a raw list)."""

    asset_id: str | None = None
    name: str | None = None
    frequency: ScheduleFrequency | None = None
    start: ScheduleTime | None = None
    end: ScheduleTime | None = None
    after: ScheduleDateEntry | None = None
    until: ScheduleDateEntry | None = None
    commands: list[RoutineCommand] = field(default_factory=list)
    end_commands: list[RoutineCommand] = field(default_factory=list)
    append: list[Any] = field(default_factory=list)
    exclude: list[Any] = field(default_factory=list)
    created_time: str | None = None
    deleted: bool | None = None
    enabled: bool | None = None
    force_cloud: bool | None = None
    reminder: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.asset_id is not None:
            body["assetId"] = self.asset_id
        if self.name is not None:
            body["name"] = self.name
        if self.frequency is not None:
            body["frequency"] = self.frequency.value
        if self.start is not None:
            body["start"] = self.start.to_json()
        if self.end is not None:
            body["end"] = self.end.to_json()
        if self.after is not None:
            body["after"] = self.after.to_json()
        if self.until is not None:
            body["until"] = self.until.to_json()
        if self.commands:
            body["commands"] = [c.to_json() for c in self.commands]
        if self.end_commands:
            body["endCommands"] = [c.to_json() for c in self.end_commands]
        if self.append:
            body["append"] = self.append
        if self.exclude:
            body["exclude"] = self.exclude
        if self.created_time is not None:
            body["createdTime"] = self.created_time
        if self.deleted is not None:
            body["deleted"] = self.deleted
        if self.enabled is not None:
            body["enabled"] = self.enabled
        if self.force_cloud is not None:
            body["forceCloud"] = self.force_cloud
        if self.reminder is not None:
            body["reminder"] = self.reminder
        return body


@dataclass(frozen=True)
class HouseholdSchedule:
    """Confirmed (androguard): scheduleId (String), options
    (ScheduleOptions). Used per SchedulesAPI for updateSchedules()
    (List<HouseholdSchedule>)."""

    schedule_id: str
    options: ScheduleOptions

    def to_json(self) -> dict[str, Any]:
        return {"scheduleId": self.schedule_id, "options": self.options.to_json()}


@dataclass(frozen=True)
class HouseholdScheduleUpdate:
    """Confirmed (androguard): identical field shape to
    HouseholdSchedule (scheduleId, options) -- a separate class exists
    in the bytecode, presumably for a more specific update context, but
    the distinction from HouseholdSchedule wasn't further resolved."""

    schedule_id: str
    options: ScheduleOptions

    def to_json(self) -> dict[str, Any]:
        return {"scheduleId": self.schedule_id, "options": self.options.to_json()}



# =========================================================================
# CommandParams/Region/CommandPolygon (com.irobot.data.missioncommand.datamodels)
# =========================================================================
#
# STATUS: NEW (July 11, eighth session). This entire class family was missing
# from the jadx output tree -- silently skipped like EditMapV1Request/
# ScheduleOptions. Found systematically through a full comparison of ALL
# com.irobot.* classes in the DEX against the jadx output tree (6755 missing
# classes total, overwhelmingly UI layer/Compose screens -- this mission
# command subgroup is the only part relevant to the library). Closes an
# item that had been open since the first session: RoutineCommand.
# params/regions/id_multipolys used to be raw dicts.
#
# No @SerialName on a single field found -- wire keys presumably = Kotlin
# property name directly (same pattern as EditMapV1Request/ScheduleOptions).


class RegionType(str, Enum):
    """REVISED (session 25): the actual wire values are LOWERCASE
    ("rid"/"zid"), confirmed by real mission history data
    (chairstacker, cmd.regions[].type). The original androguard
    reading (RID/TID/ZID, uppercase) correctly read the enum CONSTANT
    NAMES from the bytecode, but the actual serialization seems to
    lowercase them -- either a @SerialName annotation not found on the
    first scan, or automatic lowercasing in the serializer. Python
    member names stay uppercase (convention), only the VALUES were
    adjusted. "tid" remains unconfirmed (no TID seen in real data --
    only RID and ZID occurred)."""

    RID = "rid"
    TID = "tid"
    ZID = "zid"


@dataclass(frozen=True)
class PadWetnessParam:
    """Confirmed (androguard): NOT an enum (super = Object), but a
    class with three predefined constant instances (Damp, Moderate,
    Wet) and three int fields (disposable, padPlate, reusable) --
    presumably a different wetness-level encoding per pad type. Exact
    values per constant not readable from the bytecode field list
    (only field names/types, no static values) -- left as placeholder
    presets with None, NOT guessed."""

    disposable: int | None = None
    pad_plate: int | None = None
    reusable: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.disposable is not None:
            body["disposable"] = self.disposable
        if self.pad_plate is not None:
            body["padPlate"] = self.pad_plate
        if self.reusable is not None:
            body["reusable"] = self.reusable
        return body

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PadWetnessParam:
        """NEW (session 32) -- confirmed from a real get_settings()
        response (chairstacker): {"disposable": 3, "reusable": 1,
        "padPlate": 1}."""
        return cls(
            disposable=data.get("disposable"),
            pad_plate=data.get("padPlate"),
            reusable=data.get("reusable"),
        )


class CleaningMode(str, Enum):
    """Confirmed (androguard, MissionPreferenceValue$CleaningMode):
    5 values. Each also has a numeric "mode" field and a "uid" -- only
    the names as an enum here, the numeric codes weren't readable
    from the bytecode field list (only field types, no static
    values)."""

    MOP = "Mop"
    MOPPING = "Mopping"
    VAC_THEN_MOP = "VacThenMop"
    VACUUM = "Vacuum"
    VACUUM_AND_MOP = "VacuumAndMop"


class CleaningPasses(str, Enum):
    """Confirmed (androguard, MissionPreferenceValue$CleaningPasses):
    only 2 values."""

    DOUBLE = "Double"
    SINGLE = "Single"


class LiquidAmountLevel(str, Enum):
    """Confirmed (androguard, MissionPreferenceValue$LiquidAmount AND
    $ComboLiquidAmount -- both have identical 3 values High/Low/Normal,
    merged here since structurally identical)."""

    HIGH = "High"
    LOW = "Low"
    NORMAL = "Normal"


class SoftwareScrub(str, Enum):
    """Confirmed (androguard, MissionPreferenceValue$SoftwareScrub)."""

    OFF = "Off"
    ON = "On"


class VacuumPowerLevel(str, Enum):
    """Confirmed (androguard, MissionPreferenceValue$VacuumPower): 4
    values (more than CleaningMode etc.)."""

    HIGH = "High"
    LOW = "Low"
    NORMAL = "Normal"
    QUIET = "Quiet"


class MissionPreferenceSwitcherType(str, Enum):
    """Confirmed (androguard, MissionPreferenceType$Switcher): 4 values."""

    CAREFUL_DRIVE = "CarefulDrive"
    EDGE_CLEAN = "EdgeClean"
    OBSTACLE_DETECTION = "ObstacleDetection"
    PAD_WASH_AFTER = "PadWashAfter"


@dataclass(frozen=True)
class MissionPreferenceSwitcher:
    """Confirmed (androguard, MissionPreference$Switcher): isOn (Bool),
    type (MissionPreferenceType.Switcher)."""

    preference_type: MissionPreferenceSwitcherType
    is_on: bool

    def to_json(self) -> dict[str, Any]:
        return {"type": self.preference_type.value, "isOn": self.is_on}


@dataclass(frozen=True)
class MissionPreferenceSelector:
    """Confirmed (androguard, MissionPreference$Selector): possibleValues
    (List), selected (Int -- index into possibleValues), type
    (MissionPreferenceType.Selector). MissionPreferenceType.Selector
    itself is NOT an enum (has a Function0 "knownValues" field) --
    more dynamic/open than the Switcher variant, so "type" is left
    here as a raw string instead of prescribing a possibly wrong
    closed enum list."""

    preference_type: str
    possible_values: list[Any] = field(default_factory=list)
    selected: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"type": self.preference_type, "possibleValues": self.possible_values, "selected": self.selected}


@dataclass(frozen=True)
class CommandPolygonMetadata:
    """Confirmed (androguard): only field furnitureId (Int)."""

    furniture_id: int

    def to_json(self) -> dict[str, Any]:
        return {"furnitureId": self.furniture_id}


@dataclass(frozen=True)
class CommandPolygon:
    """Confirmed (androguard): id (String), metadata
    (CommandPolygonMetadata), poly (List -- presumably a list of
    positions, type not resolvable via the bytecode field signature
    due to generics type erasure, assumed here as List[Position] by
    analogy to all other polygon-like structures in this file)."""

    polygon_id: str
    poly: list[Position] = field(default_factory=list)
    metadata: CommandPolygonMetadata | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"id": self.polygon_id, "poly": [list(p) for p in self.poly]}
        if self.metadata is not None:
            body["metadata"] = self.metadata.to_json()
        return body


@dataclass(frozen=True)
class CommandParams:
    """Confirmed (androguard): ALL 37 fields directly from
    CommandParams's DEX field list, each optional (boxed
    Integer/Boolean in Kotlin = all nullable). This is the complete
    parameter surface for a mission command -- covers suction power
    (suctionLevel), pad wetness (padWetness), carpet boost
    (carpetBoost), room confinement (roomConfine), timebox
    (timeboxMinutes), drive speed for steering commands
    (velocityLeft/velocityRight) and many more. Meaning of some more
    cryptic individual fields (noKOZ, odoaMode, rankOverlap,
    gentleMode) not further investigated -- field names carried over
    1:1 from the bytecode."""

    adaptive_cleaning: bool | None = None
    bin_pause: bool | None = None
    capture_mode: int | None = None
    carpet_boost: bool | None = None
    clean_score_id: str | None = None
    cleaning_profile: str | None = None
    eco_charge: bool | None = None
    execute_in_place: bool | None = None
    gentle_mode: int | None = None
    heated_water: int | None = None
    manual_update: bool | None = None
    monitor_mode: int | None = None
    no_koz: int | None = None
    no_auto_passes: bool | None = None
    """NEW (session 27) -- confirmed from real data: embedded in
    get_state()'s cleanSchedule2[].cmdStr (a string-serialized,
    Python-repr-like object, not direct JSON -- an unusual place to
    find it). Wire key "noAutoPasses", observed value true."""
    no_persistent_pass: bool | None = None
    odoa_mode: int | None = None
    open_only: bool | None = None
    operating_mode: int | None = None
    """NEW (session 25) -- confirmed from real mission history
    (chairstacker), wire key "operatingMode". Observed values: 2, 32
    -- meaning not further investigated (presumably an operating-mode
    bit pattern, similar to cap.oMode from get_state())."""
    pad_wash_after: int | None = None
    pad_wash_area: int | None = None
    pad_wetness: PadWetnessParam | None = None
    rank_overlap: int | None = None
    replay_of: str | None = None
    routine_type: str | None = None
    """NEW (session 26) -- confirmed from real room_metadata data
    (chairstacker), observed together with replay_of (value "REPLAY").
    Presumably the discriminator value indicating that this parameter
    set comes from a repeated earlier mission rather than a new
    configuration."""
    room_confine: bool | None = None
    rotate: int | None = None
    routine_modified: bool | None = None
    schedule_hold: bool | None = None
    scrub: int | None = None
    """CORRECTED (session 25): the real wire key is "swScrub", not
    "scrub" -- confirmed from real mission history (chairstacker,
    cmd.regions[].params.swScrub). The original "scrub" key was a
    bytecode guess without strong confirmation (see class docstring:
    "more cryptic fields not further investigated"). Python attribute
    name stays "scrub" (no API change for callers), only the wire key
    in to_json()/from_json() was corrected."""
    smart_clean_id: str | None = None
    speed: int | None = None
    stream_on_route: bool | None = None
    suction_level: int | None = None
    timebox_minutes: int | None = None
    translate: int | None = None
    two_pass: bool | None = None
    vac_high: bool | None = None
    velocity_left: int | None = None
    velocity_right: int | None = None

    def to_json(self) -> dict[str, Any]:
        """Only set (non-None) fields are included, under their
        Kotlin property name (camelCase 1:1)."""
        raw = {
            "adaptiveCleaning": self.adaptive_cleaning,
            "binPause": self.bin_pause,
            "captureMode": self.capture_mode,
            "carpetBoost": self.carpet_boost,
            "cleanScoreId": self.clean_score_id,
            "profile": self.cleaning_profile,
            "ecoCharge": self.eco_charge,
            "executeInPlace": self.execute_in_place,
            "gentleMode": self.gentle_mode,
            "heatedWater": self.heated_water,
            "manualUpdate": self.manual_update,
            "monitorMode": self.monitor_mode,
            "noKOZ": self.no_koz,
            "noAutoPasses": self.no_auto_passes,
            "noPersistentPass": self.no_persistent_pass,
            "odoaMode": self.odoa_mode,
            "openOnly": self.open_only,
            "operatingMode": self.operating_mode,
            "padWashAfter": self.pad_wash_after,
            "padWashArea": self.pad_wash_area,
            "padWetness": self.pad_wetness.to_json() if self.pad_wetness is not None else None,
            "rankOverlap": self.rank_overlap,
            "replay_of": self.replay_of,
            "routine_type": self.routine_type,
            "roomConfine": self.room_confine,
            "rotate": self.rotate,
            "routineModified": self.routine_modified,
            "scheduleHold": self.schedule_hold,
            "swScrub": self.scrub,
            "smartCleanId": self.smart_clean_id,
            "speed": self.speed,
            "streamOnRoute": self.stream_on_route,
            "suctionLevel": self.suction_level,
            "timeboxMinutes": self.timebox_minutes,
            "translate": self.translate,
            "twoPass": self.two_pass,
            "vacHigh": self.vac_high,
            "velocityLeft": self.velocity_left,
            "velocityRight": self.velocity_right,
        }
        return {k: v for k, v in raw.items() if v is not None}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CommandParams:
        """NEW (July 11, ninth session) -- inverse function of
        to_json(), for response models like CleaningProfile that
        contain CommandParams. pad_wetness is deliberately not
        automatically built from nested JSON (PadWetnessParam.from_json()
        didn't exist yet -- the three fields are simple enough to read
        directly inline here)."""
        pad_wetness_data = data.get("padWetness")
        pad_wetness = None
        if pad_wetness_data:
            pad_wetness = PadWetnessParam(
                disposable=pad_wetness_data.get("disposable"),
                pad_plate=pad_wetness_data.get("padPlate"),
                reusable=pad_wetness_data.get("reusable"),
            )
        return cls(
            adaptive_cleaning=data.get("adaptiveCleaning"),
            bin_pause=data.get("binPause"),
            capture_mode=data.get("captureMode"),
            carpet_boost=data.get("carpetBoost"),
            clean_score_id=data.get("cleanScoreId"),
            cleaning_profile=data.get("profile"),
            eco_charge=data.get("ecoCharge"),
            execute_in_place=data.get("executeInPlace"),
            gentle_mode=data.get("gentleMode"),
            heated_water=data.get("heatedWater"),
            manual_update=data.get("manualUpdate"),
            monitor_mode=data.get("monitorMode"),
            no_koz=data.get("noKOZ"),
            no_auto_passes=data.get("noAutoPasses"),
            no_persistent_pass=data.get("noPersistentPass"),
            odoa_mode=data.get("odoaMode"),
            open_only=data.get("openOnly"),
            operating_mode=data.get("operatingMode"),
            pad_wash_after=data.get("padWashAfter"),
            pad_wash_area=data.get("padWashArea"),
            pad_wetness=pad_wetness,
            rank_overlap=data.get("rankOverlap"),
            replay_of=data.get("replay_of"),
            routine_type=data.get("routine_type"),
            room_confine=data.get("roomConfine"),
            rotate=data.get("rotate"),
            routine_modified=data.get("routineModified"),
            schedule_hold=data.get("scheduleHold"),
            scrub=data.get("swScrub"),
            smart_clean_id=data.get("smartCleanId"),
            speed=data.get("speed"),
            stream_on_route=data.get("streamOnRoute"),
            suction_level=data.get("suctionLevel"),
            timebox_minutes=data.get("timeboxMinutes"),
            translate=data.get("translate"),
            two_pass=data.get("twoPass"),
            vac_high=data.get("vacHigh"),
            velocity_left=data.get("velocityLeft"),
            velocity_right=data.get("velocityRight"),
        )


@dataclass(frozen=True)
class Region:
    """Confirmed (androguard): id (String), name (String), params
    (CommandParams), type (RegionType). Replaces the previous
    raw-dict element in RoutineCommand.regions.

    CORRECTED/ADDED (session 27): from_json() was completely missing
    until now (Region was only built for sending). Real mission
    history data (chairstacker) shows the key "region_id" when
    READING, not "id" as in to_json() when SENDING -- possibly two
    different wire forms for the same purpose (command echo in the
    history vs. its own send form), so both are accepted here,
    "region_id" tried first."""

    region_id: str
    region_type: RegionType
    name: str | None = None
    params: CommandParams | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"id": self.region_id, "type": self.region_type.value}
        if self.name is not None:
            body["name"] = self.name
        if self.params is not None:
            body["params"] = self.params.to_json()
        return body

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Region:
        params_data = data.get("params")
        return cls(
            region_id=data.get("region_id") or data.get("id", ""),
            region_type=_enum_or_none(RegionType, data.get("type")) or RegionType.RID,
            name=data.get("name"),
            params=CommandParams.from_json(params_data) if params_data else None,
        )


# =========================================================================
# Mission history response models (com.irobot.data.restservices.missionhistory)
# =========================================================================
#
# STATUS: NEW (July 11, ninth session). Like the missioncommand family
# before: completely missing from the jadx output tree, found via the
# systematic DEX comparison. get_mission_history() used to return raw
# JSON -- now there's parse_mission_history_entry() for the top-level
# fields.
#
# UPDATE (session 18): the original effort limit on the 20
# MissionTimelineEvent sub-event types was lifted -- all 20 are now
# typed (see MissionTimelineEvent further below in this file, after
# MissionHistoryEntry). timeline is therefore no longer a raw dict
# structure, but list[MissionTimelineEvent] via parse_mission_timeline().


class DoneCode(str, Enum):
    """REVISED (session 27): real mission history (chairstacker) shows
    "ok" (lowercase) as the done_code value -- not "OK" as originally
    derived from androguard bytecode constant names. Exactly the same
    pattern as RegionType (see its docstring): bytecode constant names
    are uppercase, actual wire serialization seems to consistently
    lowercase. ONLY "ok" is directly confirmed -- the other 18 values
    were changed along with it following the same pattern (consistent
    lowercasing more likely than mixed case within one enum), but NOT
    individually confirmed. If any turn out to be wrong, please
    correct them individually once real data with that specific error
    code is available. `_enum_or_none()` catches any non-matching
    value anyway and returns the raw string instead of crashing."""

    BATTERY = "battery"
    BATTERY_CANCEL = "battery_cancel"
    BUSY = "busy"
    CANCEL = "cancel"
    DND_END = "dnd_end"
    EMPTY = "empty"
    FULL = "full"
    INCOMPLETE = "incomplete"
    NONE_ = "none"
    OK = "ok"
    PLACE_DOCK = "place_dock"
    RETURN_HOME_END = "return_home_end"
    SCHEDULE_ERROR = "schedule_error"
    STUCK = "stuck"
    TIMEBOX_END = "timebox_end"
    USER_END = "user_end"
    USER_REBOOT = "user_reboot"
    USER_SLEEP = "user_sleep"
    USER_SPOT = "user_spot"


class PadCategory(str, Enum):
    """Confirmed (androguard): 7 values."""

    DRY = "DRY"
    INVALID = "INVALID"
    NO_PAD = "NO_PAD"
    PLATE = "PLATE"
    REUSABLE_DRY = "REUSABLE_DRY"
    REUSABLE_WET = "REUSABLE_WET"
    WET = "WET"


class RankOverlap(str, Enum):
    """Confirmed (androguard): 3 values."""

    DEEP_CLEAN = "DEEP_CLEAN"
    DETAIL_CLEAN = "DETAIL_CLEAN"
    EXTENDED_CLEAN = "EXTENDED_CLEAN"


class CoverageStrategy(str, Enum):
    """Confirmed (androguard): 3 values."""

    HYBRID_COVERAGE_PLANNER = "HYBRID_COVERAGE_PLANNER"
    RESERVED = "RESERVED"
    ROOM_SEGMENTATION = "ROOM_SEGMENTATION"


def _enum_or_none(enum_cls: type, value: Any) -> Any:
    """Helper function: returns enum_cls(value) if possible, otherwise
    the raw value (instead of raising a ValueError) -- the server may
    introduce new values that this library version doesn't know yet."""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return value


@dataclass(frozen=True)
class MissionCommandRecord:
    """CORRECTED (session 27): mapId/mapVersionId had been wrongly
    guessed, confirmed wrong by real mission history (chairstacker) --
    the real field names are p2map_id and user_p2mapv_id (the latter
    sometimes null). cleanAll was never observed in the available real
    examples (neither present nor disproven) -- field name left
    unchanged, since not confirmed wrong. regions is now typed via
    Region.from_json() instead of a raw list, since the structure
    (params/region_id/type) is now known -- params within it are
    CommandParams-shaped.

    ADDED (session 30): a dedicated, TOP-LEVEL "params" field was
    completely missing -- separate from regions[].params, sometimes
    set (e.g. {"profile": "light"}), sometimes explicitly null.
    Overlooked, even though the data had been available for a long
    time."""

    clean_all: bool | None = None
    command: str | None = None
    initiator: str | None = None
    map_id: str | None = None
    map_version_id: str | None = None
    ordered: int | None = None
    params: CommandParams | None = None
    regions: list["Region"] = field(default_factory=list)
    robot_id: str | None = None
    time: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionCommandRecord:
        params_data = data.get("params")
        return cls(
            clean_all=data.get("cleanAll"),
            command=data.get("command"),
            initiator=data.get("initiator"),
            map_id=data.get("p2map_id") or data.get("mapId"),
            map_version_id=data.get("user_p2mapv_id") or data.get("mapVersionId"),
            ordered=data.get("ordered"),
            params=CommandParams.from_json(params_data) if params_data else None,
            regions=[Region.from_json(r) for r in (data.get("regions") or [])],
            robot_id=data.get("robot_id") or data.get("robotId"),
            time=data.get("time"),
        )


@dataclass(frozen=True)
class MissionHistoryEntry:
    """Confirmed (androguard, MissionHistory): top-level fields of the
    mission history response. `timeline` deliberately remains raw JSON
    -- see module docstring for the effort limit on the 20 sub-event
    types. Not all 30+ bytecode fields were included here -- focus on
    the ones most useful for evaluation (times, doneCode, error code,
    area coverage); less commonly used fields (wifiChannel,
    startEndWlBars, etc.) remain accessible via `raw`."""

    mission_id: str | None = None
    robot_id: str | None = None
    start_time: int | None = None
    timestamp: int | None = None
    duration_m: int | None = None
    minutes_running: int | None = None
    minutes_paused: int | None = None
    minutes_charging: int | None = None
    minutes_done: int | None = None
    done_code: DoneCode | str | None = None
    done_raw: str | None = None
    error_code: int | None = None
    square_feet_covered: int | None = None
    number_of_evacuations: int | None = None
    number_of_dirt_detects: int | None = None
    docked_at_start: bool | None = None
    ended_on_dock: int | None = None
    command: MissionCommandRecord | None = None
    static_map_id: str | None = None
    coverage_strategy: CoverageStrategy | str | None = None
    rank_overlap: RankOverlap | str | None = None
    pad_category: PadCategory | str | None = None
    timeline: list["MissionTimelineEvent"] = field(default_factory=list)
    """NEW (session 18) -- all 20 sub-event types now typed, see
    MissionTimelineEvent further below in this file."""
    raw: dict[str, Any] = field(default_factory=dict)
    """The complete, unchanged server response for this element -- for
    all fields not individually included above."""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionHistoryEntry:
        """CORRECTED (session 27): almost all field names had been
        wrongly guessed (camelCase assumptions), confirmed wrong by a
        complete, real response (chairstacker). The actual fields are
        mostly short abbreviations, some snake_case: robot_id (not
        robotId), runM (not minutesRunning), pauseM (not
        minutesPaused), chrgM (not minutesCharging), doneM (not
        minutesDone), sqft (not squareFeetCovered), evacs (not
        numberOfEvacuations), eDock (not endedOnDock), cmd (not
        command), done_raw (not doneRaw, AND with an underscore).
        "done" (short) and "done_raw" seem to carry the same value
        twice (e.g. both "ok") -- done_code now reads "done", not the
        never-observed "doneCode". errorCode/numberOfDirtDetects/
        staticMapId/rankOverlap/padCategory/coverageStrategy remained
        unobserved in the available example data (no error or
        multi-map cases among them) -- field names for these
        deliberately NOT changed, since it's unconfirmed whether the
        original guess happened to be right there or not; if that
        turns out to be wrong, another real example case with an
        actual error would be needed."""
        command_data = data.get("cmd") or data.get("command")
        timeline_data = data.get("timeline") or {}
        coverage_strategy = (timeline_data or {}).get("coverageStrategy")
        timeline_events = (
            timeline_data.get("finEvents") if isinstance(timeline_data, dict) else timeline_data
        )
        # CORRECTED (session 31): "events" didn't exist at all in real
        # data -- the rich sub-events are under "finEvents", a
        # separate, sparse "event" list (just type+ts) exists
        # alongside it and is deliberately NOT used here (contains no
        # additional information compared to finEvents).
        return cls(
            mission_id=data.get("missionId"),
            robot_id=data.get("robot_id"),
            start_time=data.get("startTime"),
            timestamp=data.get("timestamp"),
            duration_m=data.get("durationM"),
            minutes_running=data.get("runM"),
            minutes_paused=data.get("pauseM"),
            minutes_charging=data.get("chrgM"),
            minutes_done=data.get("doneM"),
            done_code=_enum_or_none(DoneCode, data.get("done")),
            done_raw=data.get("done_raw"),
            error_code=data.get("errorCode"),
            square_feet_covered=data.get("sqft"),
            number_of_evacuations=data.get("evacs"),
            number_of_dirt_detects=data.get("numberOfDirtDetects"),
            docked_at_start=data.get("dockedAtStart"),
            ended_on_dock=data.get("eDock"),
            command=MissionCommandRecord.from_json(command_data) if command_data else None,
            static_map_id=data.get("staticMapId"),
            coverage_strategy=_enum_or_none(CoverageStrategy, coverage_strategy),
            rank_overlap=_enum_or_none(RankOverlap, data.get("rankOverlap")),
            pad_category=_enum_or_none(PadCategory, data.get("padCategory")),
            timeline=parse_mission_timeline(timeline_events),
            raw=data,
        )


def parse_mission_history(data: dict[str, Any] | list[dict[str, Any]]) -> list[MissionHistoryEntry]:
    """Converts the raw get_mission_history() response into a list of
    typed MissionHistoryEntry objects. NEW (July 11, ninth session).
    Accepts either a raw list or a dict with an enclosing key (response
    envelope shape not confirmed -- so both forms are tolerated:
    {"missions": [...]} or directly [...])."""
    if isinstance(data, dict):
        entries = data.get("missions") or data.get("history") or []
    else:
        entries = data
    return [MissionHistoryEntry.from_json(e) for e in entries]


# =========================================================================
# CleaningProfile / DNDStatusResponse / HouseholdSetting / Routine defaults
# =========================================================================
#
# STATUS: NEW (July 11, ninth session). As above: missing from the
# jadx output tree, found via DEX comparison.


class CleaningProfileType(str, Enum):
    """Confirmed (androguard, CleaningProfile$ProfileType): 4 values."""

    DEEP = "DEEP"
    LIGHT = "LIGHT"
    NORMAL = "NORMAL"
    SMART = "SMART"


@dataclass(frozen=True)
class CleaningProfile:
    """Confirmed (androguard): profile (ProfileType), commandParams
    (CommandParams -- same class as in RoutineCommand/Region above),
    regions (List -- structure not further investigated, left raw)."""

    profile: CleaningProfileType | str | None = None
    command_params: CommandParams | None = None
    regions: list[Any] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleaningProfile:
        params_data = data.get("commandParams")
        return cls(
            profile=_enum_or_none(CleaningProfileType, data.get("profile")),
            command_params=CommandParams.from_json(params_data) if params_data else None,
            regions=data.get("regions") or [],
        )


@dataclass(frozen=True)
class DNDStatusResponse:
    """Confirmed (androguard): dailyStart/dailyEnd (Integer, presumably
    minutes since midnight), endsAt (Long, presumably epoch millis for
    a one-time DND exception), status (Map -- structure not
    investigated). IMPORTANT: DNDSchedule (the sealed-class variant
    with DailySchedule/EndsAt as separate types) and DNDStatusResponse
    (this flat class) are TWO DIFFERENT representations --
    DNDStatusResponse is likely the actual GET response shape (directly
    referenced by DNDGetRequest callers), DNDSchedule is more likely
    internal for building the PUT request."""

    daily_start: int | None = None
    daily_end: int | None = None
    ends_at: int | None = None
    status: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DNDStatusResponse:
        return cls(
            daily_start=data.get("dailyStart"),
            daily_end=data.get("dailyEnd"),
            ends_at=data.get("endsAt"),
            status=data.get("status") or {},
        )


@dataclass(frozen=True)
class HouseholdSetting:
    """Confirmed (androguard): settingId, settingType (String),
    options (HouseholdSettingOptions -- this class itself wasn't
    further investigated, presumably a generic/polymorphic container
    depending on settingType -- left here as a raw dict)."""

    setting_id: str | None = None
    setting_type: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdSetting:
        return cls(
            setting_id=data.get("settingId"),
            setting_type=data.get("settingType"),
            options=data.get("options") or {},
        )


@dataclass(frozen=True)
class Routine:
    """Confirmed (androguard, routines/datamodels/Routine -- the
    default routines response, DIFFERENT from favorites/datamodels/
    RoutineCommand above): commandDefs (List -- by strong analogy to
    FavoriteV1.command_defs presumably List<RoutineCommand>, but not
    resolvable generically via the bytecode field signature), lastRun,
    nameLocArgs/nameLocKey (localization strings for the UI display
    name), timeEstimate/timeEstimateSeconds."""

    name: str | None = None
    command_defs: list[dict[str, Any]] = field(default_factory=list)
    last_run: int | None = None
    name_loc_key: str | None = None
    name_loc_args: list[str] = field(default_factory=list)
    time_estimate: int | None = None
    time_estimate_seconds: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Routine:
        return cls(
            name=data.get("name"),
            command_defs=data.get("commandDefs") or [],
            last_run=data.get("lastRun"),
            name_loc_key=data.get("nameLocKey"),
            name_loc_args=data.get("nameLocArgs") or [],
            time_estimate=data.get("timeEstimate"),
            time_estimate_seconds=data.get("timeEstimateSeconds"),
        )


def parse_default_routines(data: dict[str, Any] | list[dict[str, Any]]) -> list[Routine]:
    """Converts the raw get_default_routines() response into a list of
    typed Routine objects. Envelope shape not confirmed -- tolerates
    the same variants as parse_mission_history()."""
    if isinstance(data, dict):
        entries = data.get("routines") or data.get("defaults") or []
    else:
        entries = data
    return [Routine.from_json(e) for e in entries]


# =========================================================================
# MissionTimelineEvent -- all 20 sub-event types (session 18)
# =========================================================================
#
# Closes the effort limit deliberately drawn in the ninth session. All
# fields confirmed (15 classes cleanly decompiled via jadx, 4
# more -- PlanEvent/PolygonEvent/TravelEvent/TraversalEvent, plus the 4
# corresponding enums PlanType/PlanUpcoming/TravelDestination/
# TraversalType -- via androguard, since jadx had silently skipped them
# as so often). MissionTimelineEvent itself has EXACTLY 20 sub-event
# fields (androguard-confirmed) -- "relocalizing" and
# "tentativeLocation" both share the same type TentativeLocationEvent
# (two fields, one class), so 19 event classes are enough for 20 fields.
#
# No @SerialName on a single field found in this entire family -- wire
# keys = Kotlin property name directly (camelCase), the same pattern as
# everywhere else in this file.


class PlanType(str, Enum):
    """Confirmed (androguard, PlanEvent.type): 3 values."""

    ALL = "ALL"
    DRC = "DRC"
    TRAIN = "TRAIN"


class PlanUpcoming(str, Enum):
    """Confirmed (androguard, PlanEvent.upcoming list elements): 4 values."""

    POLY = "POLY"
    RID = "RID"
    WID = "WID"
    ZID = "ZID"


class TravelDestination(str, Enum):
    """Confirmed (androguard for constant names), values CHANGED to
    lowercase (session 31) -- real data shows "dest": "dock"/"zone"/
    "room" (lowercase), the same pattern as RegionType/DoneCode. Only
    "dock"/"zone"/"room" directly observed, "poly"/"waypoint" changed
    along with them following the same pattern."""

    DOCK = "dock"
    POLY = "poly"
    ROOM = "room"
    WAYPOINT = "waypoint"
    ZONE = "zone"


class TraversalType(str, Enum):
    """Confirmed (androguard for constant names), value changed to
    lowercase (session 31) -- real data shows "type": "region"
    (lowercase) within the traversal sub-object. Only REGION directly
    observed, ZONE changed along with it following the same pattern."""

    REGION = "region"
    ZONE = "zone"


@dataclass(frozen=True)
class CommandEvent:
    """Confirmed (jadx): command, initiator, time."""

    command: str | None = None
    initiator: str | None = None
    time: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CommandEvent:
        return cls(command=data.get("command"), initiator=data.get("initiator"), time=data.get("time"))


@dataclass(frozen=True)
class DiscoveryEvent:
    """Confirmed (jadx): mapId, mapVersion, regionId."""

    map_id: str | None = None
    map_version: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DiscoveryEvent:
        return cls(map_id=data.get("mapId"), map_version=data.get("mapVersion"), region_id=data.get("regionId"))


@dataclass(frozen=True)
class ErrorEvent:
    """Confirmed (jadx): only field value (presumably an error code,
    analogous to MissionHistoryEntry.error_code)."""

    value: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ErrorEvent:
        return cls(value=data.get("value"))


@dataclass(frozen=True)
class EvacEvent:
    """Confirmed (jadx): error, state -- auto-evac process (evac dock)."""

    error: int | None = None
    state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EvacEvent:
        return cls(error=data.get("error"), state=data.get("state"))


@dataclass(frozen=True)
class LiveViewEvent:
    """Confirmed (jadx): eventId, status."""

    event_id: str | None = None
    status: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LiveViewEvent:
        return cls(event_id=data.get("eventId"), status=data.get("status"))


@dataclass(frozen=True)
class PadDryEvent:
    """Confirmed (jadx): error, padDryState -- mop pad drying cycle."""

    error: int | None = None
    pad_dry_state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PadDryEvent:
        return cls(error=data.get("error"), pad_dry_state=data.get("padDryState"))


@dataclass(frozen=True)
class PadWashEvent:
    """REVISED (session 31, programmatic full comparison): real data
    shows flAmt (not fluidAmount), pwState (not padWashState) --
    error/reason were already correct."""

    error: int | None = None
    fluid_amount: int | None = None
    pad_wash_state: int | None = None
    reason: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PadWashEvent:
        return cls(
            error=data.get("error"),
            fluid_amount=data.get("flAmt") or data.get("fluidAmount"),
            pad_wash_state=data.get("pwState") or data.get("padWashState"),
            reason=data.get("reason"),
        )


@dataclass(frozen=True)
class PanoramaEvent:
    """Confirmed (jadx): eventId, mapId, mapVersion, panoramaId, status,
    waypointId -- panorama capture during mapping."""

    event_id: str | None = None
    map_id: str | None = None
    map_version: str | None = None
    panorama_id: str | None = None
    status: int | None = None
    waypoint_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PanoramaEvent:
        return cls(
            event_id=data.get("eventId"),
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            panorama_id=data.get("panoramaId"),
            status=data.get("status"),
            waypoint_id=data.get("waypointId"),
        )


@dataclass(frozen=True)
class PlanEvent:
    """Confirmed (androguard, jadx had skipped this class): mapId,
    mapVersion, ordered, type (PlanType), upcoming
    (List[PlanUpcoming]). "ordered" here clearly an intra-event
    property (position within the upcoming list) -- good evidence for
    the same reading that ha_roomba_plus had already confirmed for
    RoutineCommand.ordered (see its docstring), this time in a
    completely different context (historical report instead of a live
    command)."""

    map_id: str | None = None
    map_version: str | None = None
    ordered: int | None = None
    plan_type: PlanType | str | None = None
    upcoming: list[PlanUpcoming | str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PlanEvent:
        return cls(
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            ordered=data.get("ordered"),
            plan_type=_enum_or_none(PlanType, data.get("type")),
            upcoming=[_enum_or_none(PlanUpcoming, v) for v in (data.get("upcoming") or [])],
        )


@dataclass(frozen=True)
class PolygonEvent:
    """Confirmed (androguard): area, areaCleaned, mapId, mapVersion,
    poly (List -- structure not further investigated, left raw),
    polyId, regionId."""

    area: int | None = None
    area_cleaned: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    poly: list[Any] = field(default_factory=list)
    poly_id: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PolygonEvent:
        return cls(
            area=data.get("area"),
            area_cleaned=data.get("areaCleaned"),
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            poly=data.get("poly") or [],
            poly_id=data.get("polyId"),
            region_id=data.get("regionId"),
        )


@dataclass(frozen=True)
class RefillEvent:
    """Confirmed (jadx): error, fluidAmount, fluidReplenishmentState --
    fresh water/cleaning solution refill process."""

    error: int | None = None
    fluid_amount: int | None = None
    fluid_replenishment_state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RefillEvent:
        return cls(
            error=data.get("error"),
            fluid_amount=data.get("fluidAmount"),
            fluid_replenishment_state=data.get("fluidReplenishmentState"),
        )


@dataclass(frozen=True)
class RoomEvent:
    """REVISED (session 31, programmatic full comparison): the most
    recent jadx reading (mapId/mapVersion/regionId) was wrong -- real
    finEvents data shows the short forms p2mapId/p2mapvId/rid,
    consistent with the pattern in Travel-/Traversal-/ZoneEvent.
    conPasses/passArea were never observed in the available real
    examples (neither confirmed nor disproven) -- field names for
    these left unchanged."""

    area: int | None = None
    con_passes: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    pass_area: int | None = None
    pass_count: int | None = None
    region_id: str | None = None
    status: int | None = None
    total_area: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomEvent:
        return cls(
            area=data.get("area"),
            con_passes=data.get("conPasses"),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            pass_area=data.get("passArea"),
            pass_count=data.get("passCount"),
            region_id=data.get("rid") or data.get("regionId"),
            status=data.get("status"),
            total_area=data.get("totalArea"),
        )


@dataclass(frozen=True)
class SubRoomEvent:
    """Confirmed (jadx): area, mapId, mapVersion, operatingMode, passArea,
    passCount, polyId, regionId, status, subRegionId, totalArea, zoneId --
    progress per sub-room/zone within a room."""

    area: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    operating_mode: int | None = None
    pass_area: int | None = None
    pass_count: int | None = None
    poly_id: str | None = None
    region_id: str | None = None
    status: int | None = None
    sub_region_id: str | None = None
    total_area: int | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SubRoomEvent:
        return cls(
            area=data.get("area"),
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            operating_mode=data.get("operatingMode"),
            pass_area=data.get("passArea"),
            pass_count=data.get("passCount"),
            poly_id=data.get("polyId"),
            region_id=data.get("regionId"),
            status=data.get("status"),
            sub_region_id=data.get("subRegionId"),
            total_area=data.get("totalArea"),
            zone_id=data.get("zoneId"),
        )


@dataclass(frozen=True)
class TentativeLocationEvent:
    """REVISED (session 31, programmatic full comparison): the real
    wire key for this event is "reloc", NOT "relocalizing" or
    "tentativeLocation" as originally assumed (see
    MissionTimelineEvent.from_json()). Field names themselves also
    corrected: confp2mapId/confp2mapvId (not
    confirmedMapId/confirmedMapVersion), p2mapId/p2mapvId (not
    mapId/mapVersion). regionId/confirmedRegionId never observed in
    the available real examples -- left unchanged. Still referenced
    on TWO MissionTimelineEvent fields (relocalizing +
    tentativeLocation) -- whether "tentativeLocation" exists as its
    own, actually occurring wire key remains unconfirmed."""

    confirmed_map_id: str | None = None
    confirmed_map_version: str | None = None
    confirmed_region_id: str | None = None
    map_id: str | None = None
    map_version: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TentativeLocationEvent:
        return cls(
            confirmed_map_id=data.get("confp2mapId") or data.get("confirmedMapId"),
            confirmed_map_version=data.get("confp2mapvId") or data.get("confirmedMapVersion"),
            confirmed_region_id=data.get("confRid") or data.get("confirmedRegionId"),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            region_id=data.get("rid") or data.get("regionId"),
        )


@dataclass(frozen=True)
class TravelEvent:
    """REVISED (session 31, programmatic full comparison): almost all
    field names were wrong -- real data shows dest (not destination),
    p2mapId (not mapId), p2mapvId (not mapVersion), rid (not
    regionId), zid (not zoneId). polyId/waypointId never observed in
    the available real examples -- left unchanged."""

    destination: TravelDestination | str | None = None
    map_id: str | None = None
    map_version: str | None = None
    poly_id: str | None = None
    reason: int | None = None
    region_id: str | None = None
    status: int | None = None
    waypoint_id: str | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TravelEvent:
        return cls(
            destination=_enum_or_none(TravelDestination, data.get("dest") or data.get("destination")),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            poly_id=data.get("polyId"),
            reason=data.get("reason"),
            region_id=data.get("rid") or data.get("regionId"),
            status=data.get("status"),
            waypoint_id=data.get("waypointId"),
            zone_id=data.get("zid") or data.get("zoneId"),
        )


@dataclass(frozen=True)
class TraversalEvent:
    """REVISED (session 31, programmatic full comparison): real data
    shows p2mapId (not mapId), p2mapvId (not mapVersion), rid (not
    regionId) -- zoneId/zid never observed in the available real
    examples."""

    map_id: str | None = None
    map_version: str | None = None
    region_id: str | None = None
    traversal_type: TraversalType | str | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TraversalEvent:
        return cls(
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            region_id=data.get("rid") or data.get("regionId"),
            traversal_type=_enum_or_none(TraversalType, data.get("type")),
            zone_id=data.get("zid") or data.get("zoneId"),
        )


@dataclass(frozen=True)
class WaypointEvent:
    """Confirmed (jadx): mapId, mapVersion, waypointId."""

    map_id: str | None = None
    map_version: str | None = None
    waypoint_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WaypointEvent:
        return cls(map_id=data.get("mapId"), map_version=data.get("mapVersion"), waypoint_id=data.get("waypointId"))


@dataclass(frozen=True)
class WetOutEvent:
    """Confirmed (jadx): status, type -- mop pad wet-out process."""

    status: int | None = None
    wet_out_type: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WetOutEvent:
        return cls(status=data.get("status"), wet_out_type=data.get("type"))


@dataclass(frozen=True)
class ZoneEvent:
    """REVISED (session 31, programmatic full comparison): real data
    shows p2mapId (not mapId), p2mapvId (not mapVersion), zid (not
    zoneId) -- passArea never observed in the available real examples."""

    area: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    pass_area: int | None = None
    pass_count: int | None = None
    status: int | None = None
    total_area: int | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ZoneEvent:
        return cls(
            area=data.get("area"),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            pass_area=data.get("passArea"),
            pass_count=data.get("passCount"),
            status=data.get("status"),
            total_area=data.get("totalArea"),
            zone_id=data.get("zid") or data.get("zoneId"),
        )


@dataclass(frozen=True)
class MissionTimelineEvent:
    """Confirmed (androguard, MissionTimelineEvent): startTime, endTime,
    type (String -- discriminator for which of the 20 sub-fields is
    set, no @SerialName found), plus EXACTLY 20 optional sub-event
    fields. Typically only ONE field is set per event (matching the
    respective "type" discriminator value) -- all others remain None."""

    start_time: int | None = None
    end_time: int | None = None
    event_type: str | None = None
    command: CommandEvent | None = None
    discovery: DiscoveryEvent | None = None
    error: ErrorEvent | None = None
    evac: EvacEvent | None = None
    live_view: LiveViewEvent | None = None
    pad_dry: PadDryEvent | None = None
    pad_wash: PadWashEvent | None = None
    panorama: PanoramaEvent | None = None
    plan: PlanEvent | None = None
    polygon: PolygonEvent | None = None
    refill: RefillEvent | None = None
    relocalizing: TentativeLocationEvent | None = None
    room: RoomEvent | None = None
    sub_room: SubRoomEvent | None = None
    tentative_location: TentativeLocationEvent | None = None
    travel: TravelEvent | None = None
    traversal: TraversalEvent | None = None
    waypoint: WaypointEvent | None = None
    wet_out: WetOutEvent | None = None
    zone: ZoneEvent | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionTimelineEvent:
        """CORRECTED (session 31, programmatic full comparison against
        real data): startTime/endTime do NOT exist in real finEvents
        entries -- the actual timestamp keys are "ts" (event time) and
        "ets" (presumably "event timestamp", often close to ts). Both
        old names remain as a fallback, in case some other response
        shape does use them. "reloc" is the real key for the
        relocalization state (a wire-typical short name form,
        consistent with room/zone/travel/traversal/evac/padWash) --
        until now only "relocalizing"/"tentativeLocation" had been
        tried, neither of which is correct; "reloc" now added and
        populates the same "relocalizing" attribute."""

        def _sub(key: str, parser: Any) -> Any:
            raw = data.get(key)
            return parser(raw) if raw is not None else None

        return cls(
            start_time=data.get("ts") or data.get("startTime"),
            end_time=data.get("ets") or data.get("endTime"),
            event_type=data.get("type"),
            command=_sub("command", CommandEvent.from_json),
            discovery=_sub("discovery", DiscoveryEvent.from_json),
            error=_sub("error", ErrorEvent.from_json),
            evac=_sub("evac", EvacEvent.from_json),
            live_view=_sub("liveView", LiveViewEvent.from_json),
            pad_dry=_sub("padDry", PadDryEvent.from_json),
            pad_wash=_sub("padWash", PadWashEvent.from_json),
            panorama=_sub("panorama", PanoramaEvent.from_json),
            plan=_sub("plan", PlanEvent.from_json),
            polygon=_sub("polygon", PolygonEvent.from_json),
            refill=_sub("refill", RefillEvent.from_json),
            relocalizing=_sub("reloc", TentativeLocationEvent.from_json) or _sub("relocalizing", TentativeLocationEvent.from_json),
            room=_sub("room", RoomEvent.from_json),
            sub_room=_sub("subRoom", SubRoomEvent.from_json),
            tentative_location=_sub("tentativeLocation", TentativeLocationEvent.from_json),
            travel=_sub("travel", TravelEvent.from_json),
            traversal=_sub("traversal", TraversalEvent.from_json),
            waypoint=_sub("waypoint", WaypointEvent.from_json),
            wet_out=_sub("wetOut", WetOutEvent.from_json),
            zone=_sub("zone", ZoneEvent.from_json),
        )


def parse_mission_timeline(data: dict[str, Any] | list[dict[str, Any]] | None) -> list[MissionTimelineEvent]:
    """Converts MissionHistoryEntry.raw["timeline"] into a list of
    typed MissionTimelineEvent objects. NEW (session 18). Tolerates
    both a raw list and a dict with an enclosing key (envelope shape
    not confirmed, analogous to parse_mission_history())."""
    if data is None:
        return []
    if isinstance(data, dict):
        entries = data.get("events") or data.get("timeline") or []
    else:
        entries = data
    return [MissionTimelineEvent.from_json(e) for e in entries]


# =========================================================================
# P2MapVersion / RoomMetadataEntry / RobotSerialInfo (session 26)
# =========================================================================
#
# STATUS: NEW (session 26). Confirmed from a complete, real
# --dump-config response (chairstacker, Roomba 405). get_active_map_versions()
# and get_serial_number_data() used to return raw JSON (docstring only
# with guessed/partially wrong field names) -- now typed with the
# actual, live-confirmed structure. Especially valuable:
# rooms_metadata[].room_metadata.operating_mode_defaults' values are
# CommandParams-shaped and can be parsed directly with
# CommandParams.from_json() -- the same type as for
# RoutineCommand.params/Region.params.


@dataclass(frozen=True)
class RoomMetadataEntry:
    """Confirmed (real live response): room_id + room_metadata with
    last_operating_mode, operating_mode_defaults (dict, keys =
    operating-mode ID as a string like "512"/"32"/"2", values
    CommandParams-shaped), region_type, optional name (only set for
    some rooms, e.g. "Bathroom")."""

    room_id: str
    last_operating_mode: int | None = None
    operating_mode_defaults: dict[str, CommandParams] = field(default_factory=dict)
    region_type: RegionType | str | None = None
    name: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomMetadataEntry:
        meta = data.get("room_metadata") or {}
        defaults_raw = meta.get("operating_mode_defaults") or {}
        return cls(
            room_id=data.get("room_id", ""),
            last_operating_mode=meta.get("last_operating_mode"),
            operating_mode_defaults={k: CommandParams.from_json(v) for k, v in defaults_raw.items()},
            region_type=_enum_or_none(RegionType, meta.get("region_type")),
            name=meta.get("name"),
        )


@dataclass(frozen=True)
class P2MapVersion:
    """Confirmed (real live response, chairstacker): replaces the
    previously wrong docstring assumption ("at least mapId/mapVersionId")
    -- the real primary key is `p2map_id`, the map version is called
    `active_p2mapv_id`. An account can have multiple P2MapVersion
    entries (in the observed case two: "Whole House" and
    "Master_Bathroom")."""

    p2map_id: str
    entity_type: str | None = None
    create_time: int | None = None
    robot_id: str | None = None
    sku: str | None = None
    active_p2mapv_id: str | None = None
    last_p2mapv_ts: int | None = None
    state: str | None = None
    visible: bool | None = None
    name: str | None = None
    rooms_metadata: list[RoomMetadataEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapVersion:
        return cls(
            p2map_id=data.get("p2map_id", ""),
            entity_type=data.get("entity_type"),
            create_time=data.get("create_time"),
            robot_id=data.get("robot_id"),
            sku=data.get("sku"),
            active_p2mapv_id=data.get("active_p2mapv_id"),
            last_p2mapv_ts=data.get("last_p2mapv_ts"),
            state=data.get("state"),
            visible=data.get("visible"),
            name=data.get("name"),
            rooms_metadata=[RoomMetadataEntry.from_json(r) for r in (data.get("rooms_metadata") or [])],
        )


def parse_active_map_versions(data: list[dict[str, Any]] | None) -> list[P2MapVersion]:
    """Converts the raw get_active_map_versions() response into a list
    of typed P2MapVersion objects. NEW (session 26)."""
    if not data:
        return []
    return [P2MapVersion.from_json(entry) for entry in data]


@dataclass(frozen=True)
class RobotSerialInfo:
    """Confirmed (real live response, chairstacker,
    get_serial_number_data()). "family" observed as "Roomba Combo"
    (vacuum+mop combo device), "series" as "G1". is_raas presumably
    "Robot as a Service" (subscription/rental model), is_smartcare
    presumably a maintenance-contract flag -- both names taken from
    the JSON, their exact meaning not further investigated."""

    robot_id: str | None = None
    serial_number: str | None = None
    built_as_sku: str | None = None
    family_variant: str | None = None
    is_raas: bool | None = None
    is_refurbished: bool | None = None
    is_smartcare: bool | None = None
    min_utc_reg_date: int | None = None
    name: str | None = None
    sku: str | None = None
    series: str | None = None
    family: str | None = None
    serial_history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotSerialInfo:
        return cls(
            robot_id=data.get("RobotID"),
            serial_number=data.get("SerialNumber"),
            built_as_sku=data.get("built_as_sku"),
            family_variant=data.get("family_variant"),
            is_raas=data.get("is_raas"),
            is_refurbished=data.get("is_refurbished"),
            is_smartcare=data.get("is_smartcare"),
            min_utc_reg_date=data.get("min_utc_reg_date"),
            name=data.get("name"),
            sku=data.get("sku"),
            series=data.get("series"),
            family=data.get("family"),
            serial_history=data.get("serial_history") or [],
        )


# =========================================================================
# RobotPart / RobotPartsInfo (session 27)
# =========================================================================
#
# STATUS: NEW. Confirmed from a real get_robot_parts() response
# (chairstacker). Consumable/maintenance part counters, e.g. for pad
# washes, evac processes, or time-based usage (filter/brush).
# counter_category observed as "replacement" or "maintenance";
# reset_by as "user" or "cloud".


@dataclass(frozen=True)
class RobotPart:
    """Confirmed (real live response): part_id, counter,
    minutes_remaining (-1 if not time-based), last_updated_ts
    (optional, not present for every part), count_type (e.g.
    "combo_missions", "pad_washes_used", "minutes", "evacs"),
    count_remaining, count_used, counter_category ("replacement"/
    "maintenance"), reset_by ("user"/"cloud")."""

    part_id: str
    counter: int | None = None
    minutes_remaining: int | None = None
    last_updated_ts: int | None = None
    count_type: str | None = None
    count_remaining: int | None = None
    count_used: int | None = None
    counter_category: str | None = None
    reset_by: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotPart:
        return cls(
            part_id=data.get("part_id", ""),
            counter=data.get("counter"),
            minutes_remaining=data.get("minutes_remaining"),
            last_updated_ts=data.get("last_updated_ts"),
            count_type=data.get("count_type"),
            count_remaining=data.get("count_remaining"),
            count_used=data.get("count_used"),
            counter_category=data.get("counter_category"),
            reset_by=data.get("reset_by"),
        )


@dataclass(frozen=True)
class RobotPartsInfo:
    """Confirmed (real live response, get_robot_parts()): robot_id,
    num_parts, parts (list of RobotPart)."""

    robot_id: str | None = None
    num_parts: int | None = None
    parts: list[RobotPart] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotPartsInfo:
        return cls(
            robot_id=data.get("robot_id"),
            num_parts=data.get("num_parts"),
            parts=[RobotPart.from_json(p) for p in (data.get("parts") or [])],
        )


# =========================================================================
# Household / HouseholdRobot / HouseholdUser (session 28)
# =========================================================================
#
# STATUS: NEW. Confirmed from a real get_user_households() response
# (chairstacker) -- the endpoint itself was documented as "dead code
# in the current app, HTTP method just convention", but ACTUALLY
# responded correctly. entity_id follows a "type#id" pattern
# ("robot#{blid}", "user#{cognito_id}").


@dataclass(frozen=True)
class HouseholdRobot:
    """Confirmed (real live response): household_id, entity_id
    (format "robot#{robot_id}"), robot_id, creation_timestamp."""

    household_id: str | None = None
    entity_id: str | None = None
    robot_id: str | None = None
    creation_timestamp: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdRobot:
        return cls(
            household_id=data.get("household_id"),
            entity_id=data.get("entity_id"),
            robot_id=data.get("robot_id"),
            creation_timestamp=data.get("creation_timestamp"),
        )


@dataclass(frozen=True)
class HouseholdUser:
    """Confirmed (real live response): household_id, entity_id
    (format "user#{cognito_id}"), cognito_id, creation_timestamp."""

    household_id: str | None = None
    entity_id: str | None = None
    cognito_id: str | None = None
    creation_timestamp: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdUser:
        return cls(
            household_id=data.get("household_id"),
            entity_id=data.get("entity_id"),
            cognito_id=data.get("cognito_id"),
            creation_timestamp=data.get("creation_timestamp"),
        )


@dataclass(frozen=True)
class Household:
    """Confirmed (real live response, get_user_households()):
    household_id, owner_cognito_id, household_name (observed value
    "#AUTO_GENERATED_HOUSEHOLD#" -- suggests most users never manually
    assign a household name), has_precise_location, household_robots,
    household_users."""

    household_id: str | None = None
    owner_cognito_id: str | None = None
    household_name: str | None = None
    has_precise_location: bool | None = None
    household_robots: list[HouseholdRobot] = field(default_factory=list)
    household_users: list[HouseholdUser] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Household:
        return cls(
            household_id=data.get("household_id"),
            owner_cognito_id=data.get("owner_cognito_id"),
            household_name=data.get("household_name"),
            has_precise_location=data.get("has_precise_location"),
            household_robots=[HouseholdRobot.from_json(r) for r in (data.get("household_robots") or [])],
            household_users=[HouseholdUser.from_json(u) for u in (data.get("household_users") or [])],
        )


def parse_user_households(data: list[dict[str, Any]] | None) -> list[Household]:
    """Converts the raw get_user_households() response into a list of
    typed Household objects. NEW (session 28)."""
    if not data:
        return []
    return [Household.from_json(entry) for entry in data]


# =========================================================================
# RobotSettings (session 32)
# =========================================================================
#
# STATUS: NEW. Confirmed from a real get_settings() response (chairstacker,
# the "rw-settings"-named shadow). Resolves a large part of the settings
# vocabulary previously listed in docs/API_REFERENCE.md as "discovered, but
# unmodeled" -- many of the settings only suspected there as a commandId
# settings now directly correspond to fields in this response (SetChildLock
# -> childLock, SetAudioVolumePattern -> audio.volume,
# SetAutoEvacFrequency -> autoevacFreq, SetRobotLanguageV2 -> langs2,
# SetMapUploadAllowedCommand -> mapUploadAllowed, SetPadDryDuration ->
# padDryDur, among others). "langs2" deliberately left as a raw dict
# (nested language-list structure, little value in a dedicated model).


@dataclass(frozen=True)
class RobotSettings:
    """Confirmed (real live response, get_settings()): complete
    content of the named "rw-settings" shadow for a SMART-tier device.
    Covers things like child lock, volume, timezone, pad wash
    settings, language list, auto-evac frequency, and various
    "*Allowed" permission flags."""

    audio_volume: int | None = None
    autoevac_freq: int | None = None
    carpet_boost: bool | None = None
    child_lock: bool | None = None
    cloud_env: str | None = None
    country: str | None = None
    eco_charge: bool | None = None
    evac_allowed: bool | None = None
    map_upload_allowed: bool | None = None
    name: str | None = None
    no_auto_passes: bool | None = None
    nsmip: int | None = None
    pad_dry_allowed: int | None = None
    pad_dry_duration: int | None = None
    pad_wash_allowed: int | None = None
    pad_wash_area_interval: int | None = None
    pad_wash_return: int | None = None
    pad_wash_time_interval: int | None = None
    pad_wetness: PadWetnessParam | None = None
    sched_hold: bool | None = None
    scrub: int | None = None
    suction_level: int | None = None
    svc_deployment_id: str | None = None
    timezone: str | None = None
    two_pass: bool | None = None
    vac_high: bool | None = None
    languages_raw: dict[str, Any] | None = None
    """Raw "langs2" object (aSlots, dLangs.langs/ver, sLang, sVer) --
    deliberately not further broken down, little added value for a
    dedicated model."""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotSettings:
        audio = data.get("audio") or {}
        pad_wetness_data = data.get("padWetness")
        svc_endpoints = data.get("svcEndpoints") or {}
        return cls(
            audio_volume=audio.get("volume"),
            autoevac_freq=data.get("autoevacFreq"),
            carpet_boost=data.get("carpetBoost"),
            child_lock=data.get("childLock"),
            cloud_env=data.get("cloudEnv"),
            country=data.get("country"),
            eco_charge=data.get("ecoCharge"),
            evac_allowed=data.get("evacAllowed"),
            map_upload_allowed=data.get("mapUploadAllowed"),
            name=data.get("name"),
            no_auto_passes=data.get("noAutoPasses"),
            nsmip=data.get("nsmip"),
            pad_dry_allowed=data.get("padDryAllowed"),
            pad_dry_duration=data.get("padDryDur"),
            pad_wash_allowed=data.get("padWashAllowed"),
            pad_wash_area_interval=data.get("pwAreaInterval"),
            pad_wash_return=data.get("pwReturn"),
            pad_wash_time_interval=data.get("pwTimeInterval"),
            pad_wetness=PadWetnessParam.from_json(pad_wetness_data) if pad_wetness_data else None,
            sched_hold=data.get("schedHold"),
            scrub=data.get("swScrub"),
            suction_level=data.get("suctionLevel"),
            svc_deployment_id=svc_endpoints.get("svcDeplId"),
            timezone=data.get("timezone"),
            two_pass=data.get("twoPass"),
            vac_high=data.get("vacHigh"),
            languages_raw=data.get("langs2"),
        )
