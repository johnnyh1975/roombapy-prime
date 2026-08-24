"""A THIRD GENERATION EXISTS, AND IT IS MQTT RATHER THAN REST.

`P2MapV3Editor.sendEdit()` in app 3.0.0:

    {"method": "service.mapedit",
     "msgId": "<random int as string>",
     "params": {"map_id": "<id>", "data": {"value": <any JSON>}}}

published to `{irbt_prefix}/things/{blid}/editv3_req`, with answers on
`editv3_resp`.

**THERE ARE NO METHOD NAMES TO GUESS.** `method` is the constant
`service.mapedit`; the actual operation lives in `data.value`, a generic
`JsonElement` the Kotlin layer does not interpret. `P2MapV3Editor` is a
transport channel and nothing more -- which means the payloads inside it
are not discoverable from this APK at all.

CONFIRMED AGAINST APP 3.0.0 (August 2026), and it is now the ONLY
map-edit path in the app -- `edit_req`/`edit_resp` do not appear at
all. `MapEditV3Topics` builds `things/{assetId}/editv3_req` and
`editv3_resp`; the envelope is

    request   {method, msgId, params: {map_id, data: {value}}}
    response  {method, msgId, data}

with `method` a fixed `service.mapedit`. The nine operations and their
replies:

    setRenameRoomReq     -> setRenameRoomRsp
    setVirtualWallReq    -> setVirtualWallRsp
    setPermanentAreaReq  -> setPermanentAreaRsp
    delPermanentAreaReq  -> delPermanentAreaRes    (Res, not Rsp)
    setFurnitureReq      -> setFurnitureRsp
    setSillReq           -> setSillRsp
    setCarpetReq         -> setCarpetRsp
    getSchemDataReq      -> getSchemDataRsp
    setSchemDataReq      -> setSchemDataRsp

NOTE `delPermanentAreaRes`. Eight replies end `Rsp` and one ends `Res`
-- assume the pattern and one operation goes unanswered.

NOT WIRED UP HERE. This library edits maps over REST (`edit_map`), and
that path is field-confirmed working. But if 3.0.0 uses MQTT
exclusively, the REST path may be the legacy one -- which would also
explain why `AddCleanZones`/`DeleteCleanZones` were modelled from the
app's command set and never called.

**V3 CARRIES NINE OPERATIONS, NOT ONE.** An earlier reading here said
"exactly one operation today", counting `MapServiceHandler`: 34 methods,
of which only `deleteCleanZonesV3` and `observeV3EditResponses` name V3.

That counted the Kotlin BRIDGE, and the bridge is not where V3 lives.
`P2MapEditCommandType` in the Dart layer declares the operations, and
`map_service.dart` declares their answers:

    setRenameRoomReq     -> setRenameRoomRsp
    setVirtualWallReq    -> setVirtualWallRsp
    setPermanentAreaReq  -> setPermanentAreaRsp
    delPermanentAreaReq  -> delPermanentAreaRes   <- "Res", not "Rsp"
    setFurnitureReq      -> setFurnitureRsp
    setSillReq           -> setSillRsp
    setCarpetReq         -> setCarpetRsp
    getSchemDataReq      -> getSchemDataRsp
    setSchemDataReq      -> setSchemDataRsp

`delPermanentAreaRes` is the vendor's own typo; eight of nine end `Rsp`.
Anything matching responses has to accept both spellings.

TWO OF THEM HAVE NO V1 OR V2 EQUIVALENT AT ALL: `setSillReq`
(thresholds) and `setCarpetReq` (carpets). That inverts the note below
about `SetThresholds` being "gone from 3.0.0" -- thresholds did not
vanish, they MOVED to a channel this module does not speak. Carpets are
new outright.

So the earlier conclusion was wrong in both directions: V3 is more than
a single delete, and it is the only way to reach two map features.

WHAT HAS NOT CHANGED: nothing here sends V3, and the reason stands --
`data.value` is a generic `JsonElement` the Kotlin layer never
interprets, so the payload shapes are not discoverable from the
serialisers. The nine names are the operations; their bodies are not
known. That is a smaller gap than it was, not a closed one.

TWO COMMANDS HERE NO LONGER EXIST IN THE APP'S V2 PATH.
`EditMapV2Request$CommandV2$SetFloorTypes` and `$SetThresholds` are in
2.2.4 and gone from 3.0.0 -- only the READ side survives
(`FloorTypeFeature$Properties`), and
`PolicyZoneFeature$Properties.threshold_type` is gone too.

`SetFloorTypes` and `SetThresholds` below are kept. The app dropping a
command does not prove the robot rejects it, and neither has ever been
sent from here -- so removing them would trade an untested path for an
untested absence. What has changed is the expectation: if either fails
in the field, "the app moved this to V3" is now the first explanation to
consider.

Nothing in this module speaks V3. The V1 and V2 commands below are
verified against their own serialisers and are what this library sends.

WHAT THIS MEANS FOR A CALLER: if a robot ever refuses a V1/V2 edit, the
next thing to establish is whether it wants V3 -- and that can be
established WITHOUT a write, by watching whether the robot publishes on
`editv3_resp` at all.

Map edit commands -- both the V1 (actually used) and V2 (dead code) paths.

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

from enum import IntEnum, StrEnum

from .enums_common import FurnitureType, RoomCategory, RoomType
from .geometry import LineString, Polygon, Position


class MapEditingError(IntEnum):
    """Why the robot refused a map edit (app 3.0.0,
    `P2MapEditingErrorCode`).

    THE ONE VOCABULARY HERE THIS LIBRARY DOES NOT CONTROL. Everything
    else in this module is a command shape -- something we send, and can
    check before sending. These thirteen come BACK, and until now a
    failed edit was an unnamed integer in raw JSON.

    They fall into three groups, and the grouping is the useful part:

        NOT FOUND -- the thing you asked to change is gone. Someone
        else edited the map, or a stale id was reused.
            keepOutZoneNotFound · noMopZoneNotFound · virtualWallNotFound
            cleanZoneNotFound · furnitureNotFound

        INVALID -- the shape or type you sent is not acceptable.
            invalidRoomSplit · unexpectedRoomType · invalidVirtualWall
            invalidPermanentArea

        NOT NOW -- the request was fine and the robot could not act.
            editAppliedMapNotReady · emptyModifyRequest
            noAvailableIdentifier · unexpectedResponse

    WHY THAT MATTERS FOR A CALLER: only the middle group means "fix your
    request". A not-found is a race worth re-reading the map for, and a
    map-not-ready is worth retrying unchanged. Treating all three the
    same turns a transient refusal into a permanent failure.

    NOT WIRED INTO A RESPONSE PARSER, because neither edit path models
    its response at all -- both return raw JSON, and inventing a
    response envelope to hold this would be guessing at a shape no
    capture has shown. This names the codes so a caller reading that
    raw JSON has something better than a number."""

    UNEXPECTED_RESPONSE = 0
    INVALID_ROOM_SPLIT = 1
    UNEXPECTED_ROOM_TYPE = 2
    INVALID_VIRTUAL_WALL = 3
    INVALID_PERMANENT_AREA = 4
    KEEP_OUT_ZONE_NOT_FOUND = 5
    NO_MOP_ZONE_NOT_FOUND = 6
    VIRTUAL_WALL_NOT_FOUND = 7
    NO_AVAILABLE_IDENTIFIER = 8
    CLEAN_ZONE_NOT_FOUND = 9
    FURNITURE_NOT_FOUND = 10
    EMPTY_MODIFY_REQUEST = 11
    EDIT_APPLIED_MAP_NOT_READY = 12


@dataclass(frozen=True)
class MapEditResult:
    """What came back from a map edit, in whichever of four shapes.

    THIS WAS RAW JSON, AND THE REASON GIVEN FOR THAT WAS WRONG. Both
    edit paths returned an undecoded dict, documented here as "response
    shape not modelled" and, later, as not modellable at all -- the
    payloads were said to be undiscoverable. That was a claim about
    V3's `data.value`, applied to V1 and V2 where it does not hold.

    The serialiser extract carries all four:

        P2MapURL                  map_url
        P2MapEditSuccessFallback  status · map_url · p2mapv_id ·
                                  p2map_metadata
        P2MapEditPartialSuccess   status · p2mapv_id · p2map_metadata
        P2MapError                code · message

    PARTIAL SUCCESS IS THE ONE WORTH HAVING. It carries a new map
    version and no URL -- the edit took, the rendered map did not
    follow. A caller treating any non-error as done would show a stale
    map and never know; a caller treating it as failure would retry an
    edit that already applied.

    `code` CARRIES `MapEditingError`, which is what connects those
    thirteen names to a field a robot actually fills.

    THE MESSAGE ARRIVES TWO WAYS. `P2MapError.message` is the plain
    one; `P2MapError$MessageContainer` wraps a capital-M `Message`,
    which is AWS API Gateway's shape -- the same envelope the
    firmware-catalogue 403 came back in. Both are read.

    NOT FIELD-CONFIRMED. No capture this project holds contains a map
    edit response of any shape, because nothing here has ever sent one
    outside a dry run. Every field is permissively typed for that
    reason, and `raw` keeps the whole payload so a first real response
    can be compared against this rather than lost."""

    map_url: str | None = None
    #: The vendor's `MapEditResult` enum -- success, fail, cancel -- is
    #: the likeliest content, and `MapEditStatus` names it. Left `Any`
    #: because no capture has shown a `status` value, and typing a field
    #: on a guess is how `pad_category` silently became a string.
    status: Any | None = None
    map_version_id: str | None = None
    map_metadata: Any | None = None
    error_code: int | None = None
    error_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.error_code is not None or self.error_message is not None

    @property
    def is_partial(self) -> bool:
        """A new map version without a URL: the edit applied and the
        rendered map did not follow."""
        return (
            not self.is_error
            and self.map_version_id is not None
            and self.map_url is None
        )

    @property
    def error(self) -> MapEditingError | None:
        """The vendor's name for `code`, where it recognises one.

        None for an unknown code rather than raising: the server may add
        one, and losing the whole result over an unfamiliar number would
        be the same mistake that emptied an account's favourites."""
        if self.error_code is None:
            return None
        try:
            return MapEditingError(self.error_code)
        except ValueError:
            return None

    @classmethod
    def from_json(cls, data: Any) -> MapEditResult:
        """DELEGATES TO THE EXISTING SHAPE CLASSES rather than re-reading
        the payload.

        `P2MapEditPartialSuccess` and `P2MapEditSuccessFallback` have
        been in robot_info.py since session 51, added as "three
        separately-found response classes, not a resolved discriminated
        union" for callers willing to guess which one applied. Nothing
        ever called them, and nothing decided between them.

        The first version of this method parsed the same four fields
        again -- a second parsing site for one payload, which is
        precisely the duplicate that made `pad_category` silently stay a
        string for months. What was actually missing was the
        DISCRIMINATION and the wiring, so that is all this adds.

        The error shape has no class: `P2MapError` was named in the
        extract but never modelled, so its two fields are read here."""
        from .robot_info import (  # noqa: PLC0415
            P2MapEditPartialSuccess,
            P2MapEditSuccessFallback,
        )

        if not isinstance(data, dict):
            return cls()

        # The fallback shape is the wider of the two and reads every
        # field the partial one does, so it parses both.
        success = (
            P2MapEditSuccessFallback.from_json(data)
            if "map_url" in data
            else P2MapEditPartialSuccess.from_json(data)
        )
        message = data.get("message")
        if message is None:
            container = data.get("Message")
            message = container if isinstance(container, str) else None
        return cls(
            map_url=getattr(success, "map_url", None) or data.get("map_url"),
            status=success.status,
            map_version_id=success.p2mapv_id,
            map_metadata=success.p2map_metadata,
            error_code=data.get("code"),
            error_message=message,
            raw=dict(data),
        )


class MapEditStatus(StrEnum):
    """The vendor's `MapEditResult` (app 3.0.0): success, fail, cancel.

    THE LIKELIEST CONTENT OF `MapEditResult.status`, which this library
    types as `Any` because no capture has shown one. Three plain words,
    and `cancel` is the interesting third: an edit neither applied nor
    rejected, which a two-way success/failure reading would have to
    force into one or the other.

    NAMED `MapEditStatus`, NOT `MapEditResult`. The vendor calls this
    enum `MapEditResult`, and the response class in this module already
    carries that name -- chosen before this enum was read, and it means
    something different: the whole parsed response rather than its
    outcome word. Two things called the same name in one module would
    be exactly the confusion `PadCategory` caused.

    Not applied to the field. A capture carrying `status` would settle
    whether these three are what arrives there; until then the raw value
    passes through and this names the candidate."""

    SUCCESS = "success"
    FAIL = "fail"
    CANCEL = "cancel"


class MapVerifyResult(IntEnum):
    """Why a map edit failed verification before sending (app 3.0.0).

    A SIXTH CLIENT-SIDE CHECK, alongside the six `*InvalidReason` enums
    merged into MapEditRejectionReason -- and this one is numeric rather
    than snake_case strings, so it could not be merged with them.

    `overlapWithVirtual` names something the string reasons do not: an
    area overlapping a VIRTUAL WALL specifically, as opposed to the
    generic `overlap`."""

    SUCCESS = 0
    AREA_WITHIN_MAP_SMALL = 1
    OUT_MAP = 2
    EMPTY = 4
    OVERLAP_WITH_VIRTUAL = 5


class MapEditRejectionReason(StrEnum):
    """Why the APP declines to send a map edit, before the robot sees it
    (app 3.0.0, six `*InvalidReason` enums merged).

    DELIBERATELY NOT ENFORCED HERE, and the distinction from
    MapEditingError above is the whole point: those come back from the
    robot, these are the vendor's own client-side validations.

    Enforcing them would mean geometry this library does not do --
    overlap tests, minimum areas, room adjacency. Doing that badly would
    reject valid edits, which is worse than forwarding one the robot
    declines.

    So they are documented rather than implemented, as the list of what
    a robot is expected to refuse:

        outside_map · overlap · overlap_invalid_area · overlap_with_dock
        zone_too_small · room_too_small · threshold_too_short
        invalid_room_shape · rooms_not_adjacent · less_than_two_rooms
        map_not_ready

    `map_not_ready` appears in three of the six source enums and is the
    only one that is purely temporal -- worth retrying rather than
    fixing."""

    OUTSIDE_MAP = "outside_map"
    OVERLAP = "overlap"
    OVERLAP_INVALID_AREA = "overlap_invalid_area"
    OVERLAP_WITH_DOCK = "overlap_with_dock"
    ZONE_TOO_SMALL = "zone_too_small"
    ROOM_TOO_SMALL = "room_too_small"
    THRESHOLD_TOO_SHORT = "threshold_too_short"
    INVALID_ROOM_SHAPE = "invalid_room_shape"
    ROOMS_NOT_ADJACENT = "rooms_not_adjacent"
    LESS_THAN_TWO_ROOMS = "less_than_two_rooms"
    MAP_NOT_READY = "map_not_ready"
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
    """Add zones -- **and rename them**. There is no rename command.

    APK 3.0.0, `updateCleanZones` on the `data.sdk/map` Flutter channel:
    for each item it reads `zone, id, name, geometry`, looks up an
    `existingId`, collects zones plus `retainIds`, and deletes
    everything not retained. On the V2 path that becomes
    `AddCleanZones` + `DeleteCleanZones`.

    So **renaming is AddCleanZones carrying an existing `zone_id` and a
    new `name`.** `CleanZone.zone_id` is optional precisely for this:
    omit it to create, supply it to update.

    ROOMS TAKE A DIFFERENT PATH: `setRoomMetadata` / `setRenameRoom`.
    That is why @chairstacker can rename rooms and not zones -- two
    separate mechanisms behind one app surface.

    NOT WIRED UP HERE. Nothing in this library calls this class, so a
    caller wanting to name a zone has to build the command itself.
    """

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
    ring = list(polygon.coordinates[0]) if polygon.coordinates else []

    # take(4): EXACTLY the first four points, unconditionally.
    #
    # CORRECTED (this session) from a "drop the closing point if the ring
    # is closed" rule. Both produce identical output for a rectangle read
    # out of policyZones -- a closed 5-point ring becomes the same 4
    # points either way, which is why field payloads looked right.
    #
    # They differ on any ring that is not a closed rectangle: a 5-point
    # open polygon produced 12 elements here and 10 in the app. The wire
    # format is fixed at [id, type, 4 (x,y) pairs], and the app enforces
    # that by truncating rather than by trusting its input.
    #
    # From APK analysis of the custom VirtualWall serializer:
    #   polygon.coordinates[0].coordinates.take(4) -> add(x), add(y)
    #
    # Only coordinates[0], the outer ring. Interior rings (holes) are
    # ignored -- which matters because a Polygon can carry them and
    # nothing else in this file would have dropped them.
    ring = ring[:4]

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
    was not previously re-examined once envelope work started.

    FIELD-CONFIRMED ON HARDWARE (@bryznnguyen, Combo 105 / SKU
    G284020, x05 generation, three runs on b7): `edit_map_checked`
    returned the SUCCESS shape carrying a rendered map URL each time
    -- not `is_error`, and not the `is_partial` "new version, no
    render" case. The divide line was a user-drawn polyline
    un-projected from the map's meter space.

    WHAT THAT DOES AND DOES NOT ESTABLISH, in his own framing: the
    server accepts and applies this command and re-renders. He
    verified at the RESPONSE level only and did not audit that the
    resulting boundaries match what was drawn. So the envelope, the
    discriminator and the flat split_points shape are confirmed; the
    geometry is not. A caller drawing a line should still check the
    result on the rendered map.

    This was previously "never run" -- it had been decompiled and
    modelled but nobody had sent one. Someone did.
    """

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
    suggest).

    FIELD-CONFIRMED ON HARDWARE (@bryznnguyen, Combo 105 / SKU
    G284020, one run on b7): same success-with-rendered-URL shape as
    the split. THE DISCRIMINATOR IS THE PART THIS VALIDATES -- a
    decompiled string that reads wrong against its own class name is
    exactly the kind that gets "corrected" by a well-meaning reader.
    `arrange_room` is right, and a live robot has now acted on it.

    Response-level confirmation only, same caveat as SplitRoomV1: the
    server accepted and re-rendered; whether the merged boundary is
    geometrically what was intended was not audited.

    NOW CONFIRMED A THIRD TIME, from firmware 3.8.126. The broker's
    local RPC namespace lists `service.arrange_room` alongside
    `service.rename_room`, `service.split_room` and
    `service.rename_map`. So the discriminator that contradicts its
    own class name is attested in the app bytecode, on a live robot,
    and in the robot's own firmware. It is not a decompilation
    artefact, and nobody should "fix" it.
    """

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
        """THIS REPLACES THE ENTIRE LIST, and the list is shared.

        Confirmed by a second APK read (this session), of
        P2MapAPIZoneEditing: every write follows the same shape --
        fetchLatestPersistentMap(), then getKeepOutZones() +
        getNoMopZones() + getVirtualWalls(), then send the COMBINED
        list.

        All three zone kinds live in one `virwall` array. Sending only
        the wall you want to add therefore deletes every keep-out zone
        and no-mop zone the robot had. Any add/remove helper built on
        this must read first, merge, and send everything back.

        The app also assigns ids itself, via
        getNextVirtualWallID(existing) -- there is no server-side
        allocation, so a caller adding a wall has to pick a free id
        from the current list.

        NOT the explanation for the HTTP 500 seen in the field: the
        verify script's --update-unchanged already reads the full list
        and resends it untouched, which is exactly this pattern, and it
        still fails."""
        return {
            "command": "set_virtual_wall",
            # THE FIRST ELEMENT IS A COUNT, not a wall.
            #
            #     "virwall": [2, [...], [...]]
            #
            # Confirmed from the app's CommandSerializer bytecode: it
            # builds one JsonArray, adds walls.size() as an Int, and
            # only then appends the wall arrays.
            #
            # This is why every write returned HTTP 500 while the body
            # was valid JSON -- the server reads position 0 expecting a
            # number and finds an array, so it parses and then fails
            # deserialising. And it is why no field test could narrow it
            # down: wall count, zone types, account and map version were
            # all irrelevant, because the payload failed at element
            # zero before any of them mattered.
            #
            # NOT a general convention. adjust_furniture is equally
            # list-based and has no counter; set_virtual_wall carries
            # the only .size() call in the whole serializer.
            #
            # The count must match the walls actually sent. Callers that
            # send a partial list therefore change two things at once --
            # which is exactly why partial writes delete the rest (see
            # this method's own note about replacing the whole list).
            "params": {
                "virwall": [len(self.walls), *(w.to_json() for w in self.walls)]
            },
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

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#map_editingsetroommetadatav1
    """

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



