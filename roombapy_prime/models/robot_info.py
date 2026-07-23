"""Robot/household metadata: parts, serial number, settings, status, cleaning profiles, default routines.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from .enums_common import RoomCategory, _enum_or_none
from .mission_control import CommandParams, PadWetnessParam, RegionType


class CleaningProfileType(StrEnum):
    """Confirmed (androguard, CleaningProfile$ProfileType): 4 values."""

    DEEP = "DEEP"
    LIGHT = "LIGHT"
    NORMAL = "NORMAL"
    SMART = "SMART"


@dataclass(frozen=True)
class CleaningProfile:
    """CORRECTED (this session, parallel native-analysis track,
    DOUBLY confirmed -- both by $$serializer.<clinit> inspection AND
    against chairstacker's real get_cleaning_profiles() response from
    an earlier session, which had this exact shape the whole time):
    the wire key is "params", not "commandParams". The real,
    already-live-captured data had been sitting there showing the
    right key all along -- this was findable without any new bytecode
    analysis, just by cross-checking the existing model against
    already-captured real data, which nobody had done for this
    specific field before now.

    PRACTICAL CONSEQUENCE, more significant than the PolygonEvent
    fields fixed alongside this one: command_params stayed silently
    None against EVERY real response, every time, since "commandParams"
    never existed on the wire -- the actual parameters were sitting
    right there under "params", unread. Any caller relying on a
    cleaning profile's own parameters (light/normal/deep clean
    settings feeding into region-aware commands) would have gotten
    nothing, not just occasionally-wrong data."""

    profile: CleaningProfileType | str | None = None
    command_params: CommandParams | None = None
    regions: list[Any] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleaningProfile:
        params_data = data.get("params")
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
    some rooms, e.g. "Bathroom").

    category (NEW -- see verify_map_edit.py's own room-category test):
    the READ-side counterpart of SetRoomMetadataV1's own write-side
    room_metadata.type field (RoomCategory, enums_common.py) -- same
    key name ("type"), same enum, confirmed by construction since
    SetRoomMetadataV1's own docstring establishes this is the current
    app's room-edit path (read and write sides agreeing, same pattern
    already seen elsewhere in this project, e.g. set_map_name()/
    P2MapData.name). Added specifically so a category-change test can
    capture the ORIGINAL value before changing it, the same
    capture-then-revert safety pattern already used for room renaming."""

    room_id: str
    last_operating_mode: int | None = None
    operating_mode_defaults: dict[str, CommandParams] = field(default_factory=dict)
    region_type: RegionType | str | None = None
    name: str | None = None
    category: RoomCategory | None = None

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
            category=_enum_or_none(RoomCategory, meta.get("type")),
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


def build_room_name_map(map_versions: list[P2MapVersion], blid: str | None = None) -> dict[str, str]:
    """Turns a list of map versions into a simple {room_id: name}
    lookup -- a generic, protocol-level convenience so any consumer of
    this library (not just Home Assistant) can resolve a schedule's or
    mission's own region_id into a real room name without re-deriving
    this from scratch.

    blid, if given, filters to only map versions belonging to THIS
    robot (P2MapVersion.robot_id == blid) -- an account can have
    multiple robots, each with their own maps, and a bare room_id
    (e.g. "23") is only meaningful within one specific robot's own
    map, not globally unique across an entire account.

    Entries with no name set at all (RoomMetadataEntry.name is only
    populated for some rooms, per that class's own docstring) are
    skipped entirely -- an empty result for a given room_id means "no
    name assigned", not "unknown room".

    If the same room_id appears in more than one map version (e.g. a
    map that's been rebuilt since a room was last named), the entry
    from the version with the higher last_p2mapv_ts (more recent) wins
    -- map_versions is not assumed to already be in any particular
    order."""
    relevant = (
        [v for v in map_versions if v.robot_id == blid] if blid is not None else map_versions
    )
    # Process oldest-first so a later (more recent) map version's own
    # name naturally overwrites an earlier one for the same room_id.
    ordered = sorted(relevant, key=lambda v: v.last_p2mapv_ts or 0)
    result: dict[str, str] = {}
    for version in ordered:
        for room in version.rooms_metadata:
            if room.name:
                result[room.room_id] = room.name
    return result


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
    in the app's command config.

    "echo" AS A CHIME TRIGGER -- ALSO DISPROVEN (chairstacker, real
    device test): writing True to this field produced a genuine,
    accepted shadow write (a real update/delta response came back),
    but the robot did NOT chime -- and "locate" from the real app
    worked fine on the same device immediately after. See
    PrimeRobot.trigger_echo_via_shadow()'s own docstring for the full
    result. What "echo" actually represents remains unresolved --
    possibly a connectivity heartbeat/ping (consistent with the rest
    of this shadow being about connection status), not necessarily
    anything chime-related at all.

    TYPES CONFIRMED (parallel native-analysis track, Ghidra
    decompilation of the app's own constructor signatures, not
    guessed): connected/connected_v2 are both plain booleans.
    connected_v2's relationship to connected (newer replacement?
    different granularity?) is still not confirmed. echo is
    PROBABLY also a boolean (a packed flag in the decompiled
    constructor, slightly less certain than the other two but not
    contradicted by anything) -- kept as a plain bool here rather than
    Any, consistent with how confident this specific finding is."""

    connected: bool | None = None
    connected_v2: bool | None = None
    echo: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ConnectionStatusShadow:
        return cls(
            connected=data.get("connected"),
            connected_v2=data.get("connectedv2"),
            echo=data.get("echo"),
        )


@dataclass(frozen=True)
class SoftwareStatusShadow:
    """CONFIRMED LIVE (chairstacker) -- complete content of the named
    "rw-software" shadow, one of the two remaining never-before-queried
    candidates alongside rw-constatus (see ConnectionStatusShadow).
    Also NOT battery/charging-related -- this is OTA/firmware
    deployment and update status.

    TYPES CONFIRMED where a real deserializer for the specific field
    was found (parallel native-analysis track, Ghidra decompilation --
    not guessed): deployment_id/software_version are plain strings
    (type-tag 3). last_sw_update is a string too, parsed by the app as
    a date. deployment_state is a small int enum, 5 values (0-4) plus
    a fallback, found via a lookup table in the decompiled code -- the
    MEANING of each of the 5 values is not yet confirmed, only that
    there are exactly 5 named ones.

    deployment_mpkg/last_command have a schema constant referencing
    them but no deserializer was found for either -- their real type
    remains unconfirmed, kept as Any rather than guessed at.

    imu_recal/submodule_sw_version are CONFIRMED ABSENT from the app's
    own code entirely -- no schema constant, no deserializer at all.
    The robot's real shadow payload includes them anyway (this
    project's own confirmed key list) -- consistent with the same
    "server sends more than the app declares" pattern already seen on
    ro-currentstate. Kept as Any: there is no source at all (live or
    static) suggesting a more specific type for these two."""

    deployment_id: str | None = None
    deployment_mpkg: Any | None = None
    deployment_state: int | None = None
    imu_recal: Any | None = None
    last_command: Any | None = None
    last_sw_update: str | None = None
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


class ResolvedMissionStatus(IntEnum):
    """FULLY CONFIRMED (parallel native-analysis track, all 49 values
    0-48 extracted directly from the real enum, not partially
    transcribed anymore). Supersedes the earlier, deliberately-partial
    version of this class.

    NOT YET CONFIRMED which shadow field (if any) actually carries
    this value -- see CurrentStateShadow's own docstring for why
    "cleanMissionStatus" is a plausible but unconfirmed guess, not a
    settled mapping. The 28-47 "SENDING_COMMAND_*" range is notable on
    its own: the real app models "command sent, acknowledgment
    pending" as its own distinct transitional states per command type,
    not just a single boolean in-flight flag.

    ALSO CONFIRMED TO EXIST, NOT YET TRANSCRIBED HERE: the real app's
    own Companion object has isTraining()/isReady()-style helpers that
    group specific members of this enum together (e.g. which values
    count as "the robot is ready to start" as a category) -- the exact
    member lists for these groupings weren't extracted, only that they
    exist. Treat any grouping you might infer from these names alone
    (e.g. assuming READY/READY_WITH_ERROR are the only two "ready"
    members) as a guess, not a confirmed fact, until that companion
    logic itself is transcribed."""

    INVALID = 0
    CONNECTING = 1
    CONNECTION_REMOTE_MISSING = 2
    CONNECTION_ERROR = 3
    CONNECTION_DISCONNECTED = 4
    READY = 5
    READY_WITH_ERROR = 6
    READY_WITH_CONDITIONAL_START_REFUSE = 7
    NOT_READY_START_REFUSE = 8
    CLEANING = 9
    PAUSED = 10
    PAUSED_WITH_ERROR = 11
    PAUSED_WITH_START_REFUSE = 12
    WET_MOPPING_PAUSED_WITH_START_REFUSE = 13
    END_JOB_NO_DOCK = 14
    END_JOB_WITH_DOCK = 15
    RETURN_TO_DOCK = 16
    RETURN_TO_DOCK_SEARCHING = 17
    DOCK_EVACUATING = 18
    DOCK_REFILLING = 19
    TRAINING = 20
    SPOT_CLEANING = 21
    TIDYING_UP = 22
    VIDEO_STREAMING = 23
    PAD_WASHING = 24
    PAD_DRYING = 25
    FLUSHING_SLUICE = 26
    STOP_DOCK_EVACUATING = 27
    SENDING_COMMAND_CLEAN = 28
    SENDING_COMMAND_DOCK = 29
    SENDING_COMMAND_EVAC = 30
    SENDING_COMMAND_REFILL = 31
    SENDING_COMMAND_STOP_REFILL = 32
    SENDING_COMMAND_PAUSE = 33
    SENDING_COMMAND_RESUME = 34
    SENDING_COMMAND_START = 35
    SENDING_COMMAND_STOP = 36
    SENDING_COMMAND_TRAIN = 37
    SENDING_COMMAND_TIDYING_UP = 38
    SENDING_COMMAND_SPOT = 39
    SENDING_COMMAND_SKIP = 40
    SENDING_COMMAND_POINT_CLEAN = 41
    SENDING_COMMAND_PAD_WASH = 42
    SENDING_COMMAND_STOP_PAD_WASH = 43
    SENDING_COMMAND_PAD_DRY = 44
    SENDING_COMMAND_STOP_PAD_DRY = 45
    SENDING_COMMAND_FLUSH_SLUICE = 46
    SENDING_COMMAND_STOP_EVAC = 47
    UNKNOWN = 48


@dataclass(frozen=True)
class BinStatus:
    """CONFIRMED LIVE (chairstacker, real ro-currentstate payload):
    just one field, "present" (bool)."""

    present: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BinStatus:
        return cls(present=data.get("present"))


@dataclass(frozen=True)
class CleanMissionStatus:
    """CONFIRMED LIVE (chairstacker, real ro-currentstate payload).
    "phase" is where charging state actually lives (observed value:
    "charge") -- confirming the earlier hypothesis that a separate
    isCharging-style boolean isn't part of this shadow; the real app's
    own getIsCharging()/getIsFullyCharged() getters (see this class's
    own module docstring) are plausibly derived FROM this field rather
    than being a shadow key of their own. "operatingMode" (observed:
    2) matches OperatingModeBitmask.VACUUMING exactly, independently
    validating that enum against yet another real data point."""

    cond_not_ready: list[Any] = field(default_factory=list)
    cycle: str | None = None
    error: int | None = None
    initiator: str | None = None
    mission_id: str | None = None
    mission_start_time: int | None = None
    n_missions: int | None = None
    not_ready: int | None = None
    operating_mode: int | None = None
    phase: str | None = None
    sqft: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanMissionStatus:
        return cls(
            cond_not_ready=data.get("condNotReady") or [],
            cycle=data.get("cycle"),
            error=data.get("error"),
            initiator=data.get("initiator"),
            mission_id=data.get("missionId"),
            mission_start_time=data.get("mssnStrtTm"),
            n_missions=data.get("nMssn"),
            not_ready=data.get("notReady"),
            operating_mode=data.get("operatingMode"),
            phase=data.get("phase"),
            sqft=data.get("sqft"),
        )


class DockState(IntEnum):
    """FULLY CONFIRMED (parallel native-analysis track, all 86 values
    extracted directly from the real enum) -- previously only
    discussed in prose elsewhere in this codebase (e.g.
    DockStatus's own docstring), never actually implemented as a real
    enum until now.

    Four functional-area bands, matching the numeric-range pattern
    already observed in DockStatus's own real captured values (state/
    pw_state/pd_state = 301/601/701): DOCK_* (general dock, 300s, plus
    two low outliers at 0-3 shared with the pad-wash/pad-dry bands --
    see the duplicate-value note below), FLUID_REPLENISHMENT_* (400s),
    PAD_WASH_* (600s), PAD_DRY_* (700s).

    CONFIRMS DockStatus's own real captured values directly:
    state=301 -> DOCK_READY, pw_state=601 -> PAD_WASH_OKAY,
    pd_state=701 -> PAD_DRY_OKAY -- chairstacker's device was
    dock-ready with both pad subsystems in their own "okay" (idle,
    no error) state at capture time. What was previously only an
    "OBSERVATION, NOT A CONFIRMED MAPPING" (see DockStatus's own
    docstring) about which numeric band belongs to which category is
    now a directly confirmed, named value for each of the three
    fields captured live.

    DUPLICATE VALUES, CONFIRMED PRESENT IN THE REAL ENUM ITSELF (NOT A
    TRANSCRIPTION ERROR): 2 is shared by PAD_DRY_UNHEATED_AIR and
    PAD_WASH_NORMAL_HEATED_WATER; 3 is shared by PAD_DRY_HEATED_AIR and
    PAD_WASH_MAX_HEATED_WATER. Plausibly context-dependent (meaningful
    only within whichever specific field/subsystem reports it, not
    globally unique) -- not independently confirmed which
    interpretation is correct, only that the duplication itself is
    real. Python's own IntEnum aliasing applies here: both names for
    each duplicated value remain accessible as class attributes, but
    DockState(2)/DockState(3) themselves resolve to whichever name is
    listed first below (the PAD_DRY_* one, alphabetically/positionally
    earlier here) -- an artifact of Python enum mechanics, not
    evidence that one name is somehow more "correct" than the other."""

    DOCK_NO_COMMON_ERROR = 0
    PAD_WASH_UNHEATED_WATER = 1
    PAD_DRY_UNHEATED_AIR = 2
    PAD_WASH_NORMAL_HEATED_WATER = 2
    PAD_DRY_HEATED_AIR = 3
    PAD_WASH_MAX_HEATED_WATER = 3
    DOCK_UNKNOWN = 300
    DOCK_READY = 301
    DOCK_EVACUATION_IN_PROGRESS = 302
    DOCK_EVACUATION_COMPLETE = 303
    DOCK_EVACUATION_STOPPING = 304
    DOCK_EVACUATION_UPGRADING = 305
    DOCK_BAG_MISSING = 350
    DOCK_CLOGGED = 351
    DOCK_VACUUM_INOPERABLE = 352
    DOCK_BAG_FULL = 353
    DOCK_MOTOR_FAILURE = 354
    DOCK_PARTIAL_CLOG = 355
    DOCK_COMMUNICATION_FAILURE = 360
    DOCK_EVACUATION_REPORT_ERROR = 361
    DOCK_LIFETIME_DATA_REPORT_ERROR = 362
    DOCK_ALL_REPORTS_ERROR = 363
    DOCK_HARDWARE_ISSUE_ERROR = 365
    FLUID_REPLENISHMENT_UNKNOWN = 400
    FLUID_REPLENISHMENT_OKAY = 401
    FLUID_REPLENISHMENT_STARTED = 402
    FLUID_REPLENISHMENT_IN_PROGRESS = 403
    FLUID_REPLENISHMENT_COMPLETE = 404
    FLUID_REPLENISHMENT_COMPLETE_NOT_ENOUGH_WATER = 405
    FLUID_REPLENISHMENT_INVALID_DOCK_STATE_ERROR = 449
    FLUID_REPLENISHMENT_TANK_MISSING_ERROR = 450
    FLUID_REPLENISHMENT_TANK_LEVEL_TOO_LOW_ERROR = 451
    FLUID_REPLENISHMENT_TANK_LEVEL_SENSOR_ISSUE_ERROR = 452
    FLUID_REPLENISHMENT_COULDNT_INSERT_SNORKEL_ERROR = 453
    FLUID_REPLENISHMENT_CLOG_ERROR = 454
    FLUID_REPLENISHMENT_PUMP_FAILURE_ERROR = 455
    FLUID_REPLENISHMENT_INCORRECT_ROBOT_TANK_ERROR = 456
    FLUID_REPLENISHMENT_COMMUNICATION_FAILURE_ERROR = 457
    FLUID_REPLENISHMENT_COULDNT_EXTEND_SNORKEL_ERROR = 458
    FLUID_REPLENISHMENT_COULDNT_RETRACT_SNORKEL_ERROR = 459
    FLUID_REPLENISHMENT_DOCK_TANK_LEVEL_NOT_DECREASING_ERROR = 460
    FLUID_REPLENISHMENT_ROBOT_TANK_LEVEL_NOT_INCREASING_ERROR = 461
    FLUID_REPLENISHMENT_HARDWARE_ISSUE_ERROR = 462
    FLUID_REPLENISHMENT_DOCK_TANK_LEVEL_DECREASING_ERROR = 463
    FLUID_REPLENISHMENT_ROBOT_TANK_FILLING_TIMEOUT_ERROR = 464
    PAD_WASH_UNKNOWN = 600
    PAD_WASH_OKAY = 601
    PAD_WASH_IN_PROGRESS = 602
    PAD_WASH_COMPLETE_WITH_SUCCESS = 603
    PAD_WET_IN_PROGRESS = 604
    PAD_WET_COMPLETE = 605
    PAD_WASH_NOT_AVAILABLE_DOCK_UPDATING = 606
    PAD_WASH_FLUSHING_SLUICE = 607
    PAD_WASH_SLUICE_FLUSH_COMPLETE = 608
    PAD_WASH_INVALID_DOCK_STATE_ERROR = 649
    PAD_WASH_CLEAR_FLUID_TANK_MISSING_ERROR = 650
    PAD_WASH_CLEAR_FLUID_TANK_LEVEL_TOO_LOW_ERROR = 651
    PAD_WASH_CLEAR_FLUID_TANK_LEVEL_SENSOR_ISSUE_ERROR = 652
    PAD_WASH_GREY_WATER_TANK_MISSING_ERROR = 653
    PAD_WASH_GREY_WATER_TANK_LEVEL_TOO_FULL_ERROR = 654
    PAD_WASH_HARDWARE_ERROR = 655
    PAD_WASH_COMMUNICATION_FAILURE_ERROR = 660
    PAD_WASH_GREY_WATER_TANK_LEVEL_NOT_DECREASING_ERROR = 661
    PAD_WASH_GREY_WATER_TANK_LEVEL_NOT_INCREASING_ERROR = 662
    PAD_WASH_CLEAR_FLUID_TANK_LEVEL_DECREASING_ERROR = 663
    PAD_WASH_GREY_WATER_TANK_LEVEL_DECREASING_ERROR = 664
    PAD_WASH_HARDWARE_ISSUE_ERROR = 665
    PAD_WASH_NO_PAD_ATTACHED_ERROR = 668
    PAD_WASH_PAD_ACTUATOR_STALL_ERROR = 669
    PAD_DRY_UNKNOWN = 700
    PAD_DRY_OKAY = 701
    PAD_DRY_IN_PROGRESS = 702
    PAD_DRY_COMPLETE_WITH_SUCCESS = 703
    PAD_DRY_INTERRUPT_BY_ROBOT = 704
    PAD_DRY_INTERRUPT_BY_MISSION = 705
    PAD_DRY_INTERRUPT_BY_USER = 706
    PAD_DRY_NOT_AVAILABLE_DOCK_UPDATING = 707
    PAD_DRY_INVALID_STATE_ERROR = 749
    PAD_DRY_MOTOR_STALL_ERROR = 750
    PAD_DRY_MOTOR_FAIL_TO_START_ERROR = 751
    PAD_DRY_ACTUATOR_STALL_ERROR = 752
    PAD_DRY_PAD_NOT_WASHED_ERROR = 753
    PAD_DRY_MOTOR_F_E_T_FAULT_ERROR = 754
    PAD_DRY_HARDWARE_ISSUE_ERROR = 755
    PAD_DRY_NO_PAD_ATTACHED_ERROR = 756
    PAD_DRY_COMMUNICATION_FAILURE_ERROR = 757


@dataclass(frozen=True)
class DockCapabilities:
    """CONFIRMED LIVE (chairstacker, real ro-currentstate payload,
    nested under dock.cap) -- meaning of each still a reasonable
    guess from the name only, not further confirmed: evac (auto-evac
    capable), pad_dry/pad_wash (self-explanatory), pad_wash_or (name
    as reported, meaning genuinely unclear)."""

    evac: int | None = None
    pad_dry: int | None = None
    pad_wash: int | None = None
    pad_wash_or: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockCapabilities:
        return cls(
            evac=data.get("evac"),
            pad_dry=data.get("pd"),
            pad_wash=data.get("pw"),
            pad_wash_or=data.get("pwo"),
        )


@dataclass(frozen=True)
class DockStatus:
    """CONFIRMED LIVE (chairstacker, real ro-currentstate payload) --
    "dock" is a nested object, NOT a simple DockState enum string as
    might have been assumed from getDockState()'s own return type
    (see this class's own module docstring).

    CONFIRMED (parallel native-analysis track, all 86 DockState
    values extracted -- see that enum's own docstring): the real
    values seen here -- state=301, pw_state=601, pd_state=701 --
    directly resolve to DockState.DOCK_READY, DockState.PAD_WASH_OKAY,
    DockState.PAD_DRY_OKAY. What was previously only an "OBSERVATION,
    NOT A CONFIRMED MAPPING" (a numeric-band pattern noticed before
    the full enum was available) is now a directly confirmed, named
    value for all three fields -- chairstacker's device was
    dock-ready with both pad subsystems idle/okay at capture time.
    state/pw_state/pd_state are typed DockState here, not the plain
    int this class used before that enum existed."""

    cap: DockCapabilities | None = None
    error: int | None = None
    fw_version: str | None = None
    known: bool | None = None
    pd_state: DockState | None = None
    pw_state: DockState | None = None
    state: DockState | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockStatus:
        cap_data = data.get("cap")
        return cls(
            cap=DockCapabilities.from_json(cap_data) if cap_data else None,
            error=data.get("error"),
            fw_version=data.get("fwVer"),
            known=data.get("known"),
            pd_state=_enum_or_none(DockState, data.get("pdState")),
            pw_state=_enum_or_none(DockState, data.get("pwState")),
            state=_enum_or_none(DockState, data.get("state")),
        )


@dataclass(frozen=True)
class RuntimeStatsSummary:
    """CONFIRMED LIVE (chairstacker, real ro-currentstate payload) --
    lifetime runtime, hours+minutes. Plausibly analogous to
    ha_roomba_plus's own Classic-tier bbrun.hr ("wear data"/runtime
    hours, see MISSIONSTORE_FIELD_REGISTRY.md) -- same underlying
    concept, not confirmed to be computed identically."""

    hours: int | None = None
    minutes: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RuntimeStatsSummary:
        return cls(hours=data.get("hr"), minutes=data.get("min"))


@dataclass(frozen=True)
class P2MapRef:
    """CONFIRMED LIVE (chairstacker, real ro-currentstate payload,
    under p2maps) -- a simple map-id/version-id pair, one per known
    map. Deliberately a separate, minimal class from P2MapData (this
    module, above) -- that's the full get_map_metadata() response
    shape (8+ fields); this is just the two-field reference seen
    here, not confirmed to be interchangeable with it."""

    p2map_id: str | None = None
    p2mapv_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapRef:
        return cls(p2map_id=data.get("p2map_id"), p2mapv_id=data.get("p2mapv_id"))


@dataclass(frozen=True)
class CurrentStateShadow:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (chairstacker) -- the
    actual resolution of this whole project's battery-status search.
    One of four previously-unknown read-only ("ro-") named shadows
    found via MQTTTopics.java (see verify_named_shadows.py's own
    module docstring for that discovery).

    A real captured payload (battery at 72%, robot idle/charging on
    dock) confirmed every field below, correcting an earlier
    assumption that most of these were simple flat values -- several
    are actually nested objects, now modeled as their own classes
    above (BinStatus/CleanMissionStatus/DockStatus/
    RuntimeStatsSummary/P2MapRef). bat_pct/detected_pad/tank_present/
    reg_date/last_disconnect remain simple scalars, matching what was
    guessed before -- reg_date is a plain date STRING ("2025-09-19"),
    not a timestamp int as originally guessed.

    "charging" specifically lives in clean_mission_status.phase
    (observed: "charge"), not a dedicated boolean on this class --
    see CleanMissionStatus's own docstring. tank_present (plain bool)
    is confirmed genuinely distinct from any numeric tank-level field
    -- none appears anywhere in this real payload, consistent with
    the earlier Classic cross-reference's prediction that these are
    two different concepts.

    tz (timezone, with DST transition events) intentionally still
    left as a raw dict -- lower priority, not yet modeled in detail.
    svc_endpoints likewise (just one observed key so far,
    "svcDeplId") -- kept minimal rather than over-modeled from a
    single example."""

    bat_pct: int | None = None
    bin: BinStatus | None = None
    clean_mission_status: CleanMissionStatus | None = None
    detected_pad: str | None = None
    dock: DockStatus | None = None
    last_disconnect: int | None = None
    p2maps: list[P2MapRef] = field(default_factory=list)
    reg_date: str | None = None
    runtime_stats: RuntimeStatsSummary | None = None
    tank_present: bool | None = None
    tz: dict[str, Any] | None = None
    svc_endpoints: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CurrentStateShadow:
        bin_data = data.get("bin")
        mission_data = data.get("cleanMissionStatus")
        dock_data = data.get("dock")
        runtime_data = data.get("runtimeStats")
        return cls(
            bat_pct=data.get("batPct"),
            bin=BinStatus.from_json(bin_data) if bin_data else None,
            clean_mission_status=CleanMissionStatus.from_json(mission_data) if mission_data else None,
            detected_pad=data.get("detectedPad"),
            dock=DockStatus.from_json(dock_data) if dock_data else None,
            last_disconnect=data.get("lastDisconnect"),
            p2maps=[P2MapRef.from_json(m) for m in (data.get("p2maps") or [])],
            reg_date=data.get("regDate"),
            runtime_stats=RuntimeStatsSummary.from_json(runtime_data) if runtime_data else None,
            tank_present=data.get("tankPresent"),
            tz=data.get("tz"),
            svc_endpoints=data.get("svcEndpoints"),
        )


@dataclass(frozen=True)
class StatsShadow:
    """CONFIRMED LIVE (this session, chairstacker) -- complete key
    list of the named "ro-stats" shadow, the second of the four
    previously-unknown read-only shadows found via MQTTTopics.java.
    Only key NAMES confirmed so far, not values -- every field typed
    `Any` rather than guessed at.

    The "bb" prefix on five of these (bbchg, bbchg3, bbmssn, bbpause,
    bbrstinfo, bbsys) is UNCONFIRMED but plausibly "battery box" or
    similar -- if so, this shadow may carry lifetime/historical
    battery statistics (charge cycles, mission counts, pause events,
    reset info) as a complement to ro-currentstate's live batPct
    value, not a duplicate of it.

    CROSS-REFERENCE (this session, from ha_roomba_plus's own Classic-
    tier field registry, MISSIONSTORE_FIELD_REGISTRY.md -- an old,
    already-confirmed finding from a different product line this
    session hadn't cross-checked against Prime's field names until
    now): "bbchg3" and "bbrstinfo" both exist, confirmed with real
    sub-field structure, on Classic robots too. "bbchg3" there holds
    "estCap"/"nAvail"/"hOnDock"/"avgMin" (plus firmware-dependent
    "nLithChrg"/"nNimhChrg") -- battery-capacity-retention and
    charge-cycle data specifically, confirmed via
    "const.active_charge_cycles()" reading it for a "battery_cycles"
    metric. "bbrstinfo" there holds "nNavRst"/"nMobRst"/"nSafRst"/
    "safCauses" (plus firmware-dependent "nOomRst") -- reset-event
    diagnostics by subsystem. Same company, same field vocabulary,
    different product line -- not proof Prime's own "ro-stats" has
    identical sub-structure, but a concrete, evidence-based starting
    hypothesis for whoever parses real values here, rather than a
    bare guess. Classic's own docs also note this exact field
    (bbchg3 specifically) was ABSENT ENTIRELY on some real robots
    (firmware/model-dependent, not simply "budget hardware lacks it")
    -- worth checking whether the same per-device absence pattern
    exists for Prime's ro-stats too, not just whether it responds to
    the shadow query at all.

    Note "runtimestats" here is ALL-LOWERCASE, unlike
    ro-currentstate's camelCase "runtimeStats" -- confirmed as two
    separate keys with different casing (not a transcription error),
    kept exactly as reported."""

    bbchg: Any | None = None
    bbchg3: Any | None = None
    bbmssn: Any | None = None
    bbpause: Any | None = None
    bbrstinfo: Any | None = None
    bbsys: Any | None = None
    runtimestats: Any | None = None
    unprocessed_error: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> StatsShadow:
        return cls(
            bbchg=data.get("bbchg"),
            bbchg3=data.get("bbchg3"),
            bbmssn=data.get("bbmssn"),
            bbpause=data.get("bbpause"),
            bbrstinfo=data.get("bbrstinfo"),
            bbsys=data.get("bbsys"),
            runtimestats=data.get("runtimestats"),
            unprocessed_error=data.get("unprocessedError"),
        )


@dataclass(frozen=True)
class ServicesShadow:
    """CONFIRMED LIVE (this session, chairstacker) -- complete key
    list of the named "ro-services" shadow, the third of the four
    previously-unknown read-only shadows found via MQTTTopics.java.
    Only key NAMES confirmed so far, not values. "optFeats"
    (optional features?) plausibly a feature-flag/capability list,
    unconfirmed."""

    opt_feats: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ServicesShadow:
        return cls(opt_feats=data.get("optFeats"))


@dataclass(frozen=True)
class ConfigInfoShadow:
    """CONFIRMED LIVE (this session, chairstacker) -- complete key
    list of the named "ro-configinfo" shadow, the last of the four
    previously-unknown read-only shadows found via MQTTTopics.java.
    Only key NAMES confirmed so far, not values.

    "passwordHash" -- PRIVACY NOTE: if this genuinely holds a password
    hash, it's sensitive regardless of being a hash rather than
    plaintext. Not automatically redacted by this model itself
    (redaction happens at the diagnostics/report layer, see
    diagnostics.py's Report.redact()/sensitive-key masking) -- flagged
    here so anyone handling this shadow's real content directly is
    aware, not just relying on downstream redaction to catch it.
    "hwPartsRev" plausibly a hardware parts revision string,
    unconfirmed."""

    hw_parts_rev: Any | None = None
    password_hash: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ConfigInfoShadow:
        return cls(
            hw_parts_rev=data.get("hwPartsRev"),
            password_hash=data.get("passwordHash"),
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
    and --watch-aws-tree flags exist to actually test this now.

    FOUND (this session, chairstacker, live -- the actual resolution
    of the search this whole docstring documents): the named shadow
    "ro-currentstate" (one of four previously-unknown read-only
    shadows found via MQTTTopics.java, see verify_named_shadows.py's
    own module docstring for that discovery) reports these keys:
    "batPct", "bin", "cleanMissionStatus", "detectedPad", "dock",
    "lastDisconnect", "p2maps", "regDate", "runtimeStats",
    "svcEndpoints", "tankPresent", "tz". "batPct" -- battery
    percentage -- is exactly what this entire investigation was
    searching for, and "dock"/"cleanMissionStatus" plausibly cover
    charging/docked state and live mission status respectively.
    "cleanMissionStatus" specifically matches the exact event name
    this project's own native decompilation found on
    AssetIotTopicFactory months earlier (session covering
    mission/timeline/report's own discovery) -- two independent
    findings now pointing at the same underlying concept from
    different angles.

    A NOTE ON THE EARLIER RETRACTION ABOVE: this session's own
    "ARCHITECTURE, CORRECTED" paragraph above retracted an earlier
    parallel-track claim that a "batPct" finding belonged to the
    Classic-layer RobotV1/RobotV2 classes, unrelated to Prime. That
    retraction concerned a SPECIFIC claim about WHERE a particular
    piece of decompiled code lived (Classic-only source), not a
    claim that the field NAME "batPct" could never appear on a Prime
    device's own cloud data -- iRobot plausibly reuses the same field
    vocabulary across Classic and Prime cloud infrastructure even
    where the underlying delivery mechanism differs. This live
    "ro-currentstate" result is a directly-observed key on a real
    Prime device's own named shadow, independent of and not
    contradicted by that earlier retraction.

    STILL UNCONFIRMED: only the KEY NAMES are known so far (from
    get_named_shadow()'s reported-keys summary) -- the actual VALUES
    (is batPct 0-100? an int or a string? does "dock" mean boolean
    docked-or-not, or something richer?) have not yet been seen. A
    follow-up request for the full reported payload (not just the key
    list) is the natural next step before modeling this shadow's
    content as a proper dataclass."""

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


