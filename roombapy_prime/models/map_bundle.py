"""Map bundle read models -- what's actually IN a downloaded map bundle.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass, field
from enum import Enum
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


class RoomTypeSource(str, Enum):
    """Confirmed from P2MapRoomInfo$RoomType$Source -- HOW a room type
    came about (detected vs. set by the user). Exact string values not
    confirmed 1:1 (enum names yes, wire string serialization not
    explicitly seen in the code) -- filled in here as a placeholder
    with the enum names themselves, not as confirmed wire strings."""

    DETECTED = "DETECTED"
    USER_SET = "USER_SET"


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
    value space it uses."""

    name: str | None = None
    room_type: Any | None = None
    simplified_geometry: Polygon | None = None
    adjacent_room_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomFeatureProperties:
        simplified = data.get("simplifiedGeometry")
        return cls(
            name=data.get("name"),
            room_type=data.get("type"),
            simplified_geometry=_polygon_from_geojson(simplified) if simplified else None,
            adjacent_room_ids=data.get("adjacentRoomIDs") or [],
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
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            properties=FloorPlanFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class PolicyZoneFeatureProperties:
    """NEW (session 47), REPLACES the previous separate
    `NoMopZoneInfo`/`KeepOutZoneInfoRead`/`VirtualWallInfo` guesses --
    CONFIRMED there is actually just ONE feature type ("PolicyZone")
    covering all of these, discriminated by `zone_type`/
    `threshold_type` rather than being separate classes. This matches
    the project's own earlier documented puzzle ("keepOutZones"/
    "noMopZones"/"virtualWalls"/"thresholds" had no dedicated
    P2MapInfoType field found") -- they were never separate types to
    begin with. Exact values for zone_type/threshold_type not
    confirmed (no enum found, only the field names) -- left as raw
    strings."""

    zone_type: str | None = None
    threshold_type: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PolicyZoneFeatureProperties:
        return cls(zone_type=data.get("type"), threshold_type=data.get("threshold_type"))


@dataclass(frozen=True)
class PolicyZoneFeature:
    """NEW (session 47) -- REPLACES `NoMopZoneInfo`/
    `KeepOutZoneInfoRead`/`VirtualWallInfo`. See
    PolicyZoneFeatureProperties' docstring for why these three guessed
    classes collapse into this one, now-confirmed type. CONFIRMED via
    PolicyZoneFeature$$serializer/PolicyZoneFeature$Properties$$serializer.
    Geometry left as the general Polygon type -- whether a "virtual
    wall"-like linear case still exists within this unified type, and
    if so how it's distinguished, is not confirmed."""

    feature_id: str
    geometry: Polygon
    properties: PolicyZoneFeatureProperties = field(default_factory=PolicyZoneFeatureProperties)
    feature_type: str = "Feature"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PolicyZoneFeature:
        return cls(
            feature_id=data.get("id", ""),
            geometry=_polygon_from_geojson(data.get("geometry") or {}),
            properties=PolicyZoneFeatureProperties.from_json(data.get("properties") or {}),
            feature_type=data.get("type", "Feature"),
        )


@dataclass(frozen=True)
class CleanZoneFeatureProperties:
    """CONFIRMED (session 47): name (the one field that distinguishes
    this from AdHocCleanZoneFeature, which has none)."""

    name: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanZoneFeatureProperties:
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
    feature). CONFIRMED: type."""

    floor_type: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FloorTypeFeatureProperties:
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

    UNCONFIRMED: this manifest file's OWN filename within the tar.gz --
    parse_map_bundle() returns a flat {filename: content} dict with no
    indication of which entry IS the manifest; try likely candidates
    ("manifest.json" etc.) until a real bundle confirms this."""

    metadata: dict[str, Any] = field(default_factory=dict)
    features: list[ManifestFeature] = field(default_factory=list)
    experimental_features: list[ManifestFeature] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BundleManifest:
        return cls(
            metadata=data.get("metadata") or {},
            features=[ManifestFeature.from_json(f) for f in (data.get("features") or [])],
            experimental_features=[ManifestFeature.from_json(f) for f in (data.get("experimentalFeatures") or [])],
        )


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


