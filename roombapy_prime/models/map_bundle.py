"""Map bundle read models -- what's actually IN a downloaded map bundle.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field.
PARSER ROBUSTNESS. Every `from_json` here returns an empty instance
rather than raising when handed something that is not a mapping -- a
truncated download, a server error body, a `None` where a feature was
expected.

The exception is the classes with REQUIRED fields (`CleanZoneFeature`,
`BorderFeature` and the other GeoJSON features): they cannot construct
an empty instance, and inventing one would put a feature with no id and
no geometry into a render list. Those still raise, which is the honest
answer -- a feature that is nothing is not an empty feature.

Nothing in this library calls those directly; they are reachable only
by a caller unpacking a bundle itself.
"""
from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from typing import Any

from .enums_common import FurnitureType, _enum_or_none
from .geometry import (
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    _linestring_from_geojson,
    _multipolygon_from_geojson,
    _point_from_geojson,
    _polygon_from_geojson,
)


class RoomTypeSource(StrEnum):
    """Confirmed from P2MapRoomInfo$RoomType$Source -- HOW a room type
    came about (detected vs. set by the user). Exact string values not
    confirmed 1:1 (enum names yes, wire string serialization not
    explicitly seen in the code) -- filled in here as a placeholder
    with the enum names themselves, not as confirmed wire strings."""

    DETECTED = "DETECTED"
    USER_SET = "USER_SET"


class HazardType(StrEnum):
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
class RoomFeatureProperties:
    """CONFIRMED (session 47) via RoomFeature$Properties$$serializer's
    <clinit>: adjacentRoomIDs, name, type, simplifiedGeometry.

    room_type deliberately left as a raw value (str | int | None), NOT
    the numeric RoomType IntEnum used by the edit-side SetRoomType
    command: a quick sanity check found that reusing RoomType here
    breaks on a plausible string value ("BEDROOM"), since RoomType's
    confirmed values are the numeric edit-side codes (2100-2120), not
    strings. Whether the read side actually reports room type as one
    of those same numeric codes, or as a human-readable string enum
    of its own (not modeled here, no values confirmed), is unresolved
    -- only the FIELD NAME ("type") is bytecode-confirmed, not which
    value space it uses.

    NEW FIELD, this session: visibility -- confirmed as a real key from
    a live map bundle (chairstacker, structure-only inspection: field
    NAMES only, no values shared, see the person's own privacy
    preference noted earlier this session). Not in the original
    <clinit> list above (that confirmation predates this capture) --
    genuinely new, not a correction. Left as a raw, unconfirmed value
    (Any | None), same conservative treatment as room_type: only the
    field NAME is confirmed here, not its value space (plausible
    guesses would be a bool or a "visible"/"hidden"-style string enum,
    but nothing here confirms which)."""

    name: str | None = None
    room_type: Any | None = None
    simplified_geometry: Polygon | None = None
    adjacent_room_ids: list[str] = field(default_factory=list)
    visibility: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        simplified = data.get("simplifiedGeometry")
        return cls(
            name=data.get("name"),
            room_type=data.get("type"),
            simplified_geometry=_polygon_from_geojson(simplified) if simplified else None,
            adjacent_room_ids=data.get("adjacentRoomIDs") or [],
            visibility=data.get("visibility"),
        )


@dataclass(frozen=True)
class RoomFeature:
    """REBUILT (session 47) -- REPLACES the previous flat `RoomInfo`.
    CONFIRMED via RoomFeature$$serializer's <clinit>: this is a
    standard GeoJSON Feature ({type, id, geometry, properties}), not a
    flat object -- see this module section's header comment for the
    full story. `feature_type` is presumed "Feature" (standard GeoJSON
    convention), not independently confirmed as a literal string."""

    feature_id: str
    geometry: Polygon
    properties: RoomFeatureProperties = field(default_factory=RoomFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            properties=RoomFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class BorderFeature:
    """REBUILT (session 47) -- REPLACES `BorderInfo`. CONFIRMED via
    BorderFeature$$serializer AND BorderFeature$Properties$$serializer
    (the latter has NO custom fields beyond the shared Feature
    envelope -- confirmed empty, not an oversight)."""

    feature_id: str
    geometry: MultiPolygon
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BorderFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_multipolygon_from_geojson(data.get("geometry") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class TrajectoryFeatureProperties:
    """CONFIRMED (session 47): index, operatingModes."""

    index: int | None = None
    operating_modes: list[Any] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TrajectoryFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(index=data.get("index"), operating_modes=data.get("operatingModes") or [])


@dataclass(frozen=True)
class TrajectoryFeature:
    """REBUILT (session 47) -- REPLACES `TrajectoryInfo`. CONFIRMED via
    TrajectoryFeature$$serializer/TrajectoryFeature$Properties$$serializer."""

    feature_id: str
    geometry: LineString
    properties: TrajectoryFeatureProperties = field(default_factory=TrajectoryFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TrajectoryFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_linestring_from_geojson(data.get("geometry") or {}),
            properties=TrajectoryFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class CoverageFeatureProperties:
    """CONFIRMED (session 47): operatingModes."""

    operating_modes: list[Any] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CoverageFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(operating_modes=data.get("operatingModes") or [])


@dataclass(frozen=True)
class CoverageFeature:
    """REBUILT (session 47) -- REPLACES `CoverageInfo`. CONFIRMED via
    CoverageFeature$$serializer/CoverageFeature$Properties$$serializer."""

    feature_id: str
    geometry: MultiPolygon
    properties: CoverageFeatureProperties = field(default_factory=CoverageFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CoverageFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_multipolygon_from_geojson(data.get("geometry") or {}),
            properties=CoverageFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class DockFeatureProperties:
    """CONFIRMED (session 47): orientation."""

    orientation: float | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(orientation=data.get("orientation"))


@dataclass(frozen=True)
class DockFeature:
    """REBUILT (session 47) -- REPLACES `DockInfo`. CONFIRMED via
    DockFeature$$serializer/DockFeature$Properties$$serializer --
    position as Point, not Polygon."""

    feature_id: str
    geometry: Point
    properties: DockFeatureProperties = field(default_factory=DockFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_point_from_geojson(data.get("geometry") or {}),
            properties=DockFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class HazardFeatureProperties:
    """CONFIRMED (session 47): type (HazardType)."""

    hazard_type: HazardType | str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HazardFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(hazard_type=_enum_or_none(HazardType, data.get("type")))


@dataclass(frozen=True)
class HazardFeature:
    """REBUILT (session 47) -- REPLACES `HazardInfo`. CONFIRMED via
    HazardFeature$$serializer/HazardFeature$Properties$$serializer --
    position as Point."""

    feature_id: str
    geometry: Point
    properties: HazardFeatureProperties = field(default_factory=HazardFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HazardFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_point_from_geojson(data.get("geometry") or {}),
            properties=HazardFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class FurnitureFeatureProperties:
    """CONFIRMED (session 47): type, source, orientation, cleaningArea
    -- these are the same two fields (orientation, cleaningArea) that
    an earlier session had already correctly identified as belonging
    to the read model rather than the edit command, now additionally
    bytecode-confirmed at the exact wire-key level, plus two more
    fields (type, source) not previously modeled at all."""

    furniture_type: FurnitureType | int | None = None
    source: str | None = None
    orientation: float | None = None
    cleaning_area: Polygon | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FurnitureFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        cleaning_area = data.get("cleaningArea")
        raw_type = data.get("type")
        furniture_type = FurnitureType(raw_type) if isinstance(raw_type, int) else raw_type
        return cls(
            furniture_type=furniture_type,
            source=data.get("source"),
            orientation=data.get("orientation"),
            cleaning_area=_polygon_from_geojson(cleaning_area) if cleaning_area else None,
        )


@dataclass(frozen=True)
class FurnitureFeature:
    """REBUILT (session 47) -- REPLACES `FurnitureInfoRead`. CONFIRMED
    via FurnitureFeature$$serializer/FurnitureFeature$Properties$$serializer."""

    feature_id: str
    geometry: Polygon
    properties: FurnitureFeatureProperties = field(default_factory=FurnitureFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FurnitureFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            properties=FurnitureFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class FloorPlanFeatureProperties:
    """NEW (session 47) -- not previously modeled at all. CONFIRMED:
    type, roomId."""

    floor_type: str | None = None
    room_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FloorPlanFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(floor_type=data.get("type"), room_id=data.get("roomId"))


@dataclass(frozen=True)
class FloorPlanFeature:
    """NEW (session 47) -- not previously modeled at all. CONFIRMED via
    FloorPlanFeature$$serializer/FloorPlanFeature$Properties$$serializer."""

    feature_id: str
    geometry: Polygon
    properties: FloorPlanFeatureProperties = field(default_factory=FloorPlanFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FloorPlanFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            properties=FloorPlanFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class PolicyZoneFeatureProperties:
    """CONFIRMED there is actually just ONE feature type ("PolicyZone")
    covering keep-out zones, no-mop zones, AND virtual walls --
    discriminated by `zone_type`/`threshold_type` plus geometry shape,
    not by being separate classes.

    zone_type FULLY CONFIRMED (parallel native-analysis track,
    P2MapBundleContentHolderPersistentMapKt's own extension functions
    -- the actual code that builds P2PersistentMap's three separate
    typed lists from this one raw list): "KeepOutZone" and "NoMopZone"
    are the only two real values -- there is NO separate "VirtualWall"
    string. A virtual wall is a "KeepOutZone"-typed feature whose
    geometry happens to be a LineString instead of a Polygon -- the
    real app discriminates by GEOMETRY SHAPE for this one case, not
    by an additional type value. See PolicyZoneFeature's own docstring
    for the full, confirmed categorization rule.

    threshold_type: a third real value, "Threshold", also exists on
    this same feature type (not just keep-out/no-mop) -- confirmed
    real app code parses this via a Status enum with a DETECTED
    fallback for unknown/missing values, but the Status enum's own
    member names weren't extracted."""

    zone_type: str | None = None
    #: REMOVED IN APP 3.0.0. `PolicyZoneFeature$Properties` no longer
    #: declares it -- the only field iRobot dropped rather than renamed
    #: between 2.2.4 and 3.0.0.
    #:
    #: Kept, because a robot on older firmware may still send it and
    #: this is a read path: an unread field costs nothing, a dropped one
    #: costs whatever it carried.
    threshold_type: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PolicyZoneFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(zone_type=data.get("type"), threshold_type=data.get("threshold_type"))


def _policy_zone_geometry_from_geojson(data: dict[str, Any]) -> Polygon | LineString:
    """PolicyZoneFeature's geometry is Polygon for keep-out/no-mop
    zones but LineString for virtual walls -- see that class's own
    docstring for the confirmed evidence. Dispatches on the GeoJSON
    object's own "type" key rather than assuming Polygon
    unconditionally (an earlier version of this parser did exactly
    that, which would have silently mis-parsed any real virtual-wall
    feature -- LineString's own flat coordinate list read as if it
    were Polygon's list-of-rings shape)."""
    if data.get("type") == "LineString":
        return _linestring_from_geojson(data)
    return _polygon_from_geojson(data)


class PolicyZoneCategory(StrEnum):
    """NEW (this session): makes PolicyZoneFeature's already-CONFIRMED
    categorization rule actually APPLICABLE, instead of leaving every
    consumer to re-derive it from prose.

    The rule was fully documented on PolicyZoneFeature but never
    implemented anywhere, and one branch of it is genuinely
    counter-intuitive: a VIRTUAL WALL is not its own zone_type. It is a
    "KeepOutZone"-typed feature whose GEOMETRY happens to be a
    LineString rather than a Polygon. Anyone implementing this from the
    field names alone would almost certainly miss that and silently
    treat virtual walls as keep-out zones."""

    KEEP_OUT_ZONE = "keep_out_zone"
    VIRTUAL_WALL = "virtual_wall"
    NO_MOP_ZONE = "no_mop_zone"
    THRESHOLD = "threshold"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PolicyZoneFeature:
    """CONFIRMED via PolicyZoneFeature$$serializer/
    PolicyZoneFeature$Properties$$serializer, AND via the actual
    categorization code (P2MapBundleContentHolderPersistentMapKt's own
    extension functions building P2PersistentMap's three separate
    typed lists from this single raw "policyZones" list) -- the
    complete, confirmed rule:

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#map_bundlepolicyzonefeature
    """

    feature_id: str
    geometry: Polygon | LineString
    properties: PolicyZoneFeatureProperties = field(default_factory=PolicyZoneFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PolicyZoneFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_policy_zone_geometry_from_geojson(data.get("geometry") or {}),
            properties=PolicyZoneFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )

    @property
    def category(self) -> PolicyZoneCategory:
        """Applies the confirmed categorization rule -- see
        PolicyZoneCategory's own docstring for why the virtual-wall
        branch in particular is worth having in code rather than in
        prose. Returns UNKNOWN rather than guessing for anything the
        confirmed rule doesn't cover; the real app skips such features
        silently too, so an unrecognized value is a normal condition,
        not an error."""
        zone_type = self.properties.zone_type
        if zone_type == "Threshold":
            return PolicyZoneCategory.THRESHOLD
        if zone_type == "NoMopZone":
            return PolicyZoneCategory.NO_MOP_ZONE
        if zone_type == "KeepOutZone":
            # THE non-obvious branch: geometry shape, not type string.
            if isinstance(self.geometry, LineString):
                return PolicyZoneCategory.VIRTUAL_WALL
            return PolicyZoneCategory.KEEP_OUT_ZONE
        return PolicyZoneCategory.UNKNOWN


@dataclass(frozen=True)
class CleanZoneFeatureProperties:
    """CONFIRMED (session 47): name (the one field that distinguishes
    this from AdHocCleanZoneFeature, which has none).

    FIVE MORE FIELDS EXIST ON THE SDK'S OWN ZONE MODEL, and they are
    deliberately not added here yet.

    `models/map_data/map_contents/p2_map_zone_info` (app 3.0.0) declares
    `detected`, `detectedAccepted`, `detectedDeleted`, `detected_viewed`
    and `user_created` beside `geometry`, `id` and `status`. Together
    they are the keep-out-zone RECOMMENDATION mechanism gated by
    `digiCap.kozRecommendations`: whether the robot proposed a zone,
    whether the user accepted, rejected or merely saw it, and whether
    the user drew it themselves.

    WHY THEY MIGHT BE WIRE KEYS: `p2_map_zone_info` is the SDK's model
    for the same object this class parses, and its five extra fields
    have no counterpart here.

    AN EARLIER VERSION OF THIS NOTE ARGUED FROM THE CASING -- that
    `detectedAccepted` beside `detected_viewed` inside one class proved
    these were serialisation names, since a Dart property list would be
    uniformly camelCase. **That argument does not hold.**
    `message_center_models.dart` carries 53 fields in BOTH spellings at
    once, camelCase for the Dart property and snake_case for the wire,
    and the vendor's generated code keeps both. Mixed casing inside one
    model is that pairing maintained incompletely -- not evidence of
    anything. Where it means anything at all, snake_case is the wire
    form.

    So the casing says nothing either way, and the reason for caution is
    unchanged: this class is built from the GeoJSON bundle, whose
    serialiser (`CleanZoneFeature$Properties`) declares `name` and
    nothing else. The SDK model and the bundle feature are not confirmed
    to be the same object, and adding five fields to the wrong one is
    how a permanent None gets created. A real bundle carrying any of
    them settles it in one look."""

    name: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanZoneFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(name=data.get("name"))


@dataclass(frozen=True)
class CleanZoneFeature:
    """REBUILT (session 47) -- REPLACES `CleanZoneInfoRead`. CONFIRMED
    via CleanZoneFeature$$serializer/CleanZoneFeature$Properties$$serializer."""

    feature_id: str
    geometry: Polygon
    properties: CleanZoneFeatureProperties = field(default_factory=CleanZoneFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanZoneFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            properties=CleanZoneFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class AdHocCleanZoneFeature:
    """REBUILT (session 47) -- REPLACES `AdHocCleanZoneInfo`. CONFIRMED
    via AdHocCleanZoneFeature$$serializer -- Properties confirmed EMPTY
    (no custom fields beyond the shared Feature envelope), unlike
    CleanZoneFeature which has `name`."""

    feature_id: str
    geometry: Polygon
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AdHocCleanZoneFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class FloorTypeFeatureProperties:
    """NEW (session 47) -- not previously modeled at all (this bundle
    content type is itself under an "experimental" package in the
    decompiled source, consistent with being a newer/less-stable
    feature). CONFIRMED: type.

    FIELD-CONFIRMED 30 July 2026 (chairstacker), and the wire key is
    worth restating because it is a trap: the JSON key is `type`, not
    `floor_type`. This class names the attribute `floor_type` for
    readability, since a GeoJSON Feature already has three other `type`
    keys around it -- the FeatureCollection's, the Feature's and the
    geometry's.

    That naming cost a tester a moment: asked for `"floor_type"` he found
    nothing and correctly tried `"type"` instead. Worth remembering when
    writing grep-style instructions -- the attribute name here is ours,
    not the robot's.

    OBSERVED VALUES so far: only `"carpet"`. A real capture of four
    features on one map had carpet for all of them, which suggests the
    file lists carpeted areas rather than classifying every surface --
    i.e. anything not covered by a feature is hard floor by omission.
    Not confirmed: a robot with no carpet at all would settle it, since
    the file would then be empty or absent."""

    floor_type: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FloorTypeFeatureProperties:
        if not isinstance(data, dict):
            return cls()
        return cls(floor_type=data.get("type"))


@dataclass(frozen=True)
class FloorTypeFeature:
    """NEW (session 47), EXPERIMENTAL per its own package name in the
    decompiled source. CONFIRMED via
    experimental.FloorTypeFeature$$serializer/
    experimental.FloorTypeFeature$Properties$$serializer."""

    feature_id: str
    geometry: Polygon
    properties: FloorTypeFeatureProperties = field(default_factory=FloorTypeFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FloorTypeFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            properties=FloorTypeFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class ManifestFeature:
    """NEW (session 47). CONFIRMED via Manifest$Feature$$serializer:
    type (the content-type discriminator, e.g. presumably "rooms"/
    "borders"/etc. -- exact strings not confirmed, no enum found),
    filepath (the ACTUAL FILENAME within the tar.gz bundle for this
    content type -- this DEFINITIVELY resolves the "exact file naming"
    question open since the fifth session), schemaVersion."""

    content_type: str | None = None
    filepath: str | None = None
    schema_version: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ManifestFeature:
        if not isinstance(data, dict):
            return cls()
        return cls(
            content_type=data.get("type"),
            filepath=data.get("filepath"),
            schema_version=data.get("schemaVersion"),
        )


@dataclass(frozen=True)
class BundleMetadataSource:
    """NEW (session 47). CONFIRMED via
    Metadata$PICEASourceMetadata$$serializer: missionStartTime,
    mapUploadTime, type. "PICEA" is presumably an internal codename for
    the mapping/localization subsystem -- not otherwise investigated."""

    mission_start_time: int | None = None
    map_upload_time: int | None = None
    source_type: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BundleMetadataSource:
        if not isinstance(data, dict):
            return cls()
        return cls(
            mission_start_time=data.get("missionStartTime"),
            map_upload_time=data.get("mapUploadTime"),
            source_type=data.get("type"),
        )


@dataclass(frozen=True)
class BundleManifest:
    """NEW (session 47) -- the bundle's own index/table-of-contents
    file. CONFIRMED via Manifest$$serializer: metadata, features (a
    list of ManifestFeature, each naming a content type's real
    filepath within the bundle -- see ManifestFeature's docstring),
    experimentalFeatures (same shape, for newer/less-stable content
    types like FloorTypeFeature).

    CONFIRMED (session 57, real live bundle, chairstacker): this
    manifest file's OWN filename within the tar.gz is literally
    "manifest" -- previously unconfirmed, now settled.

    CORRECTED (session 57): the same real bundle's manifest.json had
    `"metadata"` as a bare STRING value, not a nested object as this
    class previously assumed (`dict[str, Any]`) -- typed as `Any` now
    to honestly reflect that its actual shape isn't a dict. Likely a
    version string or reference ID for this specific manifest entry,
    distinct from the SEPARATE "metadata" FILE in the same bundle
    (BundleMetadataSource) -- not further investigated."""

    metadata: Any = None
    features: list[ManifestFeature] = field(default_factory=list)
    experimental_features: list[ManifestFeature] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BundleManifest:
        if not isinstance(data, dict):
            return cls()
        return cls(
            metadata=data.get("metadata"),
            features=[ManifestFeature.from_json(f) for f in (data.get("features") or [])],
            experimental_features=[ManifestFeature.from_json(f) for f in (data.get("experimentalFeatures") or [])],
        )


KNOWN_BUNDLE_INFO_TYPES = frozenset({
    "rooms", "borders", "floorPlan", "dockPose", "floorTypes",
    "coverage", "cleanZones", "hazard", "trajectories",
    "adHocCleanZones", "furniture", "policyZones",
})
"""CORRECTED (session 57): confirmed via a real live map bundle
(chairstacker, --dump-config) that the actual filename is "dockPose"
(singular), not "dockPoses" as previously guessed -- this constant is
purely a reference/documentation set (not used to gate any parsing
logic elsewhere in this file), so the fix has no functional impact,
just corrects the record. The same real bundle also confirmed two
purely structural files outside this content-type set: "manifest"
(the table-of-contents) and "metadata" (mission/source metadata) --
both already modeled separately (BundleManifest/BundleMetadataSource)
and correctly not included here, since they aren't "content types" in
the same sense as rooms/borders/etc.

NEW (this session, a second real live bundle, chairstacker): "policyZones"
confirmed as an actual content type present in a real bundle
(policyZones.geojson) -- not previously in this set at all. This same
capture's file listing was smaller than the session-57 one (5 files:
borders/manifest/metadata/policyZones/rooms vs. the earlier capture's
8) -- plausibly just reflecting that this particular map has fewer
content types actually configured/present, not a contradiction; bundle
contents are expected to vary per-map. Meaning of "policyZones" itself
not further investigated -- conceptually plausible overlap with
permanent-area/keep-out-zone concepts from the map-editing work
(PermanentArea/VirtualWall), but that association is speculation, not
confirmed."""


# NOTE (this session, for future contributors -- documents the rest of the
# live-map exchange found in the same capture as LiveMapUpdate above, not
# modeled as dataclasses since none of it needs to be CONSTRUCTED by this
# library -- we only ever observe it, never send it):
#
# The robot's own upload side, two distinct xferTypes seen:
#   {"uploadP2MapLive": {"missionId": ..., "nMssn": ..., "p2maps":
#    [{"p2map_id": ..., "p2mapv_id": ...}]}, "xferId": <int>,
#    "xferType": "uploadP2MapLive"}
#   {"uploadP2MapMission": {...same shape...}, "xferId": <int>,
#    "xferType": "uploadP2MapMission"}
# "uploadP2MapLive" recurs throughout an active mission (periodic
# snapshots); "uploadP2MapMission" was observed exactly once, right
# after the mission concluded ("fin") -- consistent with "Live" meaning
# in-progress snapshots and "Mission" meaning the final, complete map.
#
# xferId's own meaning: it's int(unix_timestamp) at the moment the
# transfer was initiated -- e.g. xferId=1784491542 decodes to
# 2026-07-19 20:05:42 UTC, matching the SAME message's own p2mapv_id of
# "260719T200542.799" to the second (p2mapv_id carries millisecond
# precision, xferId only whole seconds). Not an opaque/random
# correlation ID as the name might suggest.
#
# PRECISION CAVEAT (this session, jayjay13011, a third independent
# capture): checked across 17 examples this time, not just a handful --
# 16 matched exactly, but one (xferId=1784559187) was off by exactly
# ONE second from its own p2mapv_id's timestamp component
# (...T145306.144 rounds to :06, xferId decodes to :07). Earlier
# phrasing here implied an unconditional exact match across every
# example checked (true for the smaller samples checked before this
# capture) -- with a larger sample, "almost always exact, occasionally
# off by one second" is the more honest characterization. Plausibly
# explained by the two values being independently generated a few
# milliseconds apart for the same logical event, straddling a
# whole-second boundary -- not confirmed against any decompiled source,
# just the simplest explanation consistent with a single-second,
# non-repeating discrepancy.
#
# Each upload request gets an answer of this shape, keyed by the same
# xferId:
#   {"reqParams": {...the original uploadP2Map* request, echoed...},
#    "status": "success", "url": "<presigned S3 PUT URL, path contains
#    'uploadlivemap'/'uploadcleanmap'>", "url_expires_ts": <int>}
# This is the UPLOAD counterpart to LiveMapUpdate's DOWNLOAD url above --
# two separate presigned-URL flows for the same underlying map data,
# not the same URL reused both ways. CONFIRMED (checked directly, not
# assumed): the URL path segment is NOT a trivial 1:1 mapping of the
# xferType string -- "uploadP2MapLive" -> ".../uploadlivemap/..." but
# "uploadP2MapMission" -> ".../uploadcleanmap/..." (not "missionmap").
#
# After the mission concludes, a distinct notification also appeared
# once:
#   {"event": {"NEW_P2MAP_AVAILABLE": {"p2map_id": ..., "p2mapv_id":
#    ..., "p2map_type": "CLEANMAP", "robot_id": ...}}}
# Confirms "CLEANMAP" as a real p2map_type value; no other values
# observed to compare against.


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




@dataclass(frozen=True)
class CleanScoreRegion:
    """One room's cleanliness value.

    `clean_score` is a float between CleanScoreConst.MIN_CLEAN_SCORE
    (0.0) and MAX_CLEAN_SCORE (1.0), so directly a fraction.

    ACCUMULATED STATE, NOT A MISSION RESULT. The value hangs off a
    region and is carried forward; CleanScoreData.mission_last_processed
    only says how far it has been advanced. This is the data behind the
    app's Smart Clean.
    """

    region_id: str | None = None
    #: HIGHER MEANS DIRTIER. Settled by an eleven-room account
    #: (@jouwdan), where the value tracks how long ago each room was
    #: last cleaned almost perfectly:
    #:
    #:     mission 33 (just now)   0.0, 0.0, 0.0
    #:     mission 32              0.15, 0.1744, 0.1744
    #:     mission 27              0.2801, 0.4
    #:     mission 25              0.4201
    #:     mission 12 (long ago)   0.6973
    #:
    #: The three rooms cleaned by the newest mission read exactly zero
    #: and carry `last_updated_by: batch_decay_skipped` -- decay skipped
    #: because they had just been reset. And `clean_score_ranges: [0.7]`
    #: is the threshold the oldest room is approaching, which makes it
    #: "needs cleaning" rather than "clean enough".
    #:
    #: A four-room account looked ambiguous because two of its rooms
    #: shared a mission and differed anyway -- room size and traffic
    #: move the rate, not the direction. Eleven rooms across five
    #: missions settle it.
    #:
    #: So anything built on this is DIRTINESS. The field name points the
    #: other way, and an automation written from the name alone would do
    #: the opposite of what its author meant.
    clean_score: float | None = None
    updated_ts: int | None = None
    last_updated_by: str | None = None
    #: `normal` in the only capture there is; the other values are
    #: unknown.
    high_traffic_enum: str | None = None
    #: The mission that last cleaned this room, and the one that last
    #: left it UNFINISHED -- `{"missionId": ..., "nMssn": ...,
    #: "startTime": ...}` or None.
    #:
    #: The unfinished one answers a question nothing else does: which
    #: room did not get done. Two of the four rooms in the first capture
    #: carry one.
    mission_last_cleaned: dict[str, Any] | None = None
    mission_last_unfinished: dict[str, Any] | None = None
    #: What the robot would use for this room on a smart clean --
    #: `operatingMode`, `suctionLevel`, `carpetBoost`, `twoPass`,
    #: `swScrub`. Kept raw; these are the robot's own defaults rather
    #: than anything this library sets.
    smart_clean_prefs: dict[str, Any] | None = None

    #: A DICT, not the string the model first declared. Live response
    #: (@DaRealGuGu, b8):
    #:   {"carpetBoost": false, "operatingMode": 6, "suctionLevel": 1,
    #:    "swScrub": 0, "twoPass": false}
    #: The same per-region parameter block that region cleaning
    #: commands carry. Kept raw: nothing was read out at this call site
    #: to say the nesting matches the command models, and assuming it
    #: does is how wire keys go wrong.
    smart_clean_prefs: dict[str, Any] | None = None

    #: FIELDS THE APK ANALYSIS DID NOT LIST, seen in the first real
    #: response. Reason enough to keep reading raw output rather than
    #: trusting a confirmed key list to be exhaustive.
    high_traffic_enum: str | None = None
    mission_last_cleaned: dict[str, Any] | None = None
    mission_last_unfinished: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanScoreRegion:
        if not isinstance(data, dict):
            return cls()
        return cls(
            region_id=data.get("region_id"),
            clean_score=data.get("clean_score"),
            updated_ts=data.get("updated_ts"),
            last_updated_by=data.get("last_updated_by"),
            smart_clean_prefs=data.get("smart_clean_prefs"),
            high_traffic_enum=data.get("high_traffic_enum"),
            mission_last_cleaned=data.get("mission_last_cleaned"),
            mission_last_unfinished=data.get("mission_last_unfinished"),
        )


@dataclass(frozen=True)
class CleanScoreData:
    """The per-room scores for one map."""

    p2map_id: str | None = None
    active_p2mapv_id: str | None = None
    user_p2mapv_id: str | None = None
    smart_clean_id: str | None = None
    mission_last_processed: dict[str, Any] | None = None
    regions: list[CleanScoreRegion] = field(default_factory=list)
    #: `error` -- the response's own error object. A cloud answering
    #: with one rather than an HTTP failure looks like a successful call
    #: with no dirty rooms, which is the shape of "nothing to do".
    error: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanScoreData:
        if not isinstance(data, dict):
            return cls()
        raw = data.get("regions")
        return cls(
            error=data.get("error"),
            p2map_id=data.get("p2map_id"),
            active_p2mapv_id=data.get("active_p2mapv_id"),
            user_p2mapv_id=data.get("user_p2mapv_id"),
            smart_clean_id=data.get("smart_clean_id"),
            # Left raw: MissionInfo's own wire keys were not read out at
            # this call site, and inventing them is how wire keys go
            # wrong. Whoever needs it can read the dict.
            mission_last_processed=data.get("mission_last_processed"),
            regions=[
                CleanScoreRegion.from_json(entry)
                for entry in (raw if isinstance(raw, list) else [])
                if isinstance(entry, dict)
            ],
        )


@dataclass(frozen=True)
class CleanScoreResponse:
    """POST /v1/p2maps/clean-score.

    WIRE KEYS CONFIRMED (APK, 2 August 2026) as literals in
    libdataModule.so, read out of the app's own response parser
    (jsonToCleanScoreData) rather than from Kotlin property names. The
    wire is snake_case; the Kotlin side is camelCase (cleanScoreData,
    cleanScoreRegions, regionId, updatedTs). Confusing the two is the
    mistake that once produced 21 wrong wire keys in this library, and
    an earlier draft of this very endpoint's docstring made it again.

    Ten of the thirteen keys are confirmed as literals here.
    `p2map_id`, `active_p2mapv_id` and `regions` resolve through shared
    serialization constants instead of their own literals at this call
    site, so their spelling is inherited from confirmed uses elsewhere.

    CONFIRMED LIVE (@DaRealGuGu, b8) -- the endpoint answers, the GET
    with `?p2map_id=` is right, and four rooms came back parsed exactly
    as counted.

    THE REAL RESPONSE CARRIED THREE FIELDS THE APK ANALYSIS DID NOT
    LIST: `high_traffic_enum`, `mission_last_cleaned` and
    `mission_last_unfinished`, plus `smart_clean_prefs` as a DICT where
    the Kotlin side suggested a string. So a key list confirmed from a
    vendor's own parser is a floor, not a ceiling -- which is the
    argument for printing raw responses even once a model exists.

    AND `profile` WAS NOT THERE. a-mavrides/roomba_v4 reads it with a
    "normal" fallback, so its code could not tell "the server sends
    this" from "someone assumed it". It was deliberately left
    unmodelled on exactly that reasoning, and the first real response
    settles it: absent.

    OPEN: what a score of 0.0 means. All four of his rooms read 0.0,
    with `last_updated_by` values of "batch_decay_skipped" and
    "rt_mission" -- so there is decay logic behind it. Whether 0.0 is
    spotless or unscored is not decidable from one account whose rooms
    all read the same, and a sensor showing 0% everywhere would need
    that answer first.
    """

    clean_score_ranges: list[float] = field(default_factory=list)
    clean_scores: list[CleanScoreData] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanScoreResponse:
        """Returns an empty response for anything unexpected rather than
        raising -- a parser's job on a shape it did not expect is to
        report nothing, not to take the caller down with it."""
        if not isinstance(data, dict):
            return cls()
        ranges = data.get("clean_score_ranges")
        scores = data.get("clean_scores")
        return cls(
            clean_score_ranges=[
                value for value in (ranges if isinstance(ranges, list) else [])
                if isinstance(value, (int, float))
            ],
            clean_scores=[
                CleanScoreData.from_json(entry)
                for entry in (scores if isinstance(scores, list) else [])
                if isinstance(entry, dict)
            ],
        )
