"""Mission history response models, including all 20 MissionTimelineEvent sub-event types.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .enums_common import _enum_or_none
from .mission_control import CommandParams, Region


class DoneCode(StrEnum):
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


class PadCategory(StrEnum):
    """Confirmed (androguard): 7 values."""

    DRY = "DRY"
    INVALID = "INVALID"
    NO_PAD = "NO_PAD"
    PLATE = "PLATE"
    REUSABLE_DRY = "REUSABLE_DRY"
    REUSABLE_WET = "REUSABLE_WET"
    WET = "WET"


class RankOverlap(StrEnum):
    """Confirmed (androguard): 3 values."""

    DEEP_CLEAN = "DEEP_CLEAN"
    DETAIL_CLEAN = "DETAIL_CLEAN"
    EXTENDED_CLEAN = "EXTENDED_CLEAN"


class CoverageStrategy(StrEnum):
    """Confirmed (androguard): 3 values."""

    HYBRID_COVERAGE_PLANNER = "HYBRID_COVERAGE_PLANNER"
    RESERVED = "RESERVED"
    ROOM_SEGMENTATION = "ROOM_SEGMENTATION"


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
    regions: list[Region] = field(default_factory=list)
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
    timeline: list[MissionTimelineEvent] = field(default_factory=list)
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


class PlanType(StrEnum):
    """Confirmed (androguard, PlanEvent.type): 3 values."""

    ALL = "ALL"
    DRC = "DRC"
    TRAIN = "TRAIN"


class PlanUpcoming(StrEnum):
    """Confirmed (androguard, PlanEvent.upcoming list elements): 4 values."""

    POLY = "POLY"
    RID = "RID"
    WID = "WID"
    ZID = "ZID"


class TravelDestination(StrEnum):
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


class TraversalType(StrEnum):
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
    these left unchanged.

    HYPOTHESIS, not confirmed (this session, chairstacker, an
    interrupted mid-cleaning mission): area appears to be the room's
    total/target size (354 in every capture of this same room,
    unchanged whether the room was fully cleaned or barely started),
    while total_area appears to be how much was ACTUALLY covered this
    visit (0, observed on a room event finished immediately after
    send_simple_command("stop") interrupted the mission before real
    coverage happened). Only two data points support this reading, one
    of them a zero -- treat as a plausible interpretation, not a
    settled one.

    Also a hypothesis, same caveat: status=0 was observed on a
    normally-superseded travel event, status=5 on this same
    interrupted room event -- consistent with 0 meaning something like
    "completed normally" and a nonzero value flagging some kind of
    interruption, but again only two data points, no enum confirmed."""

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


@dataclass(frozen=True)
class MissionTimelineReport:
    """CONFIRMED LIVE (this session, chairstacker -- a real, active
    mission, via prime_robot.py's watch_mission_timeline()). The actual
    message shape arriving on mission/timeline/report.

    A valuable cross-confirmation neither investigation alone
    established: this wraps the SAME MissionTimelineEvent model already
    confirmed (session 18/31, via androguard/jadx static analysis) for
    get_mission_history()'s HISTORICAL timeline data -- the live push
    channel and the historical pull endpoint evidently share one
    underlying event schema. RoomEvent/TravelEvent/TentativeLocationEvent
    (room/travel/reloc) all matched the live capture's fields exactly,
    with zero corrections needed.

    event: in every live message captured so far, ALWAYS exactly one
    entry -- the newest/current event. fin_events: a growing list of
    PAST events, each gaining an end_time (MissionTimelineEvent's own
    "ets" field) once superseded by the next one -- effectively a
    running history of the mission-so-far, resent in full on every
    single update rather than delta-only.

    command/initiator/command_time: NOT new data -- this is the SAME
    payload send_simple_command() itself publishes (see
    mqtt_client.py's publish_cmd()), echoed back here as context for
    which command's mission this report belongs to.

    n_missions ("nMssn" on the wire): meaning still not directly
    confirmed (a lifetime mission counter remains the most plausible
    guess), but one earlier hypothesis is now DISPROVEN: a second live
    capture (chairstacker, same session as this class's original
    confirmation) showed 256 where the first had shown 255 -- ruling
    out "a saturating counter capped at the max value of an unsigned
    8-bit integer" as an explanation, since 256 exceeds that range. A
    genuine incrementing counter (whether lifetime missions or
    something else that increments once per mission) is now the better-
    supported reading.

    timelineRequestId (optional, observed on some but not all live
    report messages, chairstacker): appears tied to an explicit
    client-side request for a fresh timeline update -- also observed as
    its own bare {"timelineRequestId": N} message on the wildcard
    channel, separate from any mission/timeline/report envelope.
    Mechanism not further investigated; stored as an opaque int when
    present.

    mission_id ("01KXXQM8XZEDJ24701JF121CCH" observed): CONFIRMED as a
    real ULID (Universally Unique Lexicographically Sortable
    Identifier), not just a plausible shape match -- rigorously
    verified against BOTH mission_ids seen across two live captures:
    26 characters, every character in the Crockford base32 alphabet
    (which deliberately excludes I/L/O/U -- neither mission_id
    contains any of those four), first character in the valid 0-7
    range a ULID's 48-bit millisecond timestamp requires. Beyond the
    shape: the timestamp actually ENCODED in the first 10 characters
    was decoded directly (standard ULID timestamp decoding, Crockford
    base32) and compared against this same report's own cmd.time (the
    real Unix timestamp of the "start" command that began the
    mission) -- 0.0s and 3.6s apart on the two captures respectively.
    This is not a coincidental format match; the ULID's own embedded
    timestamp genuinely corresponds to when the mission it identifies
    actually began.

    map_version fields observed on nested events (RoomEvent.map_version
    etc., e.g. "260719T174414.994"): decodes cleanly as YYMMDD"T"HHMMSS.mmm
    -- confirmed against two independent real captures (this session's
    "260719T174353.832" = 2026-07-19 17:43:53.832, matching the actual
    capture date; and an existing test fixture's "260715T130113.944" =
    2026-07-15 13:01:13.944). Each event in a single live capture had a
    DIFFERENT map_version despite sharing the same map_id -- suggesting
    this is a per-localization-update timestamp, not a "map was edited"
    version the way the name might suggest.
    """

    command: str | None = None
    initiator: str | None = None
    command_time: int | None = None
    event: list[MissionTimelineEvent] = field(default_factory=list)
    fin_events: list[MissionTimelineEvent] = field(default_factory=list)
    mission_id: str | None = None
    n_missions: int | None = None
    version: str | None = None
    timeline_request_id: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionTimelineReport:
        cmd = data.get("cmd") or {}
        return cls(
            command=cmd.get("command"),
            initiator=cmd.get("initiator"),
            command_time=cmd.get("time"),
            event=[MissionTimelineEvent.from_json(e) for e in data.get("event") or []],
            fin_events=[MissionTimelineEvent.from_json(e) for e in data.get("finEvents") or []],
            mission_id=data.get("mission_id"),
            n_missions=data.get("nMssn"),
            version=data.get("ver"),
            timeline_request_id=data.get("timelineRequestId"),
        )


