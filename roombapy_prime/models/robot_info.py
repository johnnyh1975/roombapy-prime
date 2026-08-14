"""Robot/household metadata: parts, serial number, settings, status, cleaning profiles, default routines.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from ..vendor_errors import vendor_error

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from .enums_common import RoomCategory, _enum_or_none
from .mission_control import CommandParams, PadWetnessParam, RegionType


class CleaningProfileType(StrEnum):
    """CORRECTED (APK 3.0.0, Dart `ProfileType`): the wire values are
    lowercase -- `light`, `normal`, `deep`, `smart`.

    The uppercase values this enum carried were the Kotlin CONSTANT
    NAMES of `CleaningProfileType`, whose wire-value map is empty. The
    androguard reading behind them was correct about what it read; it
    was reading the wrong thing. Same mistake as the DND commands and
    `RegionType.TID`, all three from the same root: a Kotlin enum
    without `@SerialName` has no wire values to report, and its member
    names are not a substitute.

    The Dart layer states them outright, member by member:
    `lightClean -> "light"`, `normalClean -> "normal"`,
    `deepClean -> "deep"`, `smartClean -> "smart"`. A separate
    `IrobotCleanProfileType` carries the ordinals 0-3 in the same order.

    EFFECT OF THE OLD VALUES was read-side only, and quiet:
    `_enum_or_none()` failed to match a real `"deep"` against `"DEEP"`
    and fell through to the raw string, so `CleaningProfile.profile`
    held a str where an enum was expected. Nothing raised, and any
    caller comparing against the enum simply never matched.

    Gated on `digiCap.cleaningProfiles`."""

    DEEP = "deep"
    LIGHT = "light"
    NORMAL = "normal"
    SMART = "smart"


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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
    "status" values select each, is still NOT confirmed -- no capture
    contains a real edit response of any shape.

    THEY ARE NO LONGER LEFT TO THE CALLER TO GUESS BETWEEN.
    `MapEditResult` (models/map_editing.py) discriminates on the fields
    present and delegates the parsing here, and
    `PrimeRobot.edit_map_checked()` returns it.

    WHAT WAS WRONG WAS NOT THAT NOBODY CALLED THIS. A protocol library
    models more of the wire than any one consumer reads, and a shape
    class waiting for its caller is doing its job. The defect was that
    `MapEditResult` first parsed these same four fields AGAIN -- two
    parsing sites for one payload, which is the shape that let
    `pad_category` stay a string for months.

    edit_map() still returns raw JSON on purpose, so a first real
    response can be inspected whole."""

    status: Any | None = None
    p2mapv_id: str | None = None
    p2map_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapEditPartialSuccess:
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
    #: `user_orientation_rad` -- how the user rotated this map version.
    #: `P2MapData` declares it; the renderer had no way to match the
    #: app's orientation without it.
    user_orientation_rad: float | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapVersion:
        if not isinstance(data, dict):
            return cls()
        return cls(
            user_orientation_rad=data.get("user_orientation_rad"),
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
    #: `robot_pmap_sharing` -- whether this robot's maps are shared
    #: with the household. Declared and unread.
    pmap_sharing: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdRobot:
        if not isinstance(data, dict):
            return cls()
        return cls(
            pmap_sharing=data.get("robot_pmap_sharing"),
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
class PrecheckStatus:
    """NEW (app 3.0.0, `model/settings/precheck`) -- the robot's own
    pre-mission readiness check.

    `readiness` is the interesting field: it is the robot's verdict on
    whether it can start, computed before a command is sent rather than
    reported as a refusal afterwards. `readinessTm` timestamps it.

    `weather` in a vacuum robot's settings is unexplained. Recorded as
    the vendor spells it rather than dismissed -- a Braava deciding
    whether to mop on a humid day is a guess, and so is anything else.

    PLACEMENT CONFIRMED by the model path (`model/settings/…`), not
    inferred. Types stay permissive: no capture here contains it."""

    readiness: Any | None = None
    readiness_time: Any | None = None
    weather: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PrecheckStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(
            readiness=data.get("readiness"),
            readiness_time=data.get("readinessTm"),
            weather=data.get("weather"),
        )


@dataclass(frozen=True)
class FilterPackStatus:
    """NEW (app 3.0.0, `model/settings/filter_pack_status`) -- filter
    life remaining as a percentage, plus when it was last reset.

    THE ONLY PERCENTAGE-BASED CONSUMABLE FIGURE in this library. Every
    other maintenance counter here counts upward (missions, evacs, pad
    washes) and needs a threshold to mean anything; `pctLeft` is already
    the answer.

    `lastRstTm` is the reset timestamp, which pairs with
    `resetAssetHealth(partId)` -- the app resets consumable counters
    individually."""

    pct_left: int | None = None
    last_reset_time: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FilterPackStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(pct_left=data.get("pctLeft"), last_reset_time=data.get("lastRstTm"))


@dataclass(frozen=True)
class CutHeightStatus:
    """NEW (app 3.0.0, `model/settings/cut_height_status`) -- current,
    desired, minimum and maximum cutting height in millimetres.

    NOT A VACUUM FIELD. A cutting height in millimetres with a desired
    value alongside the current one belongs to a mower, and `cutHeight`
    sits in the same property registry as every Roomba field. Modelled
    because it is declared and costs nothing; nothing here suggests a
    Roomba will ever send it."""

    current_mm: int | None = None
    desired_mm: int | None = None
    min_mm: int | None = None
    max_mm: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CutHeightStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(
            current_mm=data.get("currentMM"),
            desired_mm=data.get("desiredMM"),
            min_mm=data.get("minMM"),
            max_mm=data.get("maxMM"),
        )


@dataclass(frozen=True)
class Langs2Status:
    """NEW (app 3.0.0, `model/settings/langs2`) -- the language pack
    state, previously kept whole as `RobotSettings.languages_raw`.

    Five of these eight keys are individually WRITABLE (`langs2.sLang`,
    `langs2.uLangs`, `langs2.dLangs`, `langs2.aSlots`, `langs2.sVer`),
    addressed with their dots intact -- so a caller changing the
    selected language needs to know which sub-key holds it. Reading
    them typed is what makes that possible.

    `languages_raw` stays as it is: this parses the same object, and a
    caller already relying on the raw dict keeps working."""

    #: WHAT A LANGUAGE SELECTOR WOULD BE BUILT FROM, and it is not the
    #: vendor's language enum.
    #:
    #: `DeviceLanguageType` numbers 27 languages -- english=2,
    #: german=4 and so on. The shadow carries none of that:
    #: chairstacker's robot reports `sLang: "en-US"` and
    #: `dLangs.langs: ["de-DE", "es-ES", "fr-CA", "en-US", "it-IT"]`.
    #: BCP-47 locale strings, not ids.
    #:
    #: So a selector built from `DeviceLanguageType` would write `2`
    #: where `"en-US"` is expected -- accepted by nothing, and the sort
    #: of mistake only real data catches. The option set is `dLangs`,
    #: which the robot supplies per device.
    #:
    #: `aSlots` is 1 on that robot, which reads as the number of
    #: language slots installed -- five available, one active. Not
    #: confirmed.
    a_slots: Any | None = None
    d_langs: Any | None = None
    langs: Any | None = None
    pack_id: Any | None = None
    s_lang: Any | None = None
    s_ver: Any | None = None
    u_langs: Any | None = None
    ver: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Langs2Status:
        if not isinstance(data, dict):
            return cls()
        return cls(
            a_slots=data.get("aSlots"),
            d_langs=data.get("dLangs"),
            langs=data.get("langs"),
            pack_id=data.get("packId"),
            s_lang=data.get("sLang"),
            s_ver=data.get("sVer"),
            u_langs=data.get("uLangs"),
            ver=data.get("ver"),
        )


@dataclass(frozen=True)
class RobotSettings:
    """Confirmed (real live response, get_settings()): complete
    content of the named "rw-settings" shadow for a SMART-tier device.
    Covers things like child lock, volume, timezone, pad wash
    settings, language list, auto-evac frequency, and various
    "*Allowed" permission flags."""

    audio_volume: int | None = None
    #: NINE VALID VALUES, AND `cap.autoevac` DECIDES WHICH APPLY.
    #:
    #: `ClearFreqType` (app 3.0.0) declares 0, 1, 2, 4, 10, 15, 25, 30,
    #: 50 -- the first three are "every / every 2nd / every 3rd routine",
    #: 4 is "on dock return", the rest are area-based.
    #:
    #: `CapAutoEvac` is the gate, and it is a LEVEL, not a flag:
    #:
    #:     0  taskEndOnly           no frequency choice at all
    #:     1  freqModes             0, 1, 2
    #:     2  freqWithArea          plus 10, 15, 25, 30, 50
    #:     3  taskEndOrDockReturn   plus 4
    #:
    #: FIELD-CONFIRMED, AND THE TWO NUMBERS AGREE: chairstacker's robot
    #: reports `cap.autoevac = 1` and `autoevacFreq = 1` -- level
    #: `freqModes`, set to "every 2nd routine". Internally consistent.
    #:
    #: AN EARLIER NOTE HERE GOT THIS HALF RIGHT. It said the per-SKU
    #: value list `[0, 10, 15, 25, 30]` was "a subset the app happens to
    #: show" and the enum was the real option set. The list is not
    #: arbitrary -- it is what `freqWithArea` offers. What was wrong was
    #: treating the enum as one flat option set: a selector built from
    #: all nine would offer area intervals to a robot that cannot do
    #: them, and one built from the SKU list alone could not represent
    #: this robot's actual setting. Read the cap, then pick the subset.
    autoevac_freq: int | None = None
    carpet_boost: bool | None = None
    child_lock: bool | None = None
    #: `CloudEnvironment` (app 3.0.0) names the four: `prod`,
    #: `int-test`, `prod-cn`, `stage-cn`. chairstacker's robot reports
    #: `prod`, which confirms both the field and the vocabulary.
    #:
    #: Kept a str rather than typed: the value selects which AWS
    #: deployment a robot talks to, and a CN robot appearing with a
    #: fifth environment must not fail to parse.
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
    #: TWO RANGES IN ONE FIELD, AND THE SKU LIST COVERS ONE.
    #:
    #: `ReturnByMode` (app 3.0.0) declares six values across two ranges:
    #:
    #:     0   after each room        100  Standard
    #:     1   after time interval    101  Medium
    #:     2   after area interval    102  High
    #:
    #: Below 100 selects WHEN to return; 100 and above selects HOW
    #: THOROUGHLY. The per-SKU value list gives only [100, 101, 102].
    #:
    #: FIELD-SETTLED: chairstacker's robot reports `pwReturn = 2` with
    #: `pwAreaInterval = 10` -- wash by area, every 10 units, internally
    #: consistent and outside the SKU list entirely.
    #:
    #: A selector must therefore span both ranges. Splitting them into
    #: two entities would also be wrong: one write sets this one field,
    #: and `_updateWashFreqByType` branches on the value's type rather
    #: than writing a pair.
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
    #: FIVE ADDITIONS, ALL WITH PLACEMENT CONFIRMED BY THE VENDOR'S OWN
    #: MODEL PATHS (`model/settings/…`) rather than inferred.
    #:
    #: `pad_wash_heat` is the strongest of the five: it is the ONLY one
    #: of the seventy-nine unread properties with a ShadowField entry of
    #: its own -- `pwHeat`, shadow SETTINGS, kind Writing, type Integer
    #: -- and it also appears in the twenty-four-key writable switch.
    #: Two independent sources agreeing on both shadow and direction.
    #:
    #: It is also one of the six controls Issue #46 is waiting on, with
    #: values 0/1/2.
    #:
    #: `precheck` and `filter_pack` are the two with real day-to-day
    #: value: readiness before a mission, and the only percentage-based
    #: consumable figure anywhere in this library.
    pad_wash_heat: int | None = None
    precheck: PrecheckStatus | None = None
    filter_pack: FilterPackStatus | None = None
    cut_height: CutHeightStatus | None = None
    languages: Langs2Status | None = None
    """Raw "langs2" object (aSlots, dLangs.langs/ver, sLang, sVer) --
    deliberately not further broken down, little added value for a
    dedicated model."""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotSettings:
        if not isinstance(data, dict):
            return cls()
        audio = data.get("audio") or {}
        # BORROWED FROM THE COMMAND DOMAIN, AND UNVERIFIED HERE.
        #
        # PadWetnessParam lives in mission_control.py, decompiled from
        # com.irobot.data.missioncommand.datamodels (Command.kt). It is a
        # COMMAND parameter. This is the rw-settings SHADOW, a different
        # place with similar-looking contents, and no capture of the
        # shadow's padWetness has ever been seen.
        #
        # The two agreeing is plausible and not established. If the
        # shadow uses snake_case where the command uses camelCase --
        # `pad_plate` against `padPlate` -- this parses to None silently
        # and nothing anywhere reports a problem.
        #
        # WHAT WOULD SETTLE IT: one diagnostics download from a mopping
        # robot, showing rw-settings.padWetness verbatim. Until then
        # nothing writes this field: ha_roomba_plus deliberately ships no
        # pad-wetness control.
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
            pad_wash_heat=data.get("pwHeat"),
            precheck=(
                PrecheckStatus.from_json(data["precheck"])
                if isinstance(data.get("precheck"), dict)
                else None
            ),
            filter_pack=(
                FilterPackStatus.from_json(data["filterStatus"])
                if isinstance(data.get("filterStatus"), dict)
                else None
            ),
            cut_height=(
                CutHeightStatus.from_json(data["cutHeight"])
                if isinstance(data.get("cutHeight"), dict)
                else None
            ),
            languages=(
                Langs2Status.from_json(data["langs2"])
                if isinstance(data.get("langs2"), dict)
                else None
            ),
        )


@dataclass(frozen=True)
class DigiCap:
    """CONFIRMED LIVE, REAL VALUES (this session, chairstacker's
    raw_shadows.json) -- a small, separate capability namespace from
    "cap" (see CapabilityFlags below), nested under the classic/
    unnamed shadow's own "digiCap" key. Real values seen: app_ver=1,
    timeline=1. "timeline" plausibly correlates with the already-
    confirmed mission/timeline/report topic (a per-device flag for
    whether this robot even sends timeline events at all) -- a
    hypothesis, not confirmed by name alone."""

    app_ver: int | None = None
    timeline: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DigiCap:
        if not isinstance(data, dict):
            return cls()
        return cls(app_ver=data.get("appVer"), timeline=data.get("timeline"))


@dataclass(frozen=True)
class CapabilityFlags:
    """CONFIRMED LIVE, REAL VALUES (this session, chairstacker's
    raw_shadows.json) -- the classic/unnamed shadow's "cap" object, 36
    fields. THE ONLY PLACE, across every shadow this project has ever
    queried (named or unnamed), that describes what a SPECIFIC device
    can actually do -- not "what Prime supports in general", but this
    one robot's own real, per-device/per-firmware capability flags.

    Values are ints, NOT bools -- several are graduated/tiered rather
    than on/off (real examples: carpet_boost=3, floor_type_detect=4,
    suction_lvl=4, operating_mode=550) -- resist the temptation to
    truthy-check these as booleans; a 0 is a confirmed negative
    ("this device cannot do X"), but a nonzero value's actual meaning
    beyond "some level of support" is otherwise unconfirmed per field.

    Classic's own ha_roomba_plus integration already gates real
    features off an equivalent "cap" dict this same way (see
    ha_roomba_plus's const.py, e.g. cap.get("carpetBoost")/
    cap.get("pose")/cap.get("maps")) -- this is the same underlying
    concept for Prime devices, previously entirely unavailable because
    no Prime capture had ever reached this specific (unnamed) shadow
    before. Prime-side entities currently have NO capability gating at
    all -- built without knowing this data existed."""

    wifi_5ghz: int | None = None

    # ADDED FROM A REAL CAPTURE (arielgr, sku Y414040). These five were
    # present in that robot's cap object and were being silently
    # DROPPED -- from_json() only reads the fields declared here, so an
    # unmodelled capability vanishes without any error.
    #
    # That matters more than it looks: this cap object is the only
    # place that describes what a SPECIFIC device can do, and it is
    # what feature gating reads. A capability we never see is a feature
    # we can never offer, and nothing would ever have told us.
    #
    # Meanings are inferred from their names and NOT confirmed:
    #   cmds                 -- observed 1
    #   e_cmd                -- observed 0
    #   mop_lift             -- observed 0; presumably a liftable mop pad
    #   odoa                 -- observed 0; obstacle detection/avoidance
    #   p2maps_editv2_feats  -- observed 3423, clearly a bitfield rather
    #                           than a level, so do NOT compare it as one
    cmds: int | None = None
    e_cmd: int | None = None
    mop_lift: int | None = None
    odoa: int | None = None
    p2maps_editv2_feats: int | None = None

    area: int | None = None
    autoevac: int | None = None
    bin_full_detect: int | None = None
    #: `addOnHw` and `pose` -- both in `Robot$Capabilities`, both in
    #: real captures (@connormxy's `addOnHw: 0`, `pose: 2`), neither
    #: read.
    #:
    #: `pose` is the interesting one: it is the flag that separates a
    #: robot reporting its position from one that does not, which is
    #: exactly the EPHEMERAL/SMART distinction this project derives by
    #: other means.
    add_on_hw: int | None = None
    pose: int | None = None
    carpet_boost: int | None = None
    d_pause: int | None = None
    dnd: int | None = None
    dock_comm: int | None = None
    expecting_user_conf: int | None = None
    floor_type_detect: int | None = None
    idl: int | None = None
    lang: int | None = None
    lang_ota: int | None = None
    lmap: int | None = None
    log: int | None = None
    maps: int | None = None
    matter: int | None = None
    mc: int | None = None
    multi_pass: int | None = None
    ns: int | None = None
    o_mode: int | None = None
    ota: int | None = None
    pp_wet_lvl: int | None = None
    prov: int | None = None
    pw: int | None = None
    sched: int | None = None
    scrub: int | None = None
    suction_lvl: int | None = None
    svc_conf: int | None = None
    t_line: int | None = None
    vm_strat: int | None = None
    ble_log: int | None = None
    d_spot: int | None = None
    map_max: int | None = None
    p2maps: int | None = None
    sa_sku: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CapabilityFlags:
        if not isinstance(data, dict):
            return cls()
        return cls(
            wifi_5ghz=data.get("5ghz"),
            cmds=data.get("cmds"),
            e_cmd=data.get("eCmd"),
            mop_lift=data.get("mopLift"),
            odoa=data.get("odoa"),
            p2maps_editv2_feats=data.get("p2maps_editv2_feats"),
            area=data.get("area"),
            autoevac=data.get("autoevac"),
            bin_full_detect=data.get("binFullDetect"),
            add_on_hw=data.get("addOnHw"),
            pose=data.get("pose"),
            carpet_boost=data.get("carpetBoost"),
            d_pause=data.get("dPause"),
            dnd=data.get("dnd"),
            dock_comm=data.get("dockComm"),
            expecting_user_conf=data.get("expectingUserConf"),
            floor_type_detect=data.get("floorTypeDetect"),
            idl=data.get("idl"),
            lang=data.get("lang"),
            lang_ota=data.get("langOta"),
            lmap=data.get("lmap"),
            log=data.get("log"),
            maps=data.get("maps"),
            matter=data.get("matter"),
            mc=data.get("mc"),
            multi_pass=data.get("multiPass"),
            ns=data.get("ns"),
            o_mode=data.get("oMode"),
            ota=data.get("ota"),
            pp_wet_lvl=data.get("ppWetLvl"),
            prov=data.get("prov"),
            pw=data.get("pw"),
            sched=data.get("sched"),
            scrub=data.get("scrub"),
            suction_lvl=data.get("suctionLvl"),
            svc_conf=data.get("svcConf"),
            t_line=data.get("tLine"),
            vm_strat=data.get("vmStrat"),
            ble_log=data.get("bleLog"),
            d_spot=data.get("dSpot"),
            map_max=data.get("mapMax"),
            p2maps=data.get("p2maps"),
            sa_sku=data.get("saSku"),
        )


@dataclass(frozen=True)
class ClassicShadowState:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (this session,
    chairstacker's raw_shadows.json capture) -- the actual, complete
    content of get_state()'s classic/unnamed shadow, previously
    returned only as an untyped ShadowResponse with no dedicated model
    at all (unlike get_settings()'s RobotSettings). Confirmed real
    fields: cap (see CapabilityFlags), digiCap (see DigiCap),
    cleanSchedule2 (kept raw -- see ScheduleShadow's own docstring for
    why; same shape, reused rather than duplicated), schedHold, sku,
    soldAsSku, svcEndpoints.

    IMPORTANT, SEPARATE FROM rw-settings.sched_hold: this shadow has
    its OWN "schedHold" field, confirmed via metadata timestamps to be
    updated independently (~January 2026) from rw-settings' own
    sched_hold (~unchanged since device registration in the one real
    capture seen) -- NOT necessarily the same value at any given
    moment. Before building a schedule-hold switch against
    rw-settings.sched_hold alone, confirm which of the two the actual
    schedule executor reads -- see the project's own notes on this
    open question."""

    digi_cap: DigiCap | None = None
    nsmip: int | None = None
    cap: CapabilityFlags | None = None
    clean_schedule2_raw: list[Any] = field(default_factory=list)
    sched_hold: bool | None = None
    sku: str | None = None
    sold_as_sku: str | None = None
    svc_endpoints: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ClassicShadowState:
        if not isinstance(data, dict):
            return cls()
        digi_cap = data.get("digiCap")
        cap = data.get("cap")
        return cls(
            digi_cap=DigiCap.from_json(digi_cap) if isinstance(digi_cap, dict) else None,
            nsmip=data.get("nsmip"),
            cap=CapabilityFlags.from_json(cap) if isinstance(cap, dict) else None,
            clean_schedule2_raw=data.get("cleanSchedule2") or [],
            sched_hold=data.get("schedHold"),
            sku=data.get("sku"),
            sold_as_sku=data.get("soldAsSku"),
            svc_endpoints=data.get("svcEndpoints"),
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
    #: THE `nsmip*` AND `svcEndpoints*` FAMILIES ARE THIS ONE KEY,
    #: NAMED ONCE PER SHADOW.
    #:
    #: Twelve names in app 3.0.0's property registry were carried in
    #: this project as unresolvable -- `nsmipThing`, `nsmipSettings`,
    #: `nsmipConfiginfo`, `nsmipSchedule`, `nsmipServices`,
    #: `nsmipSoftware` and the matching `svcEndpoints*` set. What
    #: `nsmip` stands for was called an open question and the family
    #: was filed as a protocol gap.
    #:
    #: The suffixes are the shadow list: Thing, Settings, Configinfo,
    #: Schedule, Services, Software, CurrentState. A flat registry needs
    #: distinct names for one key on different shadows, so it appends
    #: where the key lives. None of the twelve is a wire key.
    #:
    #: CONFIRMED AGAINST A REAL ROBOT: chairstacker's dump carries bare
    #: `nsmip` on seven of nine shadows and bare `svcEndpoints` on all
    #: nine -- and `nsmip` is absent from exactly constatus, currentstate
    #: and stats, the three with no `nsmip*` registry entry.
    #:
    #: SO NOTHING WAS EVER MISSING. Both keys have been read all along.
    #: What was unresolved was a naming convention, mistaken for a
    #: protocol gap because the names were looked at and the shadows
    #: were not.
    #:
    #: `scvEndpointsSettings` and `scvEndpointsThing` are the vendor's
    #: own transposition, in two of the fourteen.
    nsmip: int | None = None
    #: ADDED (app 3.0.0, `model/schedule/clean_schedule`, which names
    #: this shadow). The OLDER schedule format, one field: `cycle`.
    #:
    #: Not a typo for `cleanSchedule2` -- both are declared, separately.
    #: A robot on older firmware may report this instead, and until now
    #: it was dropped, leaving a genuinely scheduled robot looking
    #: unscheduled.
    #:
    #: Kept raw for the same reason `cleanSchedule2` is: the inner
    #: structure is parsed elsewhere, and duplicating it here would
    #: diverge from that rather than reuse it.
    clean_schedule_raw: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ScheduleShadow:
        if not isinstance(data, dict):
            return cls()
        return cls(
            clean_schedule2_raw=data.get("cleanSchedule2") or [],
            nsmip=data.get("nsmip"),
            clean_schedule_raw=data.get("cleanSchedule"),
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

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#robot_infoconnectionstatusshadow
    """

    connected: bool | None = None
    connected_v2: bool | None = None
    echo: bool | None = None
    #: ADDED (app 3.0.0, `model/connection_status` declares four fields,
    #: not three). Kept raw: the vendor's own `model/current_state/
    #: svc_endpoints` has a single `svcDeplId`, but the five
    #: `svcEndpoints*` scalars in the property registry suggest a wider
    #: shape, and nothing here settles which arrives on this shadow.
    svc_endpoints: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ConnectionStatusShadow:
        if not isinstance(data, dict):
            return cls()
        return cls(
            connected=data.get("connected"),
            connected_v2=data.get("connectedv2"),
            echo=data.get("echo"),
            svc_endpoints=(
                data["svcEndpoints"] if isinstance(data.get("svcEndpoints"), dict) else None
            ),
        )


@dataclass(frozen=True)
class SubModuleSwVersions:
    """CONFIRMED FROM REAL DATA (chairstacker's raw_shadows.json, a
    Combo 405): per-subsystem firmware versions, previously kept whole
    as an untyped blob.

        con    "sdk-v9.3.7"    connectivity SDK
        linux  "4.9.84"        kernel
        nav    "4.6.150"       navigation
        mcu    "32"            microcontroller

    THE VENDOR DECLARES TWELVE (`model/software/sub_mod_sw_ver`: aoa,
    cam, con, eco, linux, mcu, mob, mobBtl, nav, parcels, pwr, sft); a
    real robot sent four. Both are modelled -- the eight unseen ones
    cost nothing and a different SKU may well report them.

    THAT ASYMMETRY IS THE POINT OF THIS CLASS. Everything else added in
    this round was declared-but-never-observed. These four are the
    reverse: observed on real hardware and previously unread, because
    the field arrived as a nested object and was stored as `Any`
    without anyone opening it.

    `deploymentMpkg` on the same shadow carries "sdk-v8.6.2" while
    `con` here says "sdk-v9.3.7" -- the deployment package name records
    the version it shipped as, not what is installed now. A caller
    reporting firmware should prefer these."""

    aoa: Any | None = None
    cam: Any | None = None
    con: Any | None = None
    eco: Any | None = None
    linux: Any | None = None
    mcu: Any | None = None
    mob: Any | None = None
    mob_btl: Any | None = None
    nav: Any | None = None
    parcels: Any | None = None
    pwr: Any | None = None
    sft: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SubModuleSwVersions:
        if not isinstance(data, dict):
            return cls()
        return cls(
            aoa=data.get("aoa"),
            cam=data.get("cam"),
            con=data.get("con"),
            eco=data.get("eco"),
            linux=data.get("linux"),
            mcu=data.get("mcu"),
            mob=data.get("mob"),
            mob_btl=data.get("mobBtl"),
            nav=data.get("nav"),
            parcels=data.get("parcels"),
            pwr=data.get("pwr"),
            sft=data.get("sft"),
        )


def _ml(value: Any) -> Any:
    """Both parcel-deployment objects wrap their content in a single
    `ml` key (`model/software/parcel_deployment_id` and `…_state`).

    Unwrapped rather than modelled as two one-field classes: a dataclass
    per single scalar buys nothing, and a robot sending the bare value
    instead of the wrapper still parses."""
    if isinstance(value, dict):
        return value.get("ml")
    return value


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
    #: THE SAME OBJECT, PARSED. `submodule_sw_version` above stays as
    #: the raw blob it has always been so existing callers keep working;
    #: this reads the four subsystem versions a real robot actually
    #: sends. See SubModuleSwVersions.
    sub_module_versions: SubModuleSwVersions | None = None
    #: TWO OBJECTS THE VENDOR PLACES IN THIS SHADOW
    #: (`model/software/parcel_deployment_id` and `…_state`), each
    #: carrying a single `ml` field.
    #:
    #: A parcel deployment is a partial firmware update -- `sub_mod_sw_ver`
    #: lists twelve subsystem versions, and these two track a deployment
    #: to one of them rather than to the robot as a whole. That is a
    #: different thing from `deployment_id`/`deployment_state` above,
    #: which cover the full package.
    #:
    #: What `ml` abbreviates is not established. Kept as the vendor's
    #: single key rather than expanded into a guess.
    parcel_deployment_id: Any | None = None
    parcel_deployment_state: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SoftwareStatusShadow:
        if not isinstance(data, dict):
            return cls()
        return cls(
            deployment_id=data.get("deploymentId"),
            deployment_mpkg=data.get("deploymentMpkg"),
            deployment_state=data.get("deploymentState"),
            imu_recal=data.get("imuRecal"),
            last_command=data.get("lastCommand"),
            last_sw_update=data.get("lastSwUpdate"),
            software_version=data.get("softwareVer"),
            submodule_sw_version=data.get("subModSwVer"),
            sub_module_versions=(
                SubModuleSwVersions.from_json(data["subModSwVer"])
                if isinstance(data.get("subModSwVer"), dict)
                else None
            ),
            parcel_deployment_id=_ml(data.get("parcelDeploymentId")),
            parcel_deployment_state=_ml(data.get("parcelDeploymentState")),
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
        if not isinstance(data, dict):
            return cls()
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
    #: `expireTm` and `rechrgTm`, both Long in `CleanMissionStatusData`
    #: and neither read here.
    #:
    #: They answer a question nothing else can: a paused robot shows
    #: `phase: "pause"` and no more, while `expire_time` says when that
    #: pause lapses and `recharge_time` when it intends to resume after
    #: charging. Classic surfaces both; Prime had the same numbers in
    #: the same object and dropped them.
    expire_time: int | None = None
    recharge_time: int | None = None
    #: THE SAME THREE QUANTITIES AS MINUTES, which the vendor declares
    #: alongside the timestamps (`model/current_state/clean_mission_status`:
    #: `mssnM`/`expireM`/`rechrgM` beside `mssnStrtTm`/`expireTm`/`rechrgTm`).
    #:
    #: Worth having both: the `*Tm` fields are absolute timestamps, so a
    #: remaining-time display has to compute the difference and depends
    #: on the robot's clock agreeing with the reader's. These are
    #: already durations.
    #:
    #: WHETHER A ROBOT ACTUALLY FILLS THEM IS UNCONFIRMED -- no capture
    #: this project holds contains any of the three, and the vendor
    #: declaring a field is not the robot sending it. A caller wanting
    #: remaining time should prefer these when present and fall back to
    #: the timestamps, not the other way round.
    mission_minutes: int | None = None
    expire_minutes: int | None = None
    recharge_minutes: int | None = None
    n_missions: int | None = None
    not_ready: int | None = None
    operating_mode: int | None = None
    phase: str | None = None
    sqft: int | None = None

    @property
    def error_text(self) -> dict[str, str] | None:
        """iRobot's own title and explanation for `error`, or None.

        **A LIBRARY THAT SURFACES ERRORS SHOULD NAME THEM.** This model
        carried `error: 46` and every caller had to look it up
        somewhere -- which meant asking the maintainers, because until
        now nothing here could say what 46 means.

        None for a code iRobot does not document, and that distinction
        is worth keeping: @connormxy's 236 is in neither the app's
        catalogue nor anywhere else, and a caller should be able to say
        "error 236, undocumented" rather than "no error".

        `@val` is left in place -- it is the robot's name in iRobot's
        strings, and a caller that knows it can substitute.
        """
        return vendor_error(self.error)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleanMissionStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(
            cond_not_ready=data.get("condNotReady") or [],
            cycle=data.get("cycle"),
            error=data.get("error"),
            initiator=data.get("initiator"),
            mission_id=data.get("missionId"),
            mission_start_time=data.get("mssnStrtTm"),
            expire_time=data.get("expireTm"),
            recharge_time=data.get("rechrgTm"),
            mission_minutes=data.get("mssnM"),
            expire_minutes=data.get("expireM"),
            recharge_minutes=data.get("rechrgM"),
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

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#robot_infodockstate

    THE SERVER SENDS CODES THIS ENUM DOES NOT HAVE. Confirmed for 671:
    a controlled before/after (@chairstacker, dock fwVer 20) read
    dock.pwState = 671 with the clean water tank removed and 601
    (PAD_WASH_OKAY) the moment it went back in, while dock.state,
    dock.error and pdState did not move at all.

    671 does not exist anywhere in the iRobot APK -- the pad-wash family
    ends at 669 (PAD_WASH_PAD_ACTUATOR_STALL_ERROR), and there is no
    second, newer dock-state table. The app's own DockStateImpl carries
    the fallback string "Unknown dock state %d", so the official app
    shows nothing better than a number here either.

    So the server is ahead of app version 2.2.4, the same way it sends
    `is_smart_clean_fav` on schedules that the app does not model. A
    consumer must handle unknown values rather than assume this enum is
    exhaustive; ha_roomba_plus keeps a separate field-observed table for
    them, deliberately NOT merged into this one so that decompiled
    values stay distinguishable from inferred ones.

    UNPROVEN, RECORDED SO IT IS NOT LOST: the low values 1-3 here have
    the same numbers as the `dock.cap` flags in the shadow. Two field
    captures read `pw: 1` with `pd: 2` and `pd: 3` respectively, which
    would map onto PAD_WASH_UNHEATED_WATER plus PAD_DRY_UNHEATED_AIR
    and PAD_DRY_HEATED_AIR -- i.e. dock.cap would describe WHICH KIND
    of wash and dry a dock supports rather than a bare capability level.

    That is a numeric coincidence across two namespaces and nothing
    more. This project has twice been caught by exactly that shape: 671
    filed into the mission-error table because the number looked
    plausible there, and `operatingMode` carrying different meanings in
    a command and in mission status. Do not build on it. It is written
    down as a question for the next APK round, not as a finding.
    """

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


class ScrubSupport(IntEnum):
    """`cap.scrub` (app 3.0.0, `ScrubSupport`).

    FOUR LEVELS WHERE THIS PROJECT SAW A NUMBER. Both testers report 3,
    the finest granularity the vendor defines, and nothing reads it.

    `perRoom` IS WHY THERE IS NO HOUSEHOLD SCRUB SWITCH, and that turns
    out to be right rather than an oversight. `swScrub` is writable in
    rw-settings and ha_roomba_plus exposes no control for it; at level 3
    scrubbing is a per-room property, so a single switch would write a
    value the robot overrides per region. It IS carried as a per-region
    parameter, which matches the capability.

    At level 1 -- software-only -- a household switch would be exactly
    right. Nobody has reported a level-1 robot, so the question has not
    arisen; this enum is what would answer it."""

    NONE = 0
    SOFTWARE_ONLY = 1
    SHADOW_AND_WHOLE_JOB = 2
    PER_ROOM = 3


class PointCleanSupport(IntEnum):
    """`cap.dSpot` (app 3.0.0, `CapDSpot`).

    Read by nothing today. chairstacker's robot reports 1.

    `pointCleanWithHeatedWater` at level 2 is a different command, not
    a better one -- a spot clean that also heats. A caller offering
    heated point cleaning on a level-1 robot would send something it
    cannot do."""

    UNSUPPORTED = 0
    POINT_CLEAN = 1
    POINT_CLEAN_WITH_HEATED_WATER = 2


class MidMissionAdjustments(IntEnum):
    """`cap.mc` (app 3.0.0, `MidMissionCleanAdjustmentsType`).

    Whether a room can be skipped while cleaning, and for which mission
    kinds. Both testers report 3.

    THE LEVELS ARE NOT A LADDER. `skipForDRCMissionsOnly` and
    `skipForDRCAndCleanAllMissions` say WHICH missions allow it;
    `skipCurrentRoomOnly` says WHAT can be skipped. Reading 3 as "the
    most capable" is the obvious mistake and the numbering invites
    it."""

    UNSUPPORTED = 0
    DRC_MISSIONS_ONLY = 1
    DRC_AND_CLEAN_ALL_MISSIONS = 2
    SKIP_CURRENT_ROOM_ONLY = 3


class DockEvacuation(IntEnum):
    """`dock.cap.evac` (app 3.0.0, `DockEvacuationType`)."""

    NOT_AVAILABLE = 0
    AVAILABLE = 1


class DockPadDrying(IntEnum):
    """`dock.cap.pd` (app 3.0.0, `DockPadDryingType`).

    NOT A FLAG WITH EXTRA STEPS. `unheatedAir` and `heatedAir` are
    different hardware, and a caller offering a heated-drying option to
    a level-2 dock would be offering something it cannot do.
    chairstacker's dock reads 2."""

    NOT_SUPPORTED = 0
    BASIC = 1
    UNHEATED_AIR = 2
    HEATED_AIR = 3


class DockPadWashing(IntEnum):
    """`dock.cap.pw` (app 3.0.0, `DockPadWashingType`).

    THIS ONE GATES A SETTING. `pwHeat` accepts `HeatType` 0/1/2, but
    only a level-3 dock can produce high heat and only level 2 and above
    can heat at all. chairstacker's dock reads 1, which is why his robot
    has no `pwHeat` key at all."""

    NOT_SUPPORTED = 0
    SUPPORTED = 1
    HEATED = 2
    HIGH_HEAT = 3


class DockPadWetOut(IntEnum):
    """`dock.cap.pwo` (app 3.0.0, `DockPadWetOutType`).

    Resolves a field this library carried as "meaning genuinely
    unclear" for months: pad wet-out, supported or not."""

    NOT_SUPPORTED = 0
    SUPPORTED = 1


class DockFluidRefill(IntEnum):
    """`dock.cap.fr` (app 3.0.0, `DockFluidRefillType`).

    THREE STATES, NOT TWO, and the distinction matters for controls:
    `controllable` means the user can trigger a refill, `automatic`
    means the dock decides. A refill button belongs only on the
    first."""

    NOT_AVAILABLE = 0
    CONTROLLABLE = 1
    AUTOMATIC = 2


class DockDetergent(IntEnum):
    """`detergent` (app 3.0.0, `DockDetergentType`)."""

    NOT_AVAILABLE = 0
    CONTROLLABLE = 1


@dataclass(frozen=True)
class DockCapabilities:
    """CONFIRMED LIVE (chairstacker, real ro-currentstate payload,
    nested under dock.cap), and each key now resolved by name against
    the vendor's own capability table rather than guessed from the
    abbreviation:

        evac  dock.cap.evac   auto-evacuation
        pd    dock.cap.pd     pad drying
        pw    dock.cap.pw     pad washing
        pwo   dock.cap.pwo    pad wet-out
        fr    dock.cap.fr     fluid refill      <- ADDED

    `pwo` was carried here as "meaning genuinely unclear". The
    capability mapping names it `dockPadWetOut`, which is what the
    abbreviation says once the expansion is known.

    `fr` WAS MISSING. It appears in the capability gate table AND in
    `_initDockCap`, the app's own dock-capability builder, alongside the
    four already modelled -- and `DockStatus` has carried `frState`, the
    fluid-refill STATE, all along. A dock could report that it was
    refilling while this model denied it could refill at all.

    NOT HERE: `detergent`. `_initDockCap` builds it alongside these
    five, which makes it easy to file under dock capabilities, but its
    key path is top-level `detergent` -- a sibling of `dock`, not a
    child of `dock.cap`. It is modelled on CurrentStateShadow, where it
    actually arrives.

    ALL OF THESE ARE INTEGERS, NOT BOOLEANS -- levels, not flags. A
    caller gating a feature on presence alone will enable it at level 0.
    """

    evac: int | None = None
    pad_dry: int | None = None
    pad_wash: int | None = None
    pad_wash_or: int | None = None
    fluid_refill: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockCapabilities:
        if not isinstance(data, dict):
            return cls()
        return cls(
            evac=data.get("evac"),
            pad_dry=data.get("pd"),
            pad_wash=data.get("pw"),
            pad_wash_or=data.get("pwo"),
            fluid_refill=data.get("fr"),
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
    #: SIX FIELDS `DockStatusData` DECLARES AND NOTHING WAS READING.
    #:
    #: `frState` is the fluid-refill state, the dock counterpart to
    #: `pwState` and `pdState` which this model has had all along -- so
    #: a dock could report that it was refilling and nothing here could
    #: say so.
    #:
    #: `fwVerSec`, `hwRev`, `varId` and `pn` are new in app 3.0.0;
    #: `dock_id` was in 2.2.4 and simply not taken.
    #: TWO SOURCES DISAGREE ABOUT WHERE `detergent` LIVES, so it is read
    #: in both places.
    #:
    #: The capability gate table gives its key path as top-level
    #: `detergent`; `model/current_state/dock_status` lists `detergent`
    #: among the dock's own fields. Both are the vendor's, and nothing
    #: available here settles which the robot actually sends -- possibly
    #: both, since the gate table also abbreviates paths elsewhere.
    #:
    #: Picking one and being wrong costs a permanent None, which is the
    #: failure mode this library keeps finding after the fact. Reading
    #: both costs a dict lookup. See CurrentStateShadow.detergent for
    #: the other half.
    detergent: int | None = None
    fr_state: int | None = None
    fw_version_secondary: str | None = None
    hardware_revision: int | None = None
    variant_id: int | None = None
    part_number: str | None = None
    dock_id: str | None = None
    known: bool | None = None
    pd_state: DockState | None = None
    pw_state: DockState | None = None
    state: DockState | None = None

    #: Clean water tank level, and NOT reported by every dock.
    #:
    #: Two captures, one dock each: fwVer 24 with dock.cap.pd 3 sends
    #: tankLvl 100; fwVer 20 with dock.cap.pd 2 never sends the key at
    #: all, not even while pad washing fails for lack of water. Two
    #: variables differ at once, so which one governs is not decidable
    #: from the field, and the APK cannot settle it either: pd/pw/pwo
    #: are not literals, the mapping lives in a runtime-filled
    #: map<string, DockCapability>, and DockCapability itself is purely
    #: categorical (Unknown, Evacuation, FluidReplenishment, PadWash,
    #: PadDry, Detergent) with no notion of a level 2 or 3.
    #:
    #: So consumers must gate on this being present, never on a
    #: capability flag or a particular pd value.
    #:
    #: `gwTankLvl` (grey water) is deliberately NOT modelled. The
    #: literal exists in libcorebase.so but its role could not be
    #: established, and it appears in no capture from either dock.
    tank_lvl: int | None = None

    @property
    def error_text(self) -> dict[str, str] | None:
        """iRobot's own title and explanation for `error`, or None.

        **A LIBRARY THAT SURFACES ERRORS SHOULD NAME THEM.** The dock's own error
        code, same treatment and every caller had to look it up
        somewhere -- which meant asking the maintainers, because until
        now nothing here could say what 46 means.

        None for a code iRobot does not document, and that distinction
        is worth keeping: @connormxy's 236 is in neither the app's
        catalogue nor anywhere else, and a caller should be able to say
        "error 236, undocumented" rather than "no error".

        `@val` is left in place -- it is the robot's name in iRobot's
        strings, and a caller that knows it can substitute.
        """
        return vendor_error(self.error)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockStatus:
        if not isinstance(data, dict):
            return cls()
        cap_data = data.get("cap")
        return cls(
            cap=DockCapabilities.from_json(cap_data) if cap_data else None,
            error=data.get("error"),
            fw_version=data.get("fwVer"),
            detergent=data.get("detergent"),
            fr_state=data.get("frState"),
            fw_version_secondary=data.get("fwVerSec"),
            hardware_revision=data.get("hwRev"),
            variant_id=data.get("varId"),
            part_number=data.get("pn"),
            dock_id=data.get("id"),
            tank_lvl=data.get("tankLvl"),
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
    #: ADDED (app 3.0.0, `model/current_state/runtime_stats` declares
    #: hr/min/sqft). Lifetime area cleaned. Absent from every capture
    #: this project holds, which is why it was never modelled -- the
    #: model was built from real payloads, and a key the robot does not
    #: send cannot be discovered that way.
    #:
    #: UNIT NOT CONFIRMED BEYOND THE NAME. Whether a robot in a metric
    #: locale reports square feet under this key or converts is unknown;
    #: nothing here converts it.
    sqft: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RuntimeStatsSummary:
        if not isinstance(data, dict):
            return cls()
        return cls(hours=data.get("hr"), minutes=data.get("min"), sqft=data.get("sqft"))


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
        if not isinstance(data, dict):
            return cls()
        return cls(p2map_id=data.get("p2map_id"), p2mapv_id=data.get("p2mapv_id"))


@dataclass(frozen=True)
class RaasStatus:
    """Two fields confirmed by their own deserializer (parallel
    native-analysis track): enabled + exp. "raas" most plausibly
    stands for Robot-as-a-Service; exp is likely an expiry, but that
    is a reading of the name, not something confirmed."""

    enabled: bool | None = None
    exp: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RaasStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(enabled=data.get("enabled"), exp=data.get("exp"))


@dataclass(frozen=True)
class OdoaLiteStatus:
    """Single confirmed field (enabled), same source. "odoa" is most
    plausibly Obstacle Detection and Avoidance."""

    enabled: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OdoaLiteStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(enabled=data.get("enabled"))


@dataclass(frozen=True)
class TeamingStatus:
    """NEW (app 3.0.0, `model/current_state/teaming_status`) -- two
    robots cleaning one home together.

    `teamId` groups them, `teamingType` says how they divide the work,
    `state` is where the arrangement currently stands, and `nMssn` is a
    team mission counter separate from the robot's own.

    Relevant to any household with two Prime robots, which several of
    this project's testers have."""

    n_missions: int | None = None
    state: Any | None = None
    team_id: Any | None = None
    teaming_type: Any | None = None
    ts: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TeamingStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(
            n_missions=data.get("nMssn"),
            state=data.get("state"),
            team_id=data.get("teamId"),
            teaming_type=data.get("teamingType"),
            ts=data.get("ts"),
        )


@dataclass(frozen=True)
class PmapShareStatus:
    """NEW (app 3.0.0, `model/current_state/pmap_share`) -- whether this
    robot's maps may be copied, shared, or used natively.

    Household map sharing between robots. Note the neighbouring
    properties `pmapCL` and `pmapLearningAllowed` are NOT part of this
    object -- they are separate scalars with no model and no confirmed
    shadow, so they stay unread rather than guessed into this class."""

    copy: Any | None = None
    native: Any | None = None
    share: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PmapShareStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(copy=data.get("copy"), native=data.get("native"), share=data.get("share"))


@dataclass(frozen=True)
class HwDebugStatus:
    """NEW (app 3.0.0, `model/current_state/hw_dbgr`) -- a hardware
    debugger's identity, status and software version.

    Meaning beyond the field names is not established. Modelled because
    the placement is confirmed and an unmodelled key is dropped
    silently; nothing here claims to know what it is for."""

    hw: Any | None = None
    id: Any | None = None
    status: Any | None = None
    sw_version: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HwDebugStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(
            hw=data.get("hw"),
            id=data.get("id"),
            status=data.get("status"),
            sw_version=data.get("swVer"),
        )


@dataclass(frozen=True)
class StreamingVideoStatus:
    """NEW (app 3.0.0, `model/current_state/streaming_video_status`) --
    channel and code for a robot with a camera stream.

    PRIVACY NOTE: this is a status object, not a stream. It says whether
    a channel is open; it carries no image data, and this library has no
    path to any. Neighbouring properties `imgUpload`, `peopleFilter` and
    `privacy` are separate scalars, deliberately left unread -- they
    have no confirmed shadow, and guessing at privacy-relevant fields is
    the wrong place to be approximately right."""

    channel: Any | None = None
    code: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> StreamingVideoStatus:
        if not isinstance(data, dict):
            return cls()
        return cls(channel=data.get("channel"), code=data.get("code"))


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
    # PLACEMENT UNCONFIRMED (this session): both fields are confirmed to
    # EXIST with their own deserializers, but no capture this project
    # has contains either, so which shadow actually carries them is a
    # best guess -- ro-currentstate is the most plausible home for
    # runtime feature flags, but rw-settings and ro-services are
    # equally defensible candidates. Parsing here is harmless if wrong
    # (the field simply stays None) and moving it later is trivial once
    # a real capture settles it.
    raas: RaasStatus | None = None
    odoa_lite: OdoaLiteStatus | None = None
    dock: DockStatus | None = None
    last_disconnect: int | None = None
    p2maps: list[P2MapRef] = field(default_factory=list)
    reg_date: str | None = None
    runtime_stats: RuntimeStatsSummary | None = None
    tank_present: bool | None = None

    # ADDED FROM A REAL CAPTURE (arielgr, sku Y414040). Present in his
    # ro-currentstate and previously dropped -- from_json() reads only
    # declared fields, so an unmodelled key vanishes without any error.
    #
    # Contents not investigated: presumably Google Home / Assistant
    # integration state, from the name alone. Kept as a raw dict rather
    # than guessing at a structure for something nobody has looked at.
    google_control: dict[str, Any] | None = None
    tz: dict[str, Any] | None = None
    svc_endpoints: dict[str, Any] | None = None

    #: ADDED from the vendor's capability gate table, which lists
    #: `dockDetergent` with the key path `detergent` -- top level, NOT
    #: under `dock.cap` where the app's own `_initDockCap` builds it
    #: alongside evac/pd/pw/pwo/fr. Grouped by meaning there, addressed
    #: by path here; the path is what arrives on the wire.
    #:
    #: An INTEGER, like the rest of the capability values: a level, not
    #: a flag. `detergent` is also one of the 24 individually writable
    #: settings, so a robot can report a level here and take a new one
    #: through set_setting("detergent", ...).
    #:
    #: ALSO READ ON DockStatus. `model/current_state/dock_status` lists
    #: `detergent` among the dock's own fields while the gate table puts
    #: it at top level -- two vendor sources, no way here to settle which
    #: the robot sends. Both are read; see DockStatus.detergent.
    detergent: int | None = None
    #: FOUR MORE OBJECTS THE VENDOR PLACES IN THIS SHADOW
    #: (`model/current_state/…`), each previously dropped whole.
    teaming: TeamingStatus | None = None
    pmap_share: PmapShareStatus | None = None
    hw_debugger: HwDebugStatus | None = None
    streaming_video: StreamingVideoStatus | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CurrentStateShadow:
        if not isinstance(data, dict):
            return cls()
        bin_data = data.get("bin")
        mission_data = data.get("cleanMissionStatus")
        dock_data = data.get("dock")
        runtime_data = data.get("runtimeStats")
        return cls(
            bat_pct=data.get("batPct"),
            bin=BinStatus.from_json(bin_data) if bin_data else None,
            clean_mission_status=CleanMissionStatus.from_json(mission_data) if mission_data else None,
            detected_pad=data.get("detectedPad"),
            raas=RaasStatus.from_json(data["raas"]) if isinstance(data.get("raas"), dict) else None,
            odoa_lite=(
                OdoaLiteStatus.from_json(data["odoaLite"])
                if isinstance(data.get("odoaLite"), dict) else None
            ),
            dock=DockStatus.from_json(dock_data) if dock_data else None,
            last_disconnect=data.get("lastDisconnect"),
            p2maps=[P2MapRef.from_json(m) for m in (data.get("p2maps") or [])],
            reg_date=data.get("regDate"),
            runtime_stats=RuntimeStatsSummary.from_json(runtime_data) if runtime_data else None,
            tank_present=data.get("tankPresent"),
            google_control=data.get("googleControl"),
            tz=data.get("tz"),
            svc_endpoints=data.get("svcEndpoints"),
            detergent=data.get("detergent"),
            teaming=(
                TeamingStatus.from_json(data["teaming"])
                if isinstance(data.get("teaming"), dict)
                else None
            ),
            pmap_share=(
                PmapShareStatus.from_json(data["pmapShare"])
                if isinstance(data.get("pmapShare"), dict)
                else None
            ),
            hw_debugger=(
                HwDebugStatus.from_json(data["hwdbgr"])
                if isinstance(data.get("hwdbgr"), dict)
                else None
            ),
            streaming_video=(
                StreamingVideoStatus.from_json(data["streamingVideoStatus"])
                if isinstance(data.get("streamingVideoStatus"), dict)
                else None
            ),
        )


@dataclass(frozen=True)
class BbChgStats:
    """CONFIRMED, REAL VALUES (this session, chairstacker's raw_shadows.json
    capture): lifetime charge-cycle counters. Real values seen: n_chg_ok=561,
    n_chg_err=0 -- plausible for a device registered roughly 10 months
    before the capture.

    THE NESTED "bbchg" KEY (raw_nested below): the real payload has a
    SECOND, same-named sub-object nested one level down
    (state.reported.bbchg.bbchg), holding its OWN nChgOk/nChgErr -- both
    0 in the one real capture seen so far. Purpose unconfirmed; kept raw
    rather than dropped, since a sibling field (BbPauseStats.raw_nested)
    shows this same nested-duplicate shape is NOT always just a zeroed-
    out artifact elsewhere in this shadow -- possibly a "since last
    reset"/session-scoped counter alongside the lifetime total, but
    that's a hypothesis, not a confirmed fact."""

    n_chg_ok: int | None = None
    n_chg_err: int | None = None
    #: SIX MORE FIELDS THE MODEL DECLARES (app 3.0.0,
    #: `model/stats/bbchg`): aborts, chgErr, nChatters, nKnockoffs,
    #: nLithF, smberr. None appears in the one real capture -- the model
    #: was built from that capture, so anything the robot did not send
    #: could not be found this way.
    #:
    #: `chgErr` alongside `nChgErr` is worth noting: a count and
    #: something else, not two spellings. Which is which is not
    #: confirmed; the names suggest the last error code beside the
    #: running total, but nothing here relies on that.
    aborts: int | None = None
    chg_err: int | None = None
    n_chatters: int | None = None
    n_knockoffs: int | None = None
    n_lith_f: int | None = None
    smberr: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbChgStats:
        if not isinstance(data, dict):
            return cls()
        return cls(
            n_chg_ok=data.get("nChgOk"),
            n_chg_err=data.get("nChgErr"),
            aborts=data.get("aborts"),
            chg_err=data.get("chgErr"),
            n_chatters=data.get("nChatters"),
            n_knockoffs=data.get("nKnockoffs"),
            n_lith_f=data.get("nLithF"),
            smberr=data.get("smberr"),
            raw_nested=data.get("bbchg"),
        )


@dataclass(frozen=True)
class BbChg3Stats:
    """CONFIRMED, REAL VALUES (this session, chairstacker): n_avail=285,
    hours_on_dock=293109 -- units unconfirmed (if hours, an implausibly
    large number for a ~10-month-old device; more likely a different,
    finer-grained tick unit, or a counter that isn't literally "hours").

    est_capacity/avg_minutes/n_lith_chrg/n_nimh_chrg are NOT present at
    all in the one real capture seen (not null -- entirely absent keys)
    -- included here anyway per this class's own cross-reference finding
    (see StatsShadow's docstring): Classic's own "bbchg3" carries these
    same field names, and Classic's own docs note this exact field can be
    ABSENT ENTIRELY on some real robots (firmware/model-dependent). Kept
    as optional fields for whichever future capture (different firmware/
    SKU) might show them, rather than omitted and rediscovered later."""

    n_avail: int | None = None
    hours_on_dock: int | None = None
    est_capacity: int | None = None
    avg_minutes: int | None = None
    n_lith_chrg: int | None = None
    n_nimh_chrg: int | None = None
    #: ADDED (app 3.0.0, `model/stats/bbchg3`). Dock count, the seventh
    #: field this model declares and the only one that was missing
    #: outright rather than merely absent from the capture.
    n_docks: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbChg3Stats:
        if not isinstance(data, dict):
            return cls()
        return cls(
            n_avail=data.get("nAvail"),
            hours_on_dock=data.get("hOnDock"),
            est_capacity=data.get("estCap"),
            avg_minutes=data.get("avgMin"),
            n_lith_chrg=data.get("nLithChrg"),
            n_nimh_chrg=data.get("nNimhChrg"),
            n_docks=data.get("nDocks"),
            raw_nested=data.get("bbchg3"),
        )


@dataclass(frozen=True)
class BbMssnStats:
    """CONFIRMED, REAL VALUES (this session, chairstacker): lifetime
    mission-outcome counters, and a strong internal-consistency check
    that these are exactly what they appear to be: n_mssn_canceled (25)
    + n_mssn_failed (4) + n_mssn_ok (247) = 276 = n_mssn, an exact match.
    n_mssn (276) also matches ro-currentstate's cleanMissionStatus.nMssn
    from the SAME capture -- cross-shadow consistency, not just internal."""

    n_mssn: int | None = None
    n_mssn_canceled: int | None = None
    n_mssn_failed: int | None = None
    n_mssn_ok: int | None = None
    #: ADDED (app 3.0.0, `model/stats/bbmssn`). Average cycle and
    #: average mission length in minutes -- the two non-counter fields
    #: in an otherwise all-counter model, which is why the sum check
    #: above still holds without them.
    avg_cycle_minutes: int | None = None
    avg_mission_minutes: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbMssnStats:
        if not isinstance(data, dict):
            return cls()
        return cls(
            n_mssn=data.get("nMssn"),
            n_mssn_canceled=data.get("nMssnC"),
            n_mssn_failed=data.get("nMssnF"),
            n_mssn_ok=data.get("nMssnOk"),
            avg_cycle_minutes=data.get("aCycleM"),
            avg_mission_minutes=data.get("aMssnM"),
            raw_nested=data.get("bbmssn"),
        )


@dataclass(frozen=True)
class BbPauseStats:
    """CONFIRMED, REAL VALUES (this session, chairstacker): a plain list
    of ints, real value seen [1, 48, 48, 48, 48, 48, 48, 48, 48, 48] (10
    entries) -- likely a histogram/duration-bucket array (10 buckets?)
    rather than one entry per pause event; exact semantics unconfirmed.

    Unlike BbChgStats/BbChg3Stats/BbMssnStats, this one's raw_nested
    (bbpause.bbpause) is NOT all-zero in the real capture ([29, -1]) --
    the clearest evidence so far that this nested duplicate shape isn't
    simply a zeroed-out artifact everywhere in this shadow, whatever it
    actually represents."""

    pauses: list[int] = field(default_factory=list)
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbPauseStats:
        if not isinstance(data, dict):
            return cls()
        pauses = data.get("pauses")
        return cls(
            pauses=list(pauses) if isinstance(pauses, list) else [],
            raw_nested=data.get("bbpause"),
        )


@dataclass(frozen=True)
class BbRstInfoStats:
    """CONFIRMED, REAL VALUE (this session, chairstacker): n_nav_rst=22
    (navigation resets, lifetime). n_mob_rst/n_saf_rst/saf_causes are NOT
    present at all in the one real capture seen -- kept as optional
    fields on the same cross-reference basis as BbChg3Stats's absent
    fields (Classic's own "bbrstinfo" has these same names; presence may
    be firmware/model-dependent, not confirmed absent on every device)."""

    n_nav_rst: int | None = None
    n_mob_rst: int | None = None
    n_saf_rst: int | None = None
    saf_causes: Any | None = None
    #: ADDED (app 3.0.0, `model/stats/bbrstinfo`). `causes` sits beside
    #: the existing `safCauses`, and `nMapLoadRst`/`nOomRst` name two
    #: reset kinds the model had no field for: map-load failure and
    #: out-of-memory.
    #:
    #: An OOM reset counter is the interesting one -- it distinguishes
    #: "the robot rebooted" from "the robot ran out of memory and
    #: rebooted", which no other field here separates.
    causes: Any | None = None
    n_map_load_rst: int | None = None
    n_oom_rst: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbRstInfoStats:
        if not isinstance(data, dict):
            return cls()
        return cls(
            n_nav_rst=data.get("nNavRst"),
            n_mob_rst=data.get("nMobRst"),
            n_saf_rst=data.get("nSafRst"),
            saf_causes=data.get("safCauses"),
            causes=data.get("causes"),
            n_map_load_rst=data.get("nMapLoadRst"),
            n_oom_rst=data.get("nOomRst"),
            raw_nested=data.get("bbrstinfo"),
        )


@dataclass(frozen=True)
class BbSysStats:
    """POWERED-ON HOURS, not time since registration. Confirmed by two
    field accounts with very different usage.

    | account      | wall-clock | reported | gap        | robot was |
    |--------------|-----------:|---------:|-----------:|-----------|
    | chairstacker |     7368 h |   7354 h |     14 h   | rarely off |
    | DaRealGuGu   |     9672 h |   4093 h |   5579 h   | off for months |

    The hypothesis came from chairstacker, who could not test it since
    his robot is rarely off; DaRealGuGu's account provided the other
    end of the range. Both gaps match what each owner recalls of their
    own downtime -- and if this counted wall-clock time, both gaps
    would have to be near zero.

    An earlier version of this note called chairstacker's 14 hours
    "close enough to be believable" and stopped there. That was lazy:
    14 hours is not rounding, it was a specific unexplained quantity,
    and treating it as noise cost the finding a round.

    PRACTICAL CONSEQUENCE: do NOT present this to users as "time since
    you got the robot". It is an operating-hours meter, and on a robot
    that has spent months unplugged the two differ by more than half.
    """

    hours: int | None = None
    minutes: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbSysStats:
        if not isinstance(data, dict):
            return cls()
        return cls(hours=data.get("hr"), minutes=data.get("min"), raw_nested=data.get("bbsys"))


@dataclass(frozen=True)
class BbRunStats:
    """NEW (app 3.0.0, `model/stats/bbrun`) -- sixteen lifetime fault
    and event counters, none of which this library could read before.

    The largest of the blackbox models and the one closest to a wear
    report: stalls per motor (main brush, side brush, drive, wheel),
    cliff sensor triggers front and rear, pickups, slips, overtemps,
    panics, and two dirt-detect counters (`nOpticalDD`, `nPiezoDD`)
    that distinguish the two sensor kinds.

    NOT FROM A CAPTURE. Unlike the models above, no real payload this
    project holds has ever carried `bbrun` -- this comes from the app's
    own declaration. The field names are the vendor's; what each one
    counts is read off the name and not confirmed.

    THE ONE CROSS-CHECK AVAILABLE: ha_roomba_plus reads Classic-tier
    `bbrun` already and its field vocabulary matches (see
    MISSIONSTORE_FIELD_REGISTRY.md) -- the same blackbox subsystem over
    a different transport, which is the pattern `bbchg3` and
    `bbrstinfo` already showed."""

    n_c_bump: int | None = None
    n_cliffs_f: int | None = None
    n_cliffs_r: int | None = None
    n_d_stll: int | None = None
    n_lb_stll: int | None = None
    n_mb_stll: int | None = None
    n_optical_dd: int | None = None
    n_overtemps: int | None = None
    n_panics: int | None = None
    n_picks: int | None = None
    n_piezo_dd: int | None = None
    n_rb_stll: int | None = None
    n_scrubs: int | None = None
    n_slips: int | None = None
    n_stuck: int | None = None
    n_w_stll: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbRunStats:
        if not isinstance(data, dict):
            return cls()
        return cls(
            n_c_bump=data.get("nCBump"),
            n_cliffs_f=data.get("nCliffsF"),
            n_cliffs_r=data.get("nCliffsR"),
            n_d_stll=data.get("nDStll"),
            n_lb_stll=data.get("nLBStll"),
            n_mb_stll=data.get("nMBStll"),
            n_optical_dd=data.get("nOpticalDD"),
            n_overtemps=data.get("nOvertemps"),
            n_panics=data.get("nPanics"),
            n_picks=data.get("nPicks"),
            n_piezo_dd=data.get("nPiezoDD"),
            n_rb_stll=data.get("nRBStll"),
            n_scrubs=data.get("nScrubs"),
            n_slips=data.get("nSlips"),
            n_stuck=data.get("nStuck"),
            n_w_stll=data.get("nWStll"),
            raw_nested=data.get("bbrun"),
        )


@dataclass(frozen=True)
class BbSwitchStats:
    """NEW (app 3.0.0, `model/stats/bbswitch`) -- eight lifetime button
    and switch counters: Clean, Dock and Spot presses, bumper hits, lid
    openings, lifts, drops, and a generic key count.

    NOT FROM A CAPTURE; see BbRunStats. `nBumper` here and `nCBump` in
    `bbrun` both look like bumper counts and are not confirmed to mean
    the same thing -- they belong to different models, so nothing here
    treats them as interchangeable."""

    n_bumper: int | None = None
    n_clean: int | None = None
    n_dock: int | None = None
    n_drops: int | None = None
    n_key: int | None = None
    n_lid: int | None = None
    n_lifts: int | None = None
    n_spot: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbSwitchStats:
        if not isinstance(data, dict):
            return cls()
        return cls(
            n_bumper=data.get("nBumper"),
            n_clean=data.get("nClean"),
            n_dock=data.get("nDock"),
            n_drops=data.get("nDrops"),
            n_key=data.get("nKey"),
            n_lid=data.get("nLid"),
            n_lifts=data.get("nLifts"),
            n_spot=data.get("nSpot"),
            raw_nested=data.get("bbswitch"),
        )


@dataclass(frozen=True)
class BbNavStats:
    """NEW (app 3.0.0, `model/stats/bbnav`) -- four navigation figures:
    average camera exposure and gain, motion-track quality, and a count
    of good landmarks.

    NOT FROM A CAPTURE; see BbRunStats. These are VSLAM health
    indicators rather than user-facing counts -- `nGoodLmrks` in
    particular says whether the robot can still localise, not how much
    it has cleaned."""

    a_expo: int | None = None
    a_gain: int | None = None
    a_mtrack: int | None = None
    n_good_lmrks: int | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbNavStats:
        if not isinstance(data, dict):
            return cls()
        return cls(
            a_expo=data.get("aExpo"),
            a_gain=data.get("aGain"),
            a_mtrack=data.get("aMtrack"),
            n_good_lmrks=data.get("nGoodLmrks"),
            raw_nested=data.get("bbnav"),
        )


@dataclass(frozen=True)
class BbPanicStats:
    """NEW (app 3.0.0, `model/stats/bbpanic`) -- a single `panics`
    field.

    SHAPE DELIBERATELY PERMISSIVE. `bbpause` is the only structurally
    comparable model with a real capture behind it, and its single
    field turned out to carry a LIST, not a count -- so assuming an int
    here would be assuming the opposite of the one nearby precedent.
    `bbrun.nPanics` already provides a count if a count is wanted."""

    panics: Any | None = None
    raw_nested: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BbPanicStats:
        if not isinstance(data, dict):
            return cls()
        return cls(panics=data.get("panics"), raw_nested=data.get("bbpanic"))


@dataclass(frozen=True)
class MssnNavStats:
    """NEW (app 3.0.0, `model/stats/mssn_nav_stats`) -- seventeen
    per-mission navigation diagnostics, and the only model here scoped
    to a single mission rather than the robot's lifetime: it carries
    its own `missionId`.

    NOT FROM A CAPTURE; see BbRunStats. Most names do not resolve from
    the name alone -- `kdp`, `sfkdp`, `nmc`, `nmmc`, `nrmc`, `reLc`,
    `mpSt` are recorded as the vendor spells them rather than guessed
    at. `h_drift`/`l_drift` and `h_squal`/`l_squal` pair high and low
    variants of drift and signal quality; `lmk`/`gLmk` look like
    landmark counts beside `bbnav.nGoodLmrks`.

    THE CASING IS AMBIGUOUS, so both spellings are read.

    An earlier note here claimed the mix of snake_case (`h_drift`,
    `l_squal`) and camelCase (`plnErr`, `missionId`) was "not a
    transcription slip -- the vendor's own field list does". The vendor's
    field list does, but that proves less than it looks:
    `message_center_models.dart` carries 53 fields in BOTH spellings,
    camelCase for the Dart property and snake_case for the wire. Mixed
    casing in one model is that pairing maintained incompletely.

    Where the pairing means anything, SNAKE_CASE IS THE WIRE FORM. That
    would make `plnErr` a Dart name whose wire key is `pln_err`. Nothing
    here confirms it either way, and no capture contains this object at
    all -- so each field is read under both spellings. Two dict lookups
    against a permanent None."""

    mission_id: str | None = None
    n_mssn: int | None = None
    g_lmk: Any | None = None
    lmk: Any | None = None
    h_drift: Any | None = None
    l_drift: Any | None = None
    h_squal: Any | None = None
    l_squal: Any | None = None
    kdp: Any | None = None
    sfkdp: Any | None = None
    m_trk: Any | None = None
    mp_st: Any | None = None
    nmc: Any | None = None
    nmmc: Any | None = None
    nrmc: Any | None = None
    pln_err: Any | None = None
    re_lc: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MssnNavStats:
        if not isinstance(data, dict):
            return cls()

        def either(camel: str, snake: str) -> Any:
            value = data.get(camel)
            return data.get(snake) if value is None else value

        return cls(
            mission_id=either("missionId", "mission_id"),
            n_mssn=either("nMssn", "n_mssn"),
            g_lmk=either("gLmk", "g_lmk"),
            lmk=data.get("lmk"),
            h_drift=either("hDrift", "h_drift"),
            l_drift=either("lDrift", "l_drift"),
            h_squal=either("hSqual", "h_squal"),
            l_squal=either("lSqual", "l_squal"),
            kdp=data.get("kdp"),
            sfkdp=data.get("sfkdp"),
            m_trk=either("mTrk", "m_trk"),
            mp_st=either("mpSt", "mp_st"),
            nmc=data.get("nmc"),
            nmmc=data.get("nmmc"),
            nrmc=data.get("nrmc"),
            pln_err=either("plnErr", "pln_err"),
            re_lc=either("reLc", "re_lc"),
        )


@dataclass(frozen=True)
class StatsShadow:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (this session,
    chairstacker's raw_shadows.json capture) -- complete key list AND
    real values of the named "ro-stats" shadow, the second of the four
    previously-unknown read-only shadows found via MQTTTopics.java.
    Previously only key names were confirmed (every field typed `Any`);
    see each bbX sub-class's own docstring for its specific real values
    and the cross-validation that confirms they're genuinely lifetime
    statistics, not arbitrary numbers (BbMssnStats's counters sum
    exactly; BbSysStats's hour count matches the device's registration
    age).

    The "bb" prefix (bbchg, bbchg3, bbmssn, bbpause, bbrstinfo, bbsys) is
    still unconfirmed by name, but "battery box"/blackbox-style lifetime
    telemetry is a good fit for what's actually in each one now that real
    values exist. CROSS-REFERENCE (still holds): "bbchg3" and
    "bbrstinfo" both exist with the same field vocabulary on Classic
    robots too (MISSIONSTORE_FIELD_REGISTRY.md) -- same underlying
    blackbox telemetry subsystem, relayed over a different transport.

    "unprocessedError" is a plain STRING (not an object as its name
    might suggest) -- real value seen: "picea unknown fault code:2105".
    Notable: "picea" appearing in a real backend string, consistent with
    the Shenzhen Picea Robotics acquisition (Jan 2026) already reflected
    in this project's own context notes.

    Note "runtimestats" here is ALL-LOWERCASE, unlike ro-currentstate's
    camelCase "runtimeStats" -- confirmed as two separate keys with
    different casing (not a transcription error). Reuses
    RuntimeStatsSummary (same hr/min shape, confirmed identical in the
    real capture: {"hr": 7, "min": 57})."""

    bbchg: BbChgStats | None = None
    bbchg3: BbChg3Stats | None = None
    bbmssn: BbMssnStats | None = None
    bbpause: BbPauseStats | None = None
    bbrstinfo: BbRstInfoStats | None = None
    bbsys: BbSysStats | None = None
    #: FIVE MORE THE VENDOR DECLARES AND THIS SHADOW NEVER READ.
    #:
    #: The six above were modelled from a real capture, so the list
    #: stopped where that capture stopped. App 3.0.0 declares five more
    #: under `model/stats/`: bbrun, bbswitch, bbnav, bbpanic and
    #: mssn_nav_stats.
    #:
    #: None has ever appeared in a payload this project holds, which is
    #: exactly why they were missing -- and also why nothing here claims
    #: they will appear. If a robot sends them, they are now read
    #: instead of dropped; if it does not, these stay None as before.
    bbrun: BbRunStats | None = None
    bbswitch: BbSwitchStats | None = None
    bbnav: BbNavStats | None = None
    bbpanic: BbPanicStats | None = None
    mssn_nav_stats: MssnNavStats | None = None
    runtimestats: RuntimeStatsSummary | None = None
    #: A FAULT THE ROBOT ITSELF COULD NOT NAME.
    #:
    #: @jouwdan's Max 705 reported
    #: `"picea unknown fault code:2105"` here while
    #: `cleanMissionStatus.error` read 0 -- so the mission status was
    #: clean and the stats shadow was not.
    #:
    #: Two things worth knowing. **"picea" is the app shell's own
    #: vendor name**, not iRobot's, which places this string in the
    #: platform layer rather than the robot's. And 2105 appears in
    #: neither iRobot's 112-code catalogue nor ours -- the robot is
    #: relaying a fault its own software stack could not resolve.
    #:
    #: Surfaced as the string it is. There is nothing to look up, and
    #: turning it into None would hide the one place it appears.
    unprocessed_error: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> StatsShadow:
        if not isinstance(data, dict):
            return cls()
        return cls(
            bbchg=BbChgStats.from_json(data["bbchg"]) if isinstance(data.get("bbchg"), dict) else None,
            bbchg3=BbChg3Stats.from_json(data["bbchg3"]) if isinstance(data.get("bbchg3"), dict) else None,
            bbmssn=BbMssnStats.from_json(data["bbmssn"]) if isinstance(data.get("bbmssn"), dict) else None,
            bbpause=BbPauseStats.from_json(data["bbpause"]) if isinstance(data.get("bbpause"), dict) else None,
            bbrstinfo=BbRstInfoStats.from_json(data["bbrstinfo"]) if isinstance(data.get("bbrstinfo"), dict) else None,
            bbsys=BbSysStats.from_json(data["bbsys"]) if isinstance(data.get("bbsys"), dict) else None,
            bbrun=BbRunStats.from_json(data["bbrun"]) if isinstance(data.get("bbrun"), dict) else None,
            bbswitch=(
                BbSwitchStats.from_json(data["bbswitch"])
                if isinstance(data.get("bbswitch"), dict)
                else None
            ),
            bbnav=BbNavStats.from_json(data["bbnav"]) if isinstance(data.get("bbnav"), dict) else None,
            bbpanic=(
                BbPanicStats.from_json(data["bbpanic"])
                if isinstance(data.get("bbpanic"), dict)
                else None
            ),
            mssn_nav_stats=(
                MssnNavStats.from_json(data["mssnNavStats"])
                if isinstance(data.get("mssnNavStats"), dict)
                else None
            ),
            runtimestats=(
                RuntimeStatsSummary.from_json(data["runtimestats"])
                if isinstance(data.get("runtimestats"), dict)
                else None
            ),
            unprocessed_error=data.get("unprocessedError"),
        )


@dataclass(frozen=True)
class ServicesShadow:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUE (this session,
    chairstacker) -- complete key list AND real value of the named
    "ro-services" shadow, the third of the four previously-unknown
    read-only shadows found via MQTTTopics.java.

    "optFeats" is an OBJECT (a prior working assumption that it might be
    a plain list was WRONG), mapping feature name -> int. Real value
    seen: {"carpetBoost": 0} -- exactly one entry in this capture,
    unclear whether 0 means "not an optional/paid feature for this
    device" or "feature disabled"/some other flag meaning. Kept as a
    raw dict rather than a specific dataclass since only one key has
    ever been seen -- not enough evidence yet for a typed shape."""

    opt_feats: dict[str, Any] | None = None
    #: ADDED (app 3.0.0, `model/services/smart_home`, which names this
    #: shadow). One field: `homeMonitoringAllowed`.
    #:
    #: A permission flag, and the only one of the nine third-party
    #: control properties (`alexaControl`, `siriControl`,
    #: `iftttControl`, `privacy` and the rest) whose shadow the vendor
    #: states. The other eight stay unread rather than filed here by
    #: association.
    home_monitoring_allowed: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ServicesShadow:
        if not isinstance(data, dict):
            return cls()
        opt_feats = data.get("optFeats")
        smart_home = data.get("smartHome")
        return cls(
            opt_feats=opt_feats if isinstance(opt_feats, dict) else None,
            home_monitoring_allowed=(
                smart_home.get("homeMonitoringAllowed")
                if isinstance(smart_home, dict)
                else None
            ),
        )


@dataclass(frozen=True)
class HwPartsRev:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (this session,
    chairstacker) -- the actual content of ro-configinfo's "hwPartsRev"
    object. Real capture: every string field empty ("") except
    nav_serial_no, which holds a real serial number
    ("G185020H250311N105749" -- matches the device's own SKU prefix,
    G185020). mob_board is an int, =0 in the real capture."""

    aoa_serial_no: str | None = None
    #: ADDED (app 3.0.0, `model/configinfo/hw_parts_rev`, the tenth field
    #: this model declares). Absent from the one real capture, like most
    #: of its neighbours, which is why it was never noticed missing.
    cssc_id: str | None = None
    fan: str | None = None
    imu_part_no: str | None = None
    lr_drv: str | None = None
    mob_blid: str | None = None
    mob_board: int | None = None
    nav_serial_no: str | None = None
    ui: str | None = None
    wlan0_hw_addr: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HwPartsRev:
        if not isinstance(data, dict):
            return cls()
        return cls(
            aoa_serial_no=data.get("aoaSerialNo"),
            cssc_id=data.get("csscID"),
            fan=data.get("fan"),
            imu_part_no=data.get("imuPartNo"),
            lr_drv=data.get("lrDrv"),
            mob_blid=data.get("mobBlid"),
            mob_board=data.get("mobBrd"),
            nav_serial_no=data.get("navSerialNo"),
            ui=data.get("ui"),
            wlan0_hw_addr=data.get("wlan0HwAddr"),
        )


@dataclass(frozen=True)
class MiraSwVersion:
    """NEW (app 3.0.0, `model/configinfo/mira_sw_ver`) -- a two-part
    software version: `release` and `spec`."""

    release: Any | None = None
    spec: Any | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MiraSwVersion:
        if not isinstance(data, dict):
            return cls()
        return cls(release=data.get("release"), spec=data.get("spec"))


@dataclass(frozen=True)
class BatInfo:
    """NEW (app 3.0.0, `model/configinfo/bat_info`) -- the battery's
    manufacturer details and wear counters.

        mName        manufacturer name
        mDate        manufacture date
        mDaySerial   day serial from the same batch
        mData        manufacturer data blob
        mLife        rated life
        cCount       charge cycles
        afCount      unexplained; the vendor's name, kept as it is

    Types are deliberately permissive: no capture this project holds
    contains this object, so whether `mDate` is a string, a timestamp or
    something else is unknown. Typing it `str` because the name says
    "date" would be guessing at the very thing that is unverified."""

    m_name: Any | None = None
    m_date: Any | None = None
    m_day_serial: Any | None = None
    m_data: Any | None = None
    m_life: Any | None = None
    c_count: int | None = None
    af_count: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BatInfo:
        if not isinstance(data, dict):
            return cls()
        return cls(
            m_name=data.get("mName"),
            m_date=data.get("mDate"),
            m_day_serial=data.get("mDaySerial"),
            m_data=data.get("mData"),
            m_life=data.get("mLife"),
            c_count=data.get("cCount"),
            af_count=data.get("afCount"),
        )


@dataclass(frozen=True)
class ConfigInfoShadow:
    """CONFIRMED LIVE, STRUCTURE AND REAL VALUES (this session,
    chairstacker) -- complete key list AND real values of the named
    "ro-configinfo" shadow, the last of the four previously-unknown
    read-only shadows found via MQTTTopics.java.

    "passwordHash" -- PRIVACY NOTE: confirmed as a plain STRING in the
    real capture (correctly shown as "[REDACTED]" by diagnostics.py's
    own redaction layer before the capture was ever shared with this
    project -- redaction confirmed working as intended, not just
    theoretical). Sensitive regardless of being a hash rather than
    plaintext; this model itself still does no redaction of its own
    (that stays diagnostics.py's job, see Report.redact()) -- flagged
    here so anyone handling this shadow's real content directly is
    aware, not just relying on downstream redaction to catch it."""

    hw_parts_rev: HwPartsRev | None = None
    password_hash: str | None = None
    #: NEW: the battery's own identity and wear record.
    #:
    #: PLACEMENT IS CONFIRMED, not guessed -- the vendor's model path is
    #: `model/configinfo/bat_info`, which names this shadow. That
    #: mattered: this library has one field elsewhere marked "PLACEMENT
    #: UNCONFIRMED", and a field parsed from the wrong shadow stays None
    #: forever without any error to notice.
    #:
    #: `cCount` is the charge-cycle count and `mLife` the manufacturer's
    #: rated life, which together are the closest thing here to a battery
    #: health figure -- relevant to any robot old enough to have had its
    #: battery replaced, aftermarket or otherwise.
    bat_info: BatInfo | None = None
    #: ADDED (app 3.0.0, `model/configinfo/mira_sw_ver`, which names this
    #: shadow). Two fields, `release` and `spec` -- a version pair rather
    #: than a single string, so a caller comparing firmware versions has
    #: two things to compare.
    #:
    #: `mira` is unexplained. One of nine `*Ver`/`*SwVer` properties in
    #: the registry (`navSwVer`, `uiSwVer`, `wifiSwVer`, `umiVer`,
    #: `mobilityVer` and the rest); this is the only one whose shadow the
    #: vendor states, so it is the only one added.
    mira_sw_version: MiraSwVersion | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ConfigInfoShadow:
        if not isinstance(data, dict):
            return cls()
        hw_parts_rev = data.get("hwPartsRev")
        return cls(
            hw_parts_rev=HwPartsRev.from_json(hw_parts_rev) if isinstance(hw_parts_rev, dict) else None,
            password_hash=data.get("passwordHash"),
            bat_info=BatInfo.from_json(data["batInfo"]) if isinstance(data.get("batInfo"), dict) else None,
            mira_sw_version=(
                MiraSwVersion.from_json(data["miraSwVer"])
                if isinstance(data.get("miraSwVer"), dict)
                else None
            ),
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#robot_inforobotstatusv2
    """

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
        if not isinstance(data, dict):
            return cls()
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




@dataclass(frozen=True)
class FirmwareItem:
    """A firmware release for a robot, from `GET /v2/firmware`.

    THIS ANSWERS A QUESTION THE SHADOW CANNOT. `softwareVer` says what
    is installed; nothing says what is available, what it changes, or
    how long installing it takes. The app shows all three.

    `expected_installation_time` is the one that matters in a home:
    somebody deciding whether to start an update at nine in the evening
    can be told rather than left to find out.

    Wire keys verified from `FirmwareItemDto`'s `$$serializer` in app
    3.0.0 (build 3000008). Untested against the live endpoint -- the
    request model gives no method, so even the verb is unconfirmed.
    """

    version: str | None = None
    sku: str | None = None
    #: A LIST, NOT A STRING. `FirmwareItemDto` types it
    #: `List<String>` -- one release can target several installed
    #: versions, which is what an upgrade path looks like.
    #:
    #: Modelled as a string when this class was written today, from the
    #: wire-key list alone. The key names said nothing about types; the
    #: model dump does.
    target_software_ver: list[str] | None = None
    notes: str | None = None
    release_date: str | None = None
    download_url: str | None = None
    metapackage_url: str | None = None
    deployment_mpkg: str | None = None
    track: str | None = None
    provisioning_priority: int | None = None
    ota_priority: int | None = None
    signing: str | None = None
    fused: bool | None = None
    expected_download_time: int | None = None
    expected_installation_time: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FirmwareItem:
        if not isinstance(data, dict):
            return cls()
        return cls(
            version=data.get("version"),
            sku=data.get("sku"),
            target_software_ver=(
                list(raw)
                if isinstance(raw := data.get("targetSoftwareVer"), list)
                # A single string is accepted rather than dropped: no
                # response has been seen, and a firmware entry that
                # names one target is not obviously wrong.
                else [raw] if isinstance(raw, str)
                else None
            ),
            notes=data.get("notes"),
            release_date=data.get("releaseDate"),
            download_url=data.get("downloadUrl"),
            metapackage_url=data.get("metapackageUrl"),
            deployment_mpkg=data.get("deploymentMpkg"),
            track=data.get("track"),
            provisioning_priority=data.get("provisioningPriority"),
            ota_priority=data.get("otaPriority"),
            signing=data.get("signing"),
            fused=data.get("fused"),
            expected_download_time=data.get("expectedDownloadTime"),
            expected_installation_time=data.get("expectedInstallationTime"),
        )


@dataclass(frozen=True)
class DockFirmware:
    """A dock firmware release. Five fields where the robot has fifteen
    -- no notes, no download url, no sku. `DockFirmwareDto`.
    """

    version: str | None = None
    provisioning_priority: int | None = None
    ota_priority: int | None = None
    track: str | None = None
    expected_installation_time: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DockFirmware:
        if not isinstance(data, dict):
            return cls()
        return cls(
            version=data.get("version"),
            provisioning_priority=data.get("provisioningPriority"),
            ota_priority=data.get("otaPriority"),
            track=data.get("track"),
            expected_installation_time=data.get("expectedInstallationTime"),
        )
