"""Map edit commands -- both the V1 (actually used) and V2 (dead code) paths.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums_common import FurnitureType, RoomType
from .geometry import LineString, Polygon, Position


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
    def from_two_points(cls, room_id: str, from_pos: Position, to_pos: Position) -> SplitRoom:
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


@dataclass(frozen=True)
class RenameRoomV1:
    """CORRECTED (session 48): confirmed directly from
    EditMapV1Request$Command$RenameRoom$$serializer's <clinit> (the
    same technique that resolved RobotStatusV2/ScheduleOptions/the map
    bundle models before it) -- real field names are `room_id` and
    `room_name`, NOT `id`/`name` as the earlier androguard field-name
    reading had assumed. See MapEditCommandV1's module docstring for
    the also-newly-confirmed outer envelope
    ({"edit_cmd": ..., "response_type": ...}) this now needs to be
    wrapped in by the caller (rest_client.py's edit_map())."""

    room_id: str
    name: str

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"type": "RenameRoom", "room_id": self.room_id, "room_name": self.name}


@dataclass(frozen=True)
class SplitRoomV1:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$SplitRoom$$serializer -- real field names
    are `room_id`/`split_points` (the latter was already correctly
    guessed; `id` -> `room_id` was not). The exact meaning of
    "split_points" (two endpoints like V2? or more?) still not
    independently confirmed."""

    room_id: str
    split_points: list[Position]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "type": "SplitRoom",
            "room_id": self.room_id,
            "split_points": [list(p) for p in self.split_points],
        }


@dataclass(frozen=True)
class MergeRoomsV1:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$MergeRooms$$serializer -- real field name
    is `room_ids`, not `ids` as previously guessed."""

    ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"type": "MergeRooms", "room_ids": self.ids}


@dataclass(frozen=True)
class SetRoomTypeV1:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$SetRoomType$$serializer -- real field
    names are `room_id`/`type_id`, not `id`/`type` as previously
    guessed. `type_id` presumably still carries the same numeric
    RoomType codes (NOT_RECOGNIZED, BEDROOM, DINING_ROOM, BATHROOM,
    HALLWAY, KITCHEN, LIVING_ROOM, BALCONY, OTHER) -- the existing
    RoomType int enum is reused here, though this specific value-space
    assumption for the V1 edit path is not independently confirmed
    beyond the field NAME (a caution learned the hard way this same
    session for RoomFeatureProperties.room_type -- see that class'
    docstring)."""

    room_id: str
    room_type: RoomType

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"type": "SetRoomType", "room_id": self.room_id, "type_id": int(self.room_type)}


@dataclass(frozen=True)
class SetRoomMetadataV1:
    """UPDATE (session 48): unlike 8 of the other 9 V1 command types,
    SetRoomMetadata does NOT have an auto-generated
    `$$serializer`/`<clinit>` -- it uses a hand-written CUSTOM
    `EditMapV1Request$Command$SetRoomMetadata$Serializer` class
    instead, whose actual field-mapping logic requires disassembling
    its serialize()/deserialize() methods directly rather than reading
    a simple `<clinit>` string list. Not pursued this session (the
    same kind of limit as the outer envelope's own discriminator
    mechanism, and DNDSchedule's sealed-class serializer before it) --
    this class's field names remain at their PREVIOUS confidence level
    (androguard property-name reading only, not the stronger
    $$serializer confirmation the other 8 V1 commands now have)."""

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
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$SetPermanentAreas$$serializer -- real
    field name is `area_points` (snake_case), not `areaPoints`
    (camelCase) as previously guessed. The field TYPE (List<
    PermanentArea> vs. pure position lists) still isn't independently
    resolved -- kept as a list of PermanentAreaV1 objects, the most
    plausible reading given the separately existing PermanentArea
    class."""

    areas: list[PermanentAreaV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"type": "SetPermanentAreas", "area_points": [a.to_json() for a in self.areas]}


@dataclass(frozen=True)
class DeletePermanentAreasV1:
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$DeletePermanentAreas$$serializer -- real
    field name is `area_ids` (snake_case), not `areaIDs` (camelCase)
    as previously guessed."""

    area_ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"type": "DeletePermanentAreas", "area_ids": self.area_ids}


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
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$SetVirtualWalls$$serializer -- real field
    name is `virwall` (an unusual abbreviation, not "walls" as
    previously guessed). How the "type" discriminator of the three
    VirtualWall subtypes (Linear/Rectangle/NoMopZone) actually gets
    onto the wire remains NOT confirmed -- VirtualWall itself uses a
    hand-written CUSTOM `VirtualWallSerializer` (like
    SetRoomMetadata's own custom serializer), not the auto-generated
    kind this session's technique could read directly. "type" key kept
    here as the most plausible assumption, unchanged from before."""

    walls: list[VirtualWallV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"type": "SetVirtualWalls", "virwall": [w.to_json() for w in self.walls]}


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
    """CORRECTED (session 48): confirmed via
    EditMapV1Request$Command$AdjustFurniture$$serializer -- real field
    names are `furniture_list`/`package` (snake_case, and "package"
    not "packageInfo"), `timestamp` was already correctly guessed. A
    BATCH operation (multiple furniture items at once), unlike V2's
    SetFurniture (one item per call). Meaning of "package" still not
    confirmed -- passed through here as a raw list."""

    furniture_list: list[FurnitureItemV1]
    package_info: list[dict[str, Any]] = field(default_factory=list)
    timestamp: int = 0

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "type": "AdjustFurniture",
            "furniture_list": [f.to_json() for f in self.furniture_list],
            "package": self.package_info,
            "timestamp": self.timestamp,
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


