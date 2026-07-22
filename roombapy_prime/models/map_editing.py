"""Map edit commands -- both the V1 (actually used) and V2 (dead code) paths.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field.

UPDATE (this session, live APK decompilation of the FULL
EditMapV1Request.java source, prompted by a live HTTP 500 on a room
rename -- chairstacker): the V1 outer envelope is now fully confirmed,
not just the two top-level keys. Every V1 command's inner body is
{"command": "<snake_case_discriminator>", "params": {...}} -- NOT the
previously-assumed flat {"type": "<PascalCase>", ...fields...} shape.
The "type"-vs-"command" and flat-vs-"params"-nested corrections apply
to ALL nine V1 command classes below, not just the one that triggered
the investigation (RenameRoom). Three of the nine (VirtualWall,
PermanentArea, Furniture) turned out to have their own custom
serializers emitting positional ARRAYS, not JSON objects at all --
see each class's own to_json() docstring for its confirmed array shape."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums_common import FurnitureType, RoomCategory, RoomType
from .geometry import LineString, Polygon, Position
from .map_bundle import PolicyZoneFeature


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


def _flatten_ring(polygon: Polygon) -> list[float]:
    """Flattens a Polygon's outer ring into [x1, y1, x2, y2, ...] -- the
    confirmed V1 wire shape for Rectangle/NoMopZone/PermanentArea
    (positional arrays, not GeoJSON objects). Only the FIRST ring is
    used; V1's array-based geometry has no concept of holes the way
    GeoJSON polygons do, so any additional rings are silently dropped
    here rather than guessing how (or whether) they'd be represented."""
    ring = polygon.coordinates[0] if polygon.coordinates else []
    flat: list[float] = []
    for x, y in ring:
        flat.extend((x, y))
    return flat


@dataclass(frozen=True)
class RenameRoomV1:
    """CONFIRMED (live APK decompilation of EditMapV1Request.java, this
    session): outer envelope is {"command": "rename_room", "params":
    {"room_id": ..., "room_name": ...}} -- NOT the flat {"type":
    "RenameRoom", "room_id": ..., "room_name": ...} previously assumed
    (session 48 confirmed the field NAMES room_id/room_name correctly,
    but the outer shape -- discriminator key "type" vs "command", flat
    vs "params"-nested -- was wrong, since it predates finding the
    actual EditMapV1Request$Body$$serializer envelope class).

    DEPRECATED APP-SIDE, NOT NECESSARILY SERVER-SIDE: RenameRoom carries
    a Kotlin `@Deprecated("Use SetRoomMetadata(mapId, metadata)
    instead")` annotation -- the current app build no longer calls this
    path at all, using SetRoomMetadataV1 instead. That is a statement
    about what the APP does, not evidence the SERVER has stopped
    accepting this command -- but there's equally no live confirmation
    it still works, since a live rename test (chairstacker, this
    session) went through this exact path and failed with HTTP 500 (a
    failure now understood to be caused by the wrong envelope shape,
    not necessarily settling whether RenameRoom itself is still live).
    Prefer SetRoomMetadataV1 if unsure."""

    room_id: str
    name: str

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "rename_room",
            "params": {"room_id": self.room_id, "room_name": self.name},
        }


@dataclass(frozen=True)
class SplitRoomV1:
    """CONFIRMED (live APK decompilation, this session): params are
    {"room_id": ..., "split_points": [x1, y1, x2, y2, ...]} -- a FLAT
    list of doubles (Kotlin `List<Double>`), not a list of [x,y] pairs
    as the previous [[x1,y1],[x2,y2]] shape assumed. room_id's field
    name was already correct (session 48); the split_points VALUE shape
    was not previously re-examined once envelope work started."""

    room_id: str
    split_points: list[Position]

    def to_v1_command_body(self) -> dict[str, Any]:
        flat: list[float] = []
        for x, y in self.split_points:
            flat.extend((x, y))
        return {
            "command": "split_room",
            "params": {"room_id": self.room_id, "split_points": flat},
        }


@dataclass(frozen=True)
class MergeRoomsV1:
    """CONFIRMED (live APK decompilation, this session): params are
    {"room_ids": [...]} under command "arrange_room" -- the field name
    room_ids was already correct (session 48); the discriminator string
    is the surprise here (not "merge_rooms" as the class name would
    suggest)."""

    ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "arrange_room", "params": {"room_ids": self.ids}}


@dataclass(frozen=True)
class SetRoomTypeV1:
    """CONFIRMED (live APK decompilation, this session): params are
    {"room_id": ..., "type_id": ...} under command "set_room_type".
    Field names were already correct (session 48). type_id presumably
    still carries the same numeric RoomType codes -- that specific
    value-space assumption for the V1 edit path remains not
    independently confirmed beyond the field name, same caveat as
    before."""

    room_id: str
    room_type: RoomType

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_room_type",
            "params": {"room_id": self.room_id, "type_id": int(self.room_type)},
        }


@dataclass(frozen=True)
class PermanentAreaV1:
    """CONFIRMED (live APK decompilation, this session): PermanentArea
    is NOT a JSON object (the geometry/id/name shape previously assumed
    from EditMapV1Request$PermanentArea's field names was read
    correctly, but the CLASS has its own custom serializer that emits a
    positional array, not an object -- the same kind of surprise
    SetRoomMetadata's custom serializer turned out to hide). Confirmed
    wire shape: [id, name, [x1, y1, x2, y2, ...]] -- a 3-element array
    whose third element is itself the flattened outer-ring coordinate
    list, not a GeoJSON Polygon."""

    area_id: str
    name: str
    geometry: Polygon

    def to_json(self) -> list[Any]:
        return [self.area_id, self.name, _flatten_ring(self.geometry)]


@dataclass(frozen=True)
class SetPermanentAreasV1:
    """CONFIRMED (live APK decompilation, this session): params are
    {"area_points": [...]} under command "set_permanent_area" (singular
    -- not "SetPermanentAreas"/plural as the class name suggests). The
    area_points field name itself was already correct (session 48)."""

    areas: list[PermanentAreaV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_permanent_area",
            "params": {"area_points": [a.to_json() for a in self.areas]},
        }


@dataclass(frozen=True)
class DeletePermanentAreasV1:
    """CONFIRMED (live APK decompilation, this session): params are
    {"area_ids": [...]} under command "del_permanent_area" (not
    "delete_permanent_areas" -- abbreviated "del", singular "area").
    The area_ids field name itself was already correct (session 48)."""

    area_ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "del_permanent_area",
            "params": {"area_ids": self.area_ids},
        }


@dataclass(frozen=True)
class VirtualWallLinearV1:
    """CONFIRMED (live APK decompilation, this session): VirtualWall is
    NOT a JSON object -- like PermanentArea, it has its own custom
    serializer emitting a positional array: [id, type_int, x1, y1, x2,
    y2, x3, y3, x4, y4], type_int=2 for Linear. A line segment has no
    natural 4-point shape, so the wire format degenerates it into a
    4-point polygon by repeating each endpoint: from, to, to, from --
    i.e. [id, 2, fromX, fromY, toX, toY, toX, toY, fromX, fromY]."""

    wall_id: str
    from_pos: Position
    to_pos: Position

    def to_json(self) -> list[Any]:
        fx, fy = self.from_pos
        tx, ty = self.to_pos
        return [self.wall_id, 2, fx, fy, tx, ty, tx, ty, fx, fy]


@dataclass(frozen=True)
class VirtualWallRectangleV1:
    """CONFIRMED (live APK decompilation, this session): positional
    array [id, type_int, x1, y1, x2, y2, x3, y3, x4, y4], type_int=1 for
    Rectangle -- despite the name, still just a general 4-point polygon
    on the wire, no dedicated rectangle-specific encoding."""

    wall_id: str
    polygon: Polygon

    def to_json(self) -> list[Any]:
        return [self.wall_id, 1, *_flatten_ring(self.polygon)]


@dataclass(frozen=True)
class VirtualWallNoMopZoneV1:
    """CONFIRMED (live APK decompilation, this session): positional
    array [id, type_int, x1, y1, x2, y2, x3, y3, x4, y4], type_int=6 for
    NoMopZone -- same array shape as Rectangle, only the discriminator
    int differs. Confirms the earlier finding that no-mop zones go
    through the same command type as virtual walls in V1
    (SetVirtualWalls / now "set_virtual_wall"), not a dedicated command."""

    wall_id: str
    polygon: Polygon

    def to_json(self) -> list[Any]:
        return [self.wall_id, 6, *_flatten_ring(self.polygon)]


VirtualWallV1 = VirtualWallLinearV1 | VirtualWallRectangleV1 | VirtualWallNoMopZoneV1


def policy_zone_to_virtual_wall(feature: PolicyZoneFeature) -> VirtualWallV1 | None:
    """Converts one raw policyZones.geojson feature into the matching
    VirtualWallV1 subtype for resending via SetVirtualWallsV1/
    "set_virtual_wall" -- implements the complete, CONFIRMED
    categorization rule (parallel native-analysis track,
    P2MapBundleContentHolderPersistentMapKt's own extension functions
    -- the actual code that builds P2PersistentMap's three separate
    typed lists from this single raw list):

        zone_type == "KeepOutZone" + geometry is Polygon
            -> VirtualWallRectangleV1 (a real, persistent keep-out zone)
        zone_type == "KeepOutZone" + geometry is LineString
            -> VirtualWallLinearV1 (a virtual wall -- there is NO
               separate "VirtualWall" zone_type string; this geometry-
               shape distinction is the only thing that tells them apart)
        zone_type == "NoMopZone" (always Polygon)
            -> VirtualWallNoMopZoneV1

    Returns None for "Threshold"-typed features (not part of the
    virtual-wall family at all) and for anything unrecognized --
    callers should filter these out of a combined list themselves
    (e.g. via a list comprehension dropping the None results), rather
    than this function raising on unexpected input. Geometry is passed
    through UNCHANGED -- CONFIRMED (same native-analysis track) that
    no coordinate transformation happens anywhere in this pipeline,
    from the raw bundle read all the way to the wire command."""
    zone_type = feature.properties.zone_type
    geometry = feature.geometry

    if zone_type == "KeepOutZone" and isinstance(geometry, Polygon):
        return VirtualWallRectangleV1(wall_id=feature.feature_id, polygon=geometry)
    if zone_type == "KeepOutZone" and isinstance(geometry, LineString):
        coords = geometry.coordinates
        if len(coords) < 2:
            return None
        return VirtualWallLinearV1(wall_id=feature.feature_id, from_pos=coords[0], to_pos=coords[-1])
    if zone_type == "NoMopZone" and isinstance(geometry, Polygon):
        return VirtualWallNoMopZoneV1(wall_id=feature.feature_id, polygon=geometry)
    return None


def policy_zones_to_virtual_walls(features: list[PolicyZoneFeature]) -> list[VirtualWallV1]:
    """Combines policy_zone_to_virtual_wall() over a full list read
    from policyZones.geojson, dropping thresholds/unrecognized
    entries. Order matches the real app's own rebuild order
    (confirmed, deleteVirtualWall's own real implementation): keep-out
    zones first, then no-mop zones, then virtual walls -- though since
    this function derives the category from each feature's own data
    rather than reading from three pre-split lists, this only produces
    the SAME order as the real app if the input list's own iteration
    order already groups by category; if not, use sorted() with a key
    function to reorder, matching the target write-side command's own
    lack of any confirmed order-sensitivity (not confirmed either way,
    kept simple here)."""
    return [wall for wall in (policy_zone_to_virtual_wall(f) for f in features) if wall is not None]


@dataclass(frozen=True)
class SetVirtualWallsV1:
    """CONFIRMED (live APK decompilation, this session): params are
    {"virwall": [...]} under command "set_virtual_wall" (singular --
    not "SetVirtualWalls"/plural as the class name suggests). The
    virwall field name itself was already correct (session 48). The
    previously-open question -- how the Linear/Rectangle/NoMopZone
    discriminator reaches the wire, since VirtualWall uses a custom
    serializer -- is now answered: see VirtualWall*V1.to_json()'s own
    docstrings. It isn't a "type" string at all; it's a positional int
    at array index 1."""

    walls: list[VirtualWallV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_virtual_wall",
            "params": {"virwall": [w.to_json() for w in self.walls]},
        }


@dataclass(frozen=True)
class FurnitureItemV1:
    """CONFIRMED (live APK decompilation, this session): Furniture is
    NOT a JSON object -- like PermanentArea/VirtualWall, a custom
    serializer emits a positional array: [id, type_int,
    user_modified(0/1), x1, y1, x2, y2, ...]. user_modified is an
    int 0/1 on the wire, not a JSON bool. Uses the existing
    FurnitureType int enum for the type value, same as before."""

    furniture_id: str
    furniture_type: FurnitureType
    geometry: Polygon
    user_modified: bool = True

    def to_json(self) -> list[Any]:
        return [
            self.furniture_id,
            int(self.furniture_type),
            1 if self.user_modified else 0,
            *_flatten_ring(self.geometry),
        ]


@dataclass(frozen=True)
class AdjustFurnitureV1:
    """CONFIRMED (live APK decompilation, this session): params are
    {"furniture_list": [...], "package": [1, 1], "timestamp": ...}
    under command "adjust_furniture". furniture_list/package/timestamp
    field names were already correct (session 48) -- what's newly
    confirmed is that "package" is simply a fixed 2-int default [1, 1]
    (Kotlin default parameter value), not a complex, per-call-computed
    structure as the earlier "meaning not confirmed, passed through as
    a raw list" note assumed. package_info is kept as a caller-
    overridable field (in case a real edit ever needs something other
    than the default), defaulting to [1, 1] to match."""

    furniture_list: list[FurnitureItemV1]
    package_info: list[int] = field(default_factory=lambda: [1, 1])
    timestamp: int = 0

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "adjust_furniture",
            "params": {
                "furniture_list": [f.to_json() for f in self.furniture_list],
                "package": self.package_info,
                "timestamp": self.timestamp,
            },
        }


@dataclass(frozen=True)
class SetRoomMetadataV1:
    """LIVE-CONFIRMED (chairstacker, real device: renamed "Master
    Bathroom" -> "Master Bathroom [roombapy-prime-test]" via
    verify_map_edit.py, confirmed in the real app, then reverted back
    -- also confirmed in the app). Not just decompilation-confirmed
    anymore; this specific structure has now been observed to actually
    work against a real robot, both directions (rename and revert).

    CONFIRMED (live APK decompilation, this session, down to the
    actual P2MapRoomMetadata$Serializer.serialize() call): params are
    {"room_id": ..., "room_metadata": {...}} under command
    "set_room_metadata" -- room_id sits alongside room_metadata, NOT
    nested inside it (the serializer reads value.getMetadata().getId()
    separately for the outer room_id). room_metadata itself has
    EXACTLY two possible keys, both written only when not None:
    "name" (str) and "type" (RoomCategory, see enums_common.py).
    Nothing else -- no id, no other fields -- goes into room_metadata.

    THE CURRENT APP'S ACTUAL ROOM-EDIT PATH: both room renaming AND
    room-category changes go through SetRoomMetadata now, not
    RenameRoomV1/SetRoomTypeV1 (see those classes' own docstrings for
    the deprecation finding -- SetRoomMetadata replaces BOTH of them).

    CONFIRMED CONSTRAINT: the underlying constructor requires at least
    one of name/type to be set (both individually may be None, but not
    both at once) -- enforced here too via __post_init__, so a caller
    gets a clear, immediate ValueError instead of a request the server
    would have to reject. A None field is OMITTED from room_metadata
    entirely (not sent as JSON null) -- this is a genuine partial-
    update: you can change just the name, just the category, or both,
    but never explicitly clear one back to empty this way.

    `type` uses RoomCategory (enums_common.py), NOT the RoomType used
    by SetRoomTypeV1 -- these are two unrelated enums for the same
    real-world concept, with different wire representations (int codes
    vs. snake_case strings). See RoomCategory's own docstring for why
    that distinction matters and the specific mistake it guards against
    (an earlier draft of this class conflated RoomType with the
    similarly-named-but-unrelated RegionType, caught before shipping --
    see CHANGELOG)."""

    room_id: str
    name: str | None = None
    room_type: RoomCategory | None = None

    def __post_init__(self) -> None:
        if self.name is None and self.room_type is None:
            raise ValueError(
                "SetRoomMetadataV1 requires at least one of name/room_type to be "
                "set -- the underlying API has no way to express \"change nothing\"."
            )

    def to_v1_command_body(self) -> dict[str, Any]:
        room_metadata: dict[str, Any] = {}
        if self.name is not None:
            room_metadata["name"] = self.name
        if self.room_type is not None:
            room_metadata["type"] = self.room_type.value
        return {
            "command": "set_room_metadata",
            "params": {"room_id": self.room_id, "room_metadata": room_metadata},
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



