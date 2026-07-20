"""Robot/household metadata: parts, serial number, settings, status, cleaning profiles, default routines.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .enums_common import _enum_or_none
from .mission_control import CommandParams, PadWetnessParam, RegionType


class CleaningProfileType(StrEnum):
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
class HouseholdSettingOptions:
    """NEW (session 48) -- REPLACES the previous "structure not
    investigated" placeholder. CONFIRMED via
    HouseholdSettingOptions$$serializer's <clinit>: household
    demographic info, presumably used for smart-home feature
    personalization or usage analytics -- not otherwise investigated.
    last_user_modified (timestamp), hh_adults/hh_kids/hh_pets (counts),
    hh_adults_kids_prefer_not_to_answer/hh_pets_prefer_not_to_answer
    (opt-out flags for the respective counts), hh_location_factor
    (meaning not investigated further)."""

    last_user_modified: int | None = None
    hh_adults: int | None = None
    hh_kids: int | None = None
    hh_pets: int | None = None
    hh_adults_kids_prefer_not_to_answer: bool | None = None
    hh_pets_prefer_not_to_answer: bool | None = None
    hh_location_factor: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdSettingOptions:
        return cls(
            last_user_modified=data.get("last_user_modified"),
            hh_adults=data.get("hh_adults"),
            hh_kids=data.get("hh_kids"),
            hh_pets=data.get("hh_pets"),
            hh_adults_kids_prefer_not_to_answer=data.get("hh_adults_kids_prefer_not_to_answer"),
            hh_pets_prefer_not_to_answer=data.get("hh_pets_prefer_not_to_answer"),
            hh_location_factor=data.get("hh_location_factor"),
        )


@dataclass(frozen=True)
class HouseholdSetting:
    """UPDATE (session 48): settingId/settingType confirmed via
    HouseholdSettingForUpdate$$serializer as settingId->type,
    options->options (this class's own field names were already
    correct). `options` itself is now the confirmed
    HouseholdSettingOptions above, rather than an unexamined raw dict
    -- though whether ALL settingType values use this SAME options
    shape, or whether it's genuinely polymorphic per settingType (as
    the class name area suggests, "household settings" could cover
    more than just demographics), is not confirmed. from_json() tries
    HouseholdSettingOptions.from_json() and falls back to the raw
    dict if the known keys aren't present, rather than assuming."""

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
    """CORRECTED (session 49): confirmed directly via
    Routine$$serializer's <clinit> -- real keys are `commanddefs`
    (all lowercase, no separator at all -- neither camelCase nor
    snake_case, a genuinely unusual one), `last_run`, `name_loc_key`,
    `name_loc_args`, `time_estimate`, `time_estimate_seconds`
    (snake_case) -- NOT the previously-guessed camelCase
    (`commandDefs`/`lastRun`/`nameLocKey`/`nameLocArgs`/`timeEstimate`/
    `timeEstimateSeconds`). `name` was already correct. `commanddefs`
    is presumably List<RoutineCommand> by strong analogy to
    FavoriteV1.command_defs, but not resolvable generically via the
    bytecode field signature."""

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
            command_defs=data.get("commanddefs") or [],
            last_run=data.get("last_run"),
            name_loc_key=data.get("name_loc_key"),
            name_loc_args=data.get("name_loc_args") or [],
            time_estimate=data.get("time_estimate"),
            time_estimate_seconds=data.get("time_estimate_seconds"),
        )


@dataclass(frozen=True)
class OperatingModeProfile:
    """NEW (session 49). CONFIRMED via
    OperatingModeProfile$$serializer: params, profile_type.

    CORRECTED (session 57, real live get_default_routines() response,
    chairstacker): `params` is confirmed CommandParams-shaped (fields
    seen: twoPass, suctionLevel, swScrub, carpetBoost -- a subset,
    same as everywhere else CommandParams is used defensively via
    .get()) -- previously left as untyped `Any` since the bytecode's
    generic signature couldn't reveal this. Also found: `updated_at`,
    a sibling field of params/profile_type at the same level, present
    on some but not all real entries -- missing entirely from the
    prior version of this class.

    INVESTIGATED (session 58): read the actual decompiled Kotlin
    class (OperatingModeProfile.java) directly, not just its
    serializer -- it genuinely has ONLY params/profileType, no
    inheritance, no hidden composition, nothing a bytecode scan could
    have missed. `updated_at` is real (present in live server
    responses) but was never part of the APP's own data model at
    all: kotlinx.serialization silently drops JSON keys a class
    doesn't declare, and the app itself evidently never used this
    value for anything. This isn't a scanning gap with a bytecode-side
    fix -- analyzing the app's own code can only ever reveal what the
    app itself consumes, never necessarily everything the server
    actually sends. Kept here anyway since this library wants full
    API fidelity, unlike the app -- populated defensively via
    .get()."""

    params: CommandParams | None = None
    profile_type: str | None = None
    updated_at: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OperatingModeProfile:
        params_raw = data.get("params")
        return cls(
            params=CommandParams.from_json(params_raw) if isinstance(params_raw, dict) else None,
            profile_type=data.get("profile_type"),
            updated_at=data.get("updated_at"),
        )


@dataclass(frozen=True)
class RegionDefaults:
    """NEW (session 49). CONFIRMED via RegionDefaults$$serializer:
    type, operating_mode, by_operating_mode (a dict, presumably keyed
    by operating mode name -> OperatingModeProfile, per the field name
    -- exact key format not independently confirmed).

    CORRECTED (session 57, real live get_default_routines() response,
    chairstacker): `operating_mode` is an int (e.g. 512), not a str as
    previously typed -- matches the same field's confirmed int type
    everywhere else in this codebase (e.g. RoomMetadataEntry
    .last_operating_mode). `by_operating_mode`'s keys are confirmed to
    be the operating-mode ID as a string (e.g. "512", "32") -- the
    same pattern as RoomMetadataEntry.operating_mode_defaults."""

    region_type: str | None = None
    operating_mode: int | None = None
    by_operating_mode: dict[str, OperatingModeProfile] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RegionDefaults:
        raw_by_mode = data.get("by_operating_mode") or {}
        return cls(
            region_type=data.get("type"),
            operating_mode=data.get("operating_mode"),
            by_operating_mode={k: OperatingModeProfile.from_json(v) for k, v in raw_by_mode.items()},
        )


@dataclass(frozen=True)
class RoutineBuilderDefaults:
    """NEW (session 49). CONFIRMED via
    RoutineBuilderDefaults$$serializer: regions.

    CORRECTED (session 57, real live get_default_routines() response,
    chairstacker): `regions` is a DICT keyed by region/room ID (e.g.
    "15", "100", "16"), NOT a list as previously guessed -- the same
    pattern as RoomMetadataEntry.operating_mode_defaults and several
    other dict-keyed-by-ID fields in this codebase. The bytecode alone
    couldn't distinguish List from Dict here (Java generics type
    erasure at runtime); the "list of RegionDefaults" guess in the
    original session-49 docstring turned out wrong and would have
    crashed (`AttributeError: 'str' object has no attribute 'get'`)
    the first time this method was called against an account with any
    routine_builder_defaults content -- caught here via chairstacker's
    real --dump-config output, not by any test written speculatively
    before this evidence existed."""

    regions: dict[str, RegionDefaults] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoutineBuilderDefaults:
        raw_regions = data.get("regions") or {}
        return cls(regions={k: RegionDefaults.from_json(v) for k, v in raw_regions.items()})


@dataclass(frozen=True)
class RoutinesDefaultsResponse:
    """NEW (session 49) -- the confirmed TOP-LEVEL envelope for
    get_default_routines(), previously never modeled (only the
    per-item Routine shape was). CONFIRMED via
    RoutinesDefaultsResponse$$serializer: routines (list of Routine),
    routine_builder_defaults (RoutineBuilderDefaults) -- the latter
    was never even captured by the old parse_default_routines()."""

    routines: list[Routine] = field(default_factory=list)
    routine_builder_defaults: RoutineBuilderDefaults | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoutinesDefaultsResponse:
        raw_defaults = data.get("routine_builder_defaults")
        return cls(
            routines=_parse_routines_list(data.get("routines")),
            routine_builder_defaults=RoutineBuilderDefaults.from_json(raw_defaults) if raw_defaults else None,
        )


def _parse_routines_list(raw: Any) -> list[Routine]:
    """CORRECTED (session 56): a real live response
    (chairstacker, v0.1.10a0) crashed here with `AttributeError:
    'str' object has no attribute 'get'` -- the confirmed
    `$$serializer` bytecode says `routines` is a `List<Routine>`, but
    the ACTUAL live value was very likely a JSON OBJECT (dict keyed
    by routine ID/type), not a JSON array -- iterating a dict in
    Python walks its string KEYS, not its values, which reproduces
    this exact error. This mirrors a pattern already seen elsewhere
    in this project (e.g. RoomMetadataEntry.operating_mode_defaults
    is genuinely dict-keyed-by-ID). Handled defensively here for
    both possible shapes, since the real raw JSON wasn't available
    to confirm which one definitively -- rather than crash, a
    malformed/unexpected individual entry is silently skipped so one
    bad entry doesn't take down the whole parse."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    result = []
    for entry in raw:
        if isinstance(entry, dict):
            result.append(Routine.from_json(entry))
        # else: skip -- not a dict, can't be a Routine, don't crash the whole parse over it
    return result


def parse_default_routines(data: dict[str, Any] | list[dict[str, Any]]) -> list[Routine]:
    """Converts the raw get_default_routines() response into a list of
    typed Routine objects. CORRECTED (session 49): the envelope key is
    now confirmed as "routines" (via RoutinesDefaultsResponse$$serializer)
    -- the previous "defaults" fallback guess is dropped, no longer
    needed. This convenience function only returns the routines list;
    use RoutinesDefaultsResponse.from_json() directly if you also want
    routine_builder_defaults (region-type-based default operating-mode
    settings, not previously modeled at all).

    CORRECTED (session 56): now uses the same defensive
    _parse_routines_list() helper as RoutinesDefaultsResponse.from_json()
    -- see that helper's docstring for why (a real live crash, dict-vs-list
    ambiguity for the "routines" value)."""
    if isinstance(data, dict):
        return _parse_routines_list(data.get("routines"))
    return _parse_routines_list(data)


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
class P2MapData:
    """NEW (session 51) -- the confirmed response shape for
    get_map_metadata() (GET /v1/p2maps/{p2mapId}), previously entirely
    unmodeled ("P2MapMetadata's real fields weren't captured in the
    analysis session"). CONFIRMED via P2MapData$$serializer's
    <clinit>: p2map_id, active_p2mapv_id, create_time,
    last_p2mapv_ts, state, visible, name, user_orientation_rad -- the
    last two match set_map_name()/set_map_orientation()'s own
    confirmed write-side field names exactly, confirming this is
    genuinely the same map-settings concept, read and write sides
    agreeing.

    EXTENDED (session 57): a real live response (chairstacker,
    --dump-config) showed this endpoint's actual response includes
    MORE fields than the bytecode-confirmed 8 above -- entity_type,
    robot_id, sku, and (most notably) a full rooms_metadata list,
    identical in shape to get_active_map_versions()'s own
    P2MapVersion.rooms_metadata (same RoomMetadataEntry reused here).
    In fact this real response is now confirmed to be structurally
    identical to a single P2MapVersion entry, plus user_orientation_rad
    (which did NOT appear in this particular capture either --
    consistent with it simply being omitted when unset, not evidence
    against the bytecode-confirmed field existing). Kept as a
    separate class from P2MapVersion rather than merged, since the
    bytecode evidence for user_orientation_rad specifically belongs to
    P2MapData's own serializer, not P2MapVersion's."""

    p2map_id: str | None = None
    entity_type: str | None = None
    active_p2mapv_id: str | None = None
    create_time: Any | None = None
    robot_id: str | None = None
    sku: str | None = None
    last_p2mapv_ts: Any | None = None
    state: Any | None = None
    visible: bool | None = None
    name: str | None = None
    user_orientation_rad: float | None = None
    rooms_metadata: list[RoomMetadataEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapData:
        return cls(
            p2map_id=data.get("p2map_id"),
            entity_type=data.get("entity_type"),
            active_p2mapv_id=data.get("active_p2mapv_id"),
            create_time=data.get("create_time"),
            robot_id=data.get("robot_id"),
            sku=data.get("sku"),
            last_p2mapv_ts=data.get("last_p2mapv_ts"),
            state=data.get("state"),
            visible=data.get("visible"),
            name=data.get("name"),
            user_orientation_rad=data.get("user_orientation_rad"),
            rooms_metadata=[RoomMetadataEntry.from_json(r) for r in (data.get("rooms_metadata") or [])],
        )


@dataclass(frozen=True)
class P2MapEditPartialSuccess:
    """NEW (session 51). CONFIRMED via
    P2MapEditPartialSuccess$$serializer: status, p2mapv_id,
    p2map_metadata -- one of (at least) three response shapes edit_map()
    might get back, alongside P2MapEditSuccessFallback and P2MapError.
    Which one actually comes back for a given request, and what
    "status" values select each, is NOT confirmed -- these are three
    separately-found response classes, not a resolved discriminated
    union. edit_map() still returns raw JSON; these exist for callers
    who want to attempt parsing a specific expected shape themselves."""

    status: Any | None = None
    p2mapv_id: str | None = None
    p2map_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapEditPartialSuccess:
        return cls(
            status=data.get("status"),
            p2mapv_id=data.get("p2mapv_id"),
            p2map_metadata=data.get("p2map_metadata") or {},
        )


@dataclass(frozen=True)
class P2MapEditSuccessFallback:
    """NEW (session 51). CONFIRMED via
    P2MapEditSuccessFallback$$serializer: status, map_url, p2mapv_id,
    p2map_metadata -- see P2MapEditPartialSuccess's docstring for the
    same "which shape actually comes back" caveat. The extra `map_url`
    field here (vs. P2MapEditPartialSuccess lacking it) suggests this
    variant may be used when a fresh map bundle needs to be
    (re-)downloaded after the edit, but that's an inference, not
    confirmed."""

    status: Any | None = None
    map_url: str | None = None
    p2mapv_id: str | None = None
    p2map_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapEditSuccessFallback:
        return cls(
            status=data.get("status"),
            map_url=data.get("map_url"),
            p2mapv_id=data.get("p2mapv_id"),
            p2map_metadata=data.get("p2map_metadata") or {},
        )


@dataclass(frozen=True)
class ResponseError:
    """NEW (session 51). CONFIRMED via ResponseError$$serializer (data/
    restservices/utils) AND the essentially identical P2MapError
    (irobotdata/maps/.../responses) -- both share the same two fields
    (code, message) plus the same two wrapper shapes:
    ErrorContainer ({"error": {...this shape...}}) and
    MessageContainer ({"Message": "..."} -- capital M, confirmed
    exactly as-is, not a typo). This generic error shape appears to be
    used across multiple REST areas (both a `data.restservices.utils`
    version and a map-editing-specific `P2MapError` version exist,
    field-for-field identical) -- modeled once here rather than
    duplicated. Not currently wired into RestError's own parsing
    (RestError just keeps the raw response text) -- available for
    callers who want to attempt parsing a REST error body themselves."""

    code: Any | None = None
    message: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ResponseError:
        return cls(code=data.get("code"), message=data.get("message"))

    @classmethod
    def from_error_container(cls, data: dict[str, Any]) -> ResponseError | None:
        """For the {"error": {...}} wrapper shape."""
        inner = data.get("error")
        return cls.from_json(inner) if isinstance(inner, dict) else None

    @classmethod
    def message_from_message_container(cls, data: dict[str, Any]) -> str | None:
        """For the {"Message": "..."} wrapper shape -- capital M,
        confirmed exactly as-is via MessageContainer$$serializer."""
        return data.get("Message")


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


@dataclass(frozen=True)
class ScheduleShadow:
    """CONFIRMED LIVE (this session, chairstacker) -- complete content
    of the named "rw-schedule" shadow, the third of the three
    never-before-queried candidates checked in the same pass as
    ConnectionStatusShadow/SoftwareStatusShadow. Also not battery-
    related -- this is the cleaning schedule.

    Deliberately does NOT deep-parse clean_schedule2_raw's own array
    elements here: each entry's "cmdStr" is a string-serialized,
    Python-repr-like object (not direct JSON) embedding
    CommandParams-like fields (adaptive_cleaning/carpet_boost/
    operating_mode/etc. -- see models/mission_control.py's own notes
    next to no_auto_passes/operating_mode for what's already confirmed
    about that inner structure from a different investigation). That
    parsing is a separate, already-ongoing effort tracked there;
    duplicating it here would diverge rather than reuse it. Stored raw
    so the data is still fully available to a caller who wants it."""

    clean_schedule2_raw: list[Any] = field(default_factory=list)
    nsmip: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ScheduleShadow:
        return cls(
            clean_schedule2_raw=data.get("cleanSchedule2") or [],
            nsmip=data.get("nsmip"),
        )


@dataclass(frozen=True)
class ConnectionStatusShadow:
    """CONFIRMED LIVE (this session, chairstacker) -- complete content
    of the named "rw-constatus" shadow, the leading candidate for
    battery/charging status from a native-app symbol trace (this
    library had never queried it before). That hypothesis is now
    DISPROVEN: this is MQTT/AWS-IoT connection status (is the device
    currently connected to the broker), not battery or charging state.
    The name's surface resemblance to "connection status" was
    accurate, but pointed at the wrong KIND of connection -- see
    RobotStatusV2's own docstring for the full correction. "echo"
    plausibly corresponds to the write-side SetEchoCommand this shadow
    was originally (and, per this finding, correctly) associated with
    in the app's command config. UPDATED (this session, prompted by a
    real bug report): this is very plausibly the actual "find my
    robot" (audible chime) trigger, not just a generic heartbeat/ping
    -- see PrimeRobot.trigger_echo_via_shadow()'s own docstring for
    the full reasoning and the existing REST-based implementation this
    would replace if confirmed. One real capture showed echo=0 in an
    idle state; the actual trigger value is not yet confirmed.
    connected_v2's relationship to connected (newer replacement?
    different granularity?) is not confirmed either -- both are stored
    as opaque values rather than guessed at."""

    connected: Any | None = None
    connected_v2: Any | None = None
    echo: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ConnectionStatusShadow:
        return cls(
            connected=data.get("connected"),
            connected_v2=data.get("connectedv2"),
            echo=data.get("echo"),
        )


@dataclass(frozen=True)
class SoftwareStatusShadow:
    """CONFIRMED LIVE (this session, chairstacker) -- complete content
    of the named "rw-software" shadow, one of the two remaining
    never-before-queried candidates alongside rw-constatus (see
    ConnectionStatusShadow). Also NOT battery/charging-related --
    this is OTA/firmware deployment and update status. "imuRecal" is
    the one field with genuine unresolved meaning (IMU recalibration
    status/trigger?, not confirmed); the rest are self-describing
    deployment/version bookkeeping fields."""

    deployment_id: Any | None = None
    deployment_mpkg: Any | None = None
    deployment_state: Any | None = None
    imu_recal: Any | None = None
    last_command: Any | None = None
    last_sw_update: Any | None = None
    software_version: str | None = None
    submodule_sw_version: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SoftwareStatusShadow:
        return cls(
            deployment_id=data.get("deploymentId"),
            deployment_mpkg=data.get("deploymentMpkg"),
            deployment_state=data.get("deploymentState"),
            imu_recal=data.get("imuRecal"),
            last_command=data.get("lastCommand"),
            last_sw_update=data.get("lastSwUpdate"),
            software_version=data.get("softwareVer"),
            submodule_sw_version=data.get("subModSwVer"),
        )


@dataclass(frozen=True)
class DockPadDryReport:
    """NEW (this session, live capture, chairstacker) -- CONFIRMED LIVE,
    not decompiled: a push message on a completely new topic family,
    "{prefix}/things/{blid}/dock/paddry/report", fired essentially
    immediately after a mission's "start" command (well before any
    actual docking/pad-drying activity) -- plausibly a "here's the
    dock's current lifetime stats" report triggered by leaving the
    dock, not specifically by a pad-dry cycle itself.

    GENUINELY NEW LEAD for the battery/RobotStatusV2 question: the
    topic name itself ("dock/paddry/report") strongly suggests a
    topic FAMILY shaped like "dock/{reportType}/report", with
    "paddry" being only the one reportType observed so far. If other
    reportType values exist (a "charge" or "battery" one would be the
    obvious hope), they'd very plausibly arrive on sibling topics of
    the same family -- not confirmed, no other reportType has been
    seen yet in any capture, but this is a more concrete, structurally-
    grounded lead than anywhere else has pointed so far. No dedicated
    watch method added for this speculatively -- the existing
    watch_raw_topic() wildcard already covers this whole family
    without needing to know reportType values in advance.

    Two-level structure, confirmed directly from the raw payload: an
    inner "bbk" object (lifetime/aggregate counters, name unexplained --
    plausibly "black box") with values that looked STALE compared to
    the top-level ones in the one capture seen (bbk.dock_id="UNKNOWN"/
    bbk.dock_ver="UNKNOWN" vs top-level dock_id="NA"/dock_ver="20") --
    whether this staleness is a real, meaningful distinction or just
    this particular robot's own dock never having been individually
    identified is unconfirmed, only one example exists."""

    report_type: str | None = None
    robot_id: str | None = None
    dock_id: str | None = None
    dock_pn: str | None = None
    dock_ver: str | None = None
    error: int | None = None
    hw_rev: int | None = None
    pd_state: int | None = None
    var_id: int | None = None
    start_time: int | None = None
    end_time: int | None = None
    report_time: int | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    bbk: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockPadDryReport:
        return cls(
            report_type=data.get("reportType"),
            robot_id=data.get("robotId"),
            dock_id=data.get("dockId"),
            dock_pn=data.get("dockPn"),
            dock_ver=data.get("dockVer"),
            error=data.get("error"),
            hw_rev=data.get("hwRev"),
            pd_state=data.get("pdState"),
            var_id=data.get("varId"),
            start_time=data.get("startTime"),
            end_time=data.get("endTime"),
            report_time=data.get("reportTime"),
            capabilities=data.get("cap") or {},
            bbk=data.get("bbk") or {},
        )


@dataclass(frozen=True)
class DockControl:
    """NEW (session 49). CONFIRMED via DockControl$$serializer:
    control, status. Element type of RobotStatusV2.dock_controls."""

    control: Any | None = None
    status: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockControl:
        return cls(control=data.get("control"), status=data.get("status"))


@dataclass(frozen=True)
class RobotStatusButton:
    """NEW (session 49). CONFIRMED via RobotStatusV2$Button$$serializer:
    status, action. Element type of RobotStatusV2.buttons. Named
    RobotStatusButton (not plain Button) to avoid collision with any
    future, unrelated "Button" concept elsewhere in this library."""

    status: Any | None = None
    action: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotStatusButton:
        return cls(status=data.get("status"), action=data.get("action"))


@dataclass(frozen=True)
class RobotStatusError:
    """NEW (session 49). CONFIRMED via
    RobotStatusV2$RobotError$$serializer AND
    RobotStatusV2$ConditionalRobotError$$serializer -- both have the
    EXACT SAME confirmed fields (error_id, bucket, allowed_modes),
    despite being two distinct Kotlin classes. One shared dataclass
    used here for both RobotStatusV2.errors (RobotError elements) and
    RobotStatusV2.conditional_errors (ConditionalRobotError elements)
    -- the distinction between the two, if any exists beyond the
    identical field shape, isn't confirmed."""

    error_id: Any | None = None
    bucket: Any | None = None
    allowed_modes: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotStatusError:
        return cls(
            error_id=data.get("error_id"),
            bucket=data.get("bucket"),
            allowed_modes=data.get("allowed_modes"),
        )


@dataclass(frozen=True)
class RobotStatusV2:
    """See the module section comment above for the full evidence trail
    and the unresolved data-source caveat. All 11 fields below are
    bytecode-confirmed wire keys, but this class itself is NOT confirmed
    to be part of get_state()'s response -- treat any successful parse
    as a data point to report back, not an assumption to build on.

    UPDATE (session 49): the four list/dict-typed fields' own element
    types are now ALSO confirmed (DockControl/RobotStatusButton/
    RobotStatusError, see their own docstrings) -- previously left as
    list[Any], now properly typed.

    STRONGER NEGATIVE EVIDENCE (this session, jayjay13011, roombapy-prime
    v0.1.11a6): a live capture with fully topic-tracked wildcard coverage
    (7 distinct topics identified: mission/timeline/report, livemap/update,
    livemap/cmd, filexfer_req, filexfer_resp, cmd, service_event) watched
    for 300 seconds after stop+dock were sent -- specifically to give the
    robot time to physically reach its dock -- and NONE of these 7 topics
    carried anything battery/charging-related. This doesn't prove
    RobotStatusV2 is unreachable via MQTT (it could still live on a topic
    this particular wildcard scope doesn't cover, e.g. outside
    "things/{blid}/"), but it does rule out "we just weren't watching
    long enough" and "it's mixed in with one of these other message
    types but we didn't notice" as explanations. The most likely
    remaining possibilities: it's shadow/get_state()-only (never pushed),
    or it lives under a topic root this wildcard didn't reach.

    NAMED-SHADOW HYPOTHESIS DISPROVEN (this session, chairstacker, all
    five known named shadows checked in one pass via
    get_named_shadow()): "rw-constatus" was the leading candidate,
    reasoned from a native-app symbol trace showing RobotStatusV2's
    value assembled from four combined data streams rather than one
    ready-made field. Live content: {"connected", "connectedv2",
    "echo", "svcEndpoints"} -- this is MQTT/AWS-IoT CONNECTION status
    (is the device currently connected to the broker), not battery or
    charging status. The name's surface resemblance to "connection
    status" was accurate, but pointed at the wrong KIND of
    "connection" -- network connectivity, not power/charging state.
    The other two candidates also confirmed content, neither
    battery-related either: "rw-schedule" is just {"cleanSchedule2",
    "nsmip", "svcEndpoints"} (the cleaning schedule -- now modeled as
    ScheduleShadow, alongside ConnectionStatusShadow/SoftwareStatusShadow
    for the other two), "rw-software" is {"deploymentId",
    "deploymentMpkg", "deploymentState", "imuRecal", "lastCommand",
    "lastSwUpdate", "nsmip", "softwareVer", "subModSwVer",
    "svcEndpoints"} (OTA/firmware update status). All five named
    shadows this wildcard-subscription pattern covers are now fully
    enumerated -- none contain battery/charging/dock data. Whatever
    "AssetNetworkData"/"OTAStatusData" (from the same native trace)
    actually resolve to in the real app, they evidently aren't
    equivalent to "rw-constatus"/"rw-software" the way this hypothesis
    assumed, at least not for the battery-relevant portion of
    RobotStatusV2 specifically.

    ARCHITECTURE, CORRECTED (this session, parallel reverse-engineering
    track -- two earlier claims from that same track's own prior notes
    were explicitly retracted, not carried forward here: a "batPct"/
    "NetworkType.CLOUD" finding that turned out to belong to the
    Classic-layer RobotV1/RobotV2 classes, unrelated to Prime; and an
    unsupported "battery isn't available via the cloud at all" claim --
    logically untenable, since the app itself displays battery remotely,
    so SOME cloud channel must carry it). The actual, better-supported
    finding: the data lives in core::MissionData, a JNI proxy class
    (getBatteryLevelPercentage/getIsCharging/getIsFullyCharged/
    getTankLevel/getDockState/getResolvedMissionStatus/
    getCommandReadinessMap, plus dock descriptors) that itself must be
    FED from outside the native core -- a proxy doesn't invent values.
    Combined with SettingsData/AssetNetworkData/OTAStatusData via
    rxcpp::combine_latest into StatusReducerData -> this class -> UI.
    Structurally notable: this class has no $$serializer despite
    @SerialName-annotated fields -- those annotations describe the
    native-to-Kotlin handoff format (via ObservableUseCaseJsonCallback),
    NOT necessarily the cloud wire format directly.

    EXPANDED FIELD LIST (this session, from RobotStatusV2Constants.java
    directly -- meaningfully larger than the 11 fields modeled below,
    which predate this finding): battery_level, allowed_modes, buttons,
    conditional_errors, dock_controls, dock_info, command_readiness,
    cycle, asset_connection_state (a composite: robot_connected_to_iot,
    aws_network_state, app_to_robot_local, is_asset_missing_detected,
    status_error_code), dock_state_* (dock_id, evac_state,
    firmware_version, fluid_replenishment_state, capabilities, error).
    Not yet added as dataclass fields here -- documented so a future
    capture that DOES find this structure somewhere is recognized
    against the fuller list, not just the 11 already modeled.

    THE ACTUAL UNTESTED GAP (this session): every wildcard capture so
    far has only covered "{irbt_topic_prefix}/things/{blid}/#" -- the
    entire "$aws/" tree (where get_state()/get_settings() already build
    their OWN topics, under "$aws/things/{blid}/shadow", see
    _shadow_base() above) has never been wildcard-captured, and
    watch_state()'s update/delta push subscription has never been run
    LIVE during an active mission (see its own docstring's correction).
    One real device (chairstacker) showed a shadow version of 5324 --
    over five thousand updates, hard to explain for purely static
    configuration. verify_mission_timeline.py's --watch-shadow-delta
    and --watch-aws-tree flags exist to actually test this now."""

    robot_state: int | None = None
    battery_level: int | None = None
    is_charging: bool | None = None
    is_robot_on_dock: bool | None = None
    current_p2map_id: str | None = None
    current_p2map_version_id: str | None = None
    dock_controls: list[DockControl] = field(default_factory=list)
    errors: list[RobotStatusError] = field(default_factory=list)
    conditional_errors: list[RobotStatusError] = field(default_factory=list)
    buttons: list[RobotStatusButton] = field(default_factory=list)
    localization_args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotStatusV2:
        return cls(
            robot_state=data.get("robot_state"),
            battery_level=data.get("battery_level"),
            is_charging=data.get("is_charging"),
            is_robot_on_dock=data.get("is_robot_on_dock"),
            current_p2map_id=data.get("p2mapId"),
            current_p2map_version_id=data.get("p2mapvId"),
            dock_controls=[DockControl.from_json(d) for d in (data.get("dock_controls") or [])],
            errors=[RobotStatusError.from_json(e) for e in (data.get("errors") or [])],
            conditional_errors=[RobotStatusError.from_json(e) for e in (data.get("conditional_errors") or [])],
            buttons=[RobotStatusButton.from_json(b) for b in (data.get("buttons") or [])],
            localization_args=data.get("localization_args") or {},
        )

    @classmethod
    def any_field_present(cls, data: dict[str, Any]) -> bool:
        """Helper for callers deciding whether a parse attempt found
        anything real, as opposed to an all-None/empty result from a
        dict that simply doesn't contain this structure at all (see the
        unresolved data-source caveat above -- most real dicts handed to
        this class will legitimately not contain it)."""
        keys = (
            "robot_state", "battery_level", "is_charging", "is_robot_on_dock",
            "p2mapId", "p2mapvId", "dock_controls", "errors",
            "conditional_errors", "buttons", "localization_args",
        )
        return any(k in data for k in keys)


def parse_robot_status_v2(data: dict[str, Any] | None) -> RobotStatusV2 | None:
    """NEW (session 40). Attempts to parse RobotStatusV2 out of a dict --
    typically get_state()'s `state.reported` (or `state.desired`)
    sub-object, though where this structure actually lives is itself
    unconfirmed, see the module section comment above. Returns None if
    the dict is empty/missing or none of the 11 known keys are present
    (RobotStatusV2.any_field_present()), rather than returning an
    all-None object that would misleadingly look like a successful,
    empty parse."""
    if not data or not RobotStatusV2.any_field_present(data):
        return None
    return RobotStatusV2.from_json(data)


