"""Mission command payload models (RoutineCommand/CommandParams/Region).

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .enums_common import _enum_or_none
from .geometry import Position


class MissionCommandType(StrEnum):
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
    id_multipolys: list[CommandPolygon] | list[dict[str, Any]] | None = None
    params: CommandParams | dict[str, Any] | None = None
    regions: list[Region] | list[dict[str, Any]] | None = None
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


class RegionType(StrEnum):
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


class CleaningMode(StrEnum):
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


class CleaningPasses(StrEnum):
    """Confirmed (androguard, MissionPreferenceValue$CleaningPasses):
    only 2 values."""

    DOUBLE = "Double"
    SINGLE = "Single"


class LiquidAmountLevel(StrEnum):
    """Confirmed (androguard, MissionPreferenceValue$LiquidAmount AND
    $ComboLiquidAmount -- both have identical 3 values High/Low/Normal,
    merged here since structurally identical)."""

    HIGH = "High"
    LOW = "Low"
    NORMAL = "Normal"


class SoftwareScrub(StrEnum):
    """Confirmed (androguard, MissionPreferenceValue$SoftwareScrub)."""

    OFF = "Off"
    ON = "On"


class VacuumPowerLevel(StrEnum):
    """Confirmed (androguard, MissionPreferenceValue$VacuumPower): 4
    values (more than CleaningMode etc.)."""

    HIGH = "High"
    LOW = "Low"
    NORMAL = "Normal"
    QUIET = "Quiet"


class MissionPreferenceSwitcherType(StrEnum):
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


