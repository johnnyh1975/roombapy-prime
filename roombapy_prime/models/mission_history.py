"""Mission history response models, including all 20 MissionTimelineEvent sub-event types.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from .enums_common import _enum_or_none


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
    value anyway and returns the raw string instead of crashing.

    RE-CHECKED AGAINST APP 3.0.0 AND STILL UNSETTLED. None of the
    nineteen values appears in the app's send path in a `done_code`
    context, and the app has no `DoneCode` enum under any name. Five of
    them do appear as bare literals -- `battery`, `cancel`, `empty`,
    `full`, `none` -- but in a bin-status serialiser, a floor-editor
    back action and a timeline view. Ordinary words colliding, not
    evidence; a literal index cannot tell the difference and neither
    could a count.

    So the position is unchanged: "ok" confirmed from real data, the
    rest inferred, and a real capture carrying any other done code
    settles that one."""

    #: FIELD-CONFIRMED FROM APP 3.0.0 -- the wire values are
    #: **abbreviated camelCase**, not the snake_case this enum carried.
    #:
    #: Two sets in the object pool, used by `isMissionHistorySuccess`
    #: and `isMissionHistoryCancelled` in
    #: `irobot_clean_task_completion_semantics.dart`:
    #:
    #:     success   {ok, busy, dndEnd, returnHomeEnd, timeboxEnd}
    #:     cancelled {cncl, usrSlp, plcDoc, usrEnd, usrSpt, batcncl}
    #:
    #: The six cancelled values are now CONFIRMED FROM A SECOND,
    #: INDEPENDENT SOURCE: the firmware 3.8.126 image lists exactly
    #: `cncl, usrSlp, plcDoc, usrEnd, usrSpt, batcncl` as its
    #: cycle-end DoneCodes. App send-path and firmware agree, which is
    #: the standard this project treats as confirmed rather than
    #: one-derivation-deep. (The five success values are not in the
    #: firmware list -- it enumerates cancellation reasons, and a
    #: success is the absence of one.)
    #:
    #: Nine of the nineteen previous values were wrong. The
    #: lowercase-snake_case rule, carried over from `RegionType`, does
    #: not apply here at all -- **not one** snake_case form appears
    #: anywhere in the APK.
    OK = "ok"
    BUSY = "busy"
    DND_END = "dndEnd"
    RETURN_HOME_END = "returnHomeEnd"
    TIMEBOX_END = "timeboxEnd"

    CANCEL = "cncl"
    USER_SLEEP = "usrSlp"
    PLACE_DOCK = "plcDoc"
    USER_END = "usrEnd"
    USER_SPOT = "usrSpt"
    BATTERY_CANCEL = "batcncl"

    #: UNPROVEN, AND KEPT ONLY AS PLACEHOLDERS. These eight came from
    #: bytecode constant names in an older APK and appear nowhere in
    #: 3.0.0 -- neither as written here nor as the abbreviations the
    #: confirmed values would predict (`schedErr`, `usrRbt`: no hits).
    #:
    #: EXHAUSTIVELY RULED OUT FOR FIRMWARE 3.8.126. A re-verification
    #: of the 393 MB image found each of the six confirmed values
    #: exactly once, all six contiguous in a single 31-byte window --
    #: one static enum literal, not scattered fragments. No additional
    #: candidate token exists anywhere in the image. That is as far as
    #: string analysis reaches, and it reaches far enough: THESE EIGHT
    #: ARE NOT FIRMWARE-BACKED and must not be cited as though they
    #: were.
    #:
    #: KEPT ANYWAY, deliberately. The check covers ONE firmware
    #: version; it does not prove these never existed on older builds,
    #: and this project supports robots several generations back.
    #: `_enum_or_none()` returns the raw string for anything unmatched,
    #: so nothing depends on them being right -- removing them would
    #: trade a harmless unused member for a lost record of what was
    #: once believed and why.
    BATTERY = "battery"
    EMPTY = "empty"
    FULL = "full"
    INCOMPLETE = "incomplete"
    NONE_ = "none"
    SCHEDULE_ERROR = "schedule_error"
    STUCK = "stuck"
    USER_REBOOT = "user_reboot"


#: REMOVED FROM HERE: a second `PadCategory`, seven UPPERCASE values
#: (`DRY`, `WET`, `PLATE`, `NO_PAD`, `REUSABLE_DRY`, `REUSABLE_WET`,
#: `INVALID`), documented as "Confirmed (androguard): 7 values".
#:
#: `mission_control` HAS CARRIED THE @SerialName READING OF THE SAME
#: ENUM ALL ALONG, and says so in its own docstring: "CONFIRMED
#: @SerialName wire values ... for the REST/mission-history pad field".
#: That is this field. The two map one to one -- `DRY`/`dispDry`,
#: `WET`/`dispWet`, `PLATE`/`padPlate` -- so they are one enum read
#: twice: once off the constant names, once off the annotation.
#:
#: Two classes with the same name in one package, and the parser
#: reached for the constant-name one while the annotated one sat unused
#: next door. Nothing failed: `_enum_or_none()` returns the raw string
#: on no match, so `pad_category` simply stayed a str against every real
#: response instead of becoming an enum. The same silent shape as
#: `commandParams`/`params`.
#:
#: A device reporting the uppercase vocabulary still parses -- as a raw
#: string, which is what an unrecognised value has always become here.
from .mission_control import CommandParams, PadCategory, Region


class RankOverlap(StrEnum):
    """Confirmed (androguard): 3 values."""

    DEEP_CLEAN = "DEEP_CLEAN"
    DETAIL_CLEAN = "DETAIL_CLEAN"
    EXTENDED_CLEAN = "EXTENDED_CLEAN"


class CoverageStrategy(StrEnum):
    """CONSTANT NAMES, NOT CONFIRMED WIRE VALUES.

    Previously documented as "Confirmed (androguard): 3 values". The
    androguard reading was accurate about what it read -- Kotlin enum
    CONSTANT NAMES -- which is not the same as a wire value. That
    phrasing has now produced four wrong vocabularies in this library
    (the DND commands, `RegionType.TID`, `CleaningProfileType`, and the
    duplicate `PadCategory` above), so it is no longer treated as a
    confirmation on its own.

    SEARCHED AND NOT FOUND: app 3.0.0 contains none of these three
    strings, in any casing -- not `HYBRID_COVERAGE_PLANNER`, not
    `hybridCoveragePlanner`, not `hybrid_coverage_planner`, and no
    `coverageStrategy` key. So there is no second reading to compare
    against and no basis for changing the values either.

    That is a complete result, not a gap: unlike the four cases above,
    nothing here says the current values are wrong. They are simply
    unverified, and inventing a lowercase variant to match a pattern
    would be the same mistake in the opposite direction.

    `_enum_or_none()` returns the raw string on no match, so a real
    capture carrying a different vocabulary will surface it rather than
    crash -- and settles this the moment one arrives."""

    HYBRID_COVERAGE_PLANNER = "HYBRID_COVERAGE_PLANNER"
    RESERVED = "RESERVED"
    ROOM_SEGMENTATION = "ROOM_SEGMENTATION"


class MissionType(StrEnum):
    """What a mission was asked to do (app 3.0.0, `MissionType`).

    Three wire strings, none of them read anywhere in this library. The
    distinction is not derivable from the command: a room clean and a
    zone clean both arrive as `start` with regions, and only the region
    types tell them apart -- which is a reconstruction where the server
    states it outright."""

    FULL_HOME = "full_home"
    ROOM_CLEANING = "room_cleaning"
    ZONE_CLEANING = "zone_cleaning"


class TimelineEventPhase(IntEnum):
    """Whether a timeline event has happened, is happening, or is
    planned (app 3.0.0, `RobotTimelineEventPhase`).

    NAMES A SPLIT THIS LIBRARY ALREADY PARSES. `MissionTimeline` reads
    `finEvents` (done) and `futureEvents` (intended) into separate
    lists, which is the same distinction arrived at from the field
    names. `current` is the third value and has no list of its own --
    the event in progress presumably sits at the head of one of them,
    and nothing here has seen a live timeline to say which.

    Not wired into the parser: the two lists already carry the meaning.
    Named so that an event object carrying an explicit phase is
    readable rather than a number."""

    PAST = 0
    CURRENT = 1
    FUTURE = 2


class FaultScene(IntEnum):
    """Which task was running when a fault was raised (app 3.0.0,
    `FaultScene`).

    THE SAME ERROR CODE MEANS DIFFERENT THINGS PER TASK, which is why
    the app resolves a fault against a scene rather than showing one
    text per code. A stall during `washTask` is a dock problem; the
    same stall during `cleanTask` is a robot problem.

    TWELVE SCENES, AND THE DOCK OWNS FIVE of them -- wash, dry, evac,
    fluid refill and dock. This library's error catalogue is flat: 112
    codes, one text each, no scene. That is not wrong, it is less than
    the vendor has.

    THERE IS NO SCENE FIELD, and saying "the field has not been
    identified" was wrong -- it implied one exists to find.
    `getFaultScene({cmStatus, command})` DERIVES the scene from the
    mission status and the currently running command. The app computes
    it; the robot never sends it.

    THAT MAKES IT BUILDABLE RATHER THAN BLOCKED. Both inputs are
    already read here -- `CleanMissionStatus` carries phase and cycle,
    and `rw-software.lastCommand` carries the command. A scene could be
    derived the same way the app derives it.

    FIVE SCENES ARE FULLY SPECIFIED and `scene_for()` below derives
    them. Saying the conditions were "only partly recorded" was another
    thing looked for in the wrong place -- they are written out as code,
    and as a data file this project had not opened.

    THE DOCK SCENES CHECK COMMAND *OR* PHASE, which is the part worth
    understanding rather than copying: a fault during `padWash` is a
    wash fault whether the user asked for the wash or the robot started
    it on its own. `evacTask` checks three sources -- command, cycle and
    phase.

    THE OTHER SEVEN STAY UNRESOLVED, and they are the ones with no
    stated condition at all: cleanTask is the documented default, and
    startTask, vacuumTask, mopTask, chargeTask, updateTask and
    returnTask have empty condition sets in the vendor extract. Guessing
    at those would put a wrong task name on a real error message."""

    @staticmethod
    def scene_for(
        command: str | None = None,
        cycle: str | None = None,
        phase: str | None = None,
    ) -> FaultScene | None:
        """The scene for a fault, from the five specified rules.

        Returns None rather than `CLEAN_TASK` when no rule matches.
        `cleanTask` is documented as the default case, but defaulting
        here would hide the seven unresolved scenes behind a plausible
        answer -- a caller that wants the default can say so."""
        if "evac" in (command, cycle, phase):
            return FaultScene.EVAC_TASK
        if command == "washpad" or phase == "padWash":
            return FaultScene.WASH_TASK
        if command == "drypad" or phase == "padDry":
            return FaultScene.DRY_TASK
        if command == "flrefill" or phase == "refill":
            return FaultScene.FLUID_REFILL_TASK
        if command == "dock" or cycle == "dock":
            return FaultScene.DOCK_TASK
        return None

    CLEAN_TASK = 0
    START_TASK = 1
    VACUUM_TASK = 2
    MOP_TASK = 3
    RETURN_TASK = 4
    DOCK_TASK = 5
    EVAC_TASK = 6
    WASH_TASK = 7
    DRY_TASK = 8
    FLUID_REFILL_TASK = 9
    UPDATE_TASK = 10
    CHARGE_TASK = 11


class Initiator(StrEnum):
    """Who started a mission (app 3.0.0, `Initiator`, 25 values).

    THIS PROJECT KNEW TWO OF THEM. `initiator` has been read as a bare
    string with a docstring recording the two values real captures
    happened to contain -- `cloud` for schedule-triggered and `rmtApp`
    for app-triggered. The vendor names twenty-five, and the interesting
    ones are the ones no capture would have shown:

        alexa · siri · google · ifttt · homey · openHAB · yonomi
        bosch · swisscom · alismart          third-party assistants
        dockBtn                              the physical dock button
        manual                               the button on the robot
        schedule · rmtAuto · loclAuto        automatic triggers
        localApp · rmtApp                    the iRobot app
        team                                 the other robot, teaming

    WHY IT MATTERS BEYOND COMPLETENESS: "why did the robot start?" is a
    question people actually ask, and `cloud` versus `dockBtn` versus
    `alexa` answers it. A household with a voice assistant and a
    schedule cannot tell them apart today.

    OFFERED, NOT IMPOSED. `initiator` stays a str on the event: values
    outside this list must survive, and a server that adds a
    twenty-sixth integration should not break parsing. Use
    `Initiator(event.initiator)` where a name helps.

    FOUND BY vendor_gap_report.py, not by anyone looking. Nobody would
    have searched for an enum called `Initiator` while reading a field
    documented as having two values."""

    ALEXA = "alexa"
    ALISMART = "alismart"
    BOSCH = "bosch"
    CLOUD = "cloud"
    DOCK_BUTTON = "dockBtn"
    HOMEY = "homey"
    IFTTT = "ifttt"
    IFTTTC = "iftttc"
    INTERNAL = "internal"
    LOCAL_APP = "localApp"
    LOCAL_AUTO = "loclAuto"
    MANUAL = "manual"
    OPENHAB = "openHAB"
    REMOTE_APP = "rmtApp"
    REMOTE_AUTO = "rmtAuto"
    SCHEDULE = "schedule"
    SHELL = "shell"
    SIM_AUTO = "simAuto"
    SIRI = "siri"
    SWISSCOM = "swisscom"
    TEAM = "team"
    UNKNOWN = "unknown"
    WIFI = "wifi"
    YONOMI = "yonomi"


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
    #: `favoriteId` and `userMapId`, both declared by `Command` and
    #: neither read -- so a mission started from a favourite could not
    #: say which one, and the history could not tell a favourite run
    #: from a manual one.
    favorite_id: str | None = None
    user_map_id: str | None = None
    params: CommandParams | None = None
    regions: list[Region] = field(default_factory=list)
    robot_id: str | None = None
    time: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionCommandRecord:
        if not isinstance(data, dict):
            return cls()
        params_data = data.get("params")
        return cls(
            clean_all=data.get("cleanAll"),
            command=data.get("command"),
            initiator=data.get("initiator"),
            map_id=data.get("p2map_id") or data.get("mapId"),
            favorite_id=data.get("favoriteId") or data.get("favorite_id"),
            user_map_id=data.get("userMapId"),
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
    mission history response.

    FOUR MINUTE FIELDS, NOT ONE, and they are not interchangeable:

        durationM   wall clock, start to finish
        runM        actually cleaning
        pauseM      paused
        chrgM       charging mid-mission

    A consumer asking "how long did the last clean take" almost
    certainly wants `runM`, not `durationM` -- a robot that returned to
    charge halfway through reports a wall-clock duration several times
    its cleaning time. This entry carries all four, so the decision
    belongs to the caller rather than here.

    Verified against the vendor's own 20-entry sample: every field in
    that payload maps to something on this class, none left over. `timeline` deliberately remains raw JSON
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
    #: `oModeStats` -- MINUTES AND AREA PER OPERATING MODE.
    #:
    #: In neither the app model NOR our reader. It appears in real
    #: mission entries (`{"vac": {"nMin": 10, "sqft": 90}}`) and is
    #: absent from iRobot's own 33-key `MissionHistoryItemResponse`:
    #: **the robot sends more than its maker's app declares.**
    #:
    #: It answers the one question a Combo mission raises that a single
    #: duration cannot -- how much of it was vacuuming and how much
    #: mopping. `duration_m` says forty minutes; this says ten of them
    #: were vacuum over ninety square feet.
    #:
    #: Kept raw. The inner keys are mode names (`vac` observed; `mop`
    #: and `vacMop` plausible and unseen), and inventing a model for
    #: modes nobody has captured is what put three guessed keys in this
    #: file already.
    o_mode_stats: dict[str, Any] | None = None
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
        if not isinstance(data, dict):
            return cls()
        command_data = data.get("cmd") or data.get("command")
        timeline_data = data.get("timeline") or {}
        # `covStrat` IS THE VENDOR'S KEY, from `MissionTimelineDto`.
        # `coverageStrategy` was the readable guess, and like `dirt` and
        # `map_id` before it, no robot has ever sent that spelling --
        # this has read None on every mission.
        coverage_strategy = (
            (timeline_data or {}).get("covStrat")
            or (timeline_data or {}).get("coverageStrategy")
        )
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
            # AND A CORRECT KEY IS NOT A GUARANTEED VALUE.
            #
            # @jouwdan's Max 705 returned 30 parsed missions on 0.3.0b1
            # with the corrected readers in place: **all 30 `dirt` values
            # None, zero room `coverage`, zero `map_id`, empty
            # `covStrat`.** Timelines were populated (2-68 events per
            # mission), so the parse itself is working.
            #
            # So these fields are model-dependent, firmware-dependent, or
            # both -- and this project cannot yet say which. What changed
            # today is that reading them is no longer the reason they are
            # absent. Before, a correct robot and a wrong key looked
            # identical; now only one explanation is left.
            #
            # `dirt` IS THE VENDOR'S KEY. `numberOfDirtDetects` was a
            # readable guess at what the field might be called, and no
            # robot has ever sent it -- so this counter has read None on
            # every mission since it was written.
            o_mode_stats=data.get("oModeStats"),
            number_of_dirt_detects=(
                data.get("dirt")
                if data.get("dirt") is not None
                else data.get("numberOfDirtDetects")
            ),
            docked_at_start=data.get("dockedAtStart"),
            ended_on_dock=data.get("eDock"),
            command=MissionCommandRecord.from_json(command_data) if command_data else None,
            # Same shape of guess: the response says `map_id`.
            static_map_id=(
                data.get("map_id")
                if data.get("map_id") is not None
                else data.get("staticMapId")
            ),
            coverage_strategy=_enum_or_none(CoverageStrategy, coverage_strategy),
            rank_overlap=_enum_or_none(RankOverlap, data.get("rankOverlap")),
            pad_category=_enum_or_none(PadCategory, data.get("padCategory")),
            timeline=parse_mission_timeline(timeline_events),
            raw=data,
        )


def parse_mission_history(data: dict[str, Any] | list[dict[str, Any]]) -> list[MissionHistoryEntry]:
    """Converts the raw get_mission_history() response into a list of
    typed MissionHistoryEntry objects.

    THE RESPONSE IS A BARE ARRAY. Confirmed from the app's own
    `restservices/missionhistory` package: the API method returns
    `Result<List<MissionHistory>>`, and `MissionHistory` is a single
    entry -- `startTime`, `durationM`, `sqft`, `done_raw`, `nMssn`,
    `robot_id`. There is no envelope class anywhere in that package: 63
    files, 30 of them `$$serializer`, none for a container with a root
    field.

    So the `missions` and `history` keys below never fire. They were
    guesses, and they were guesses in a place where being wrong is
    invisible: a response full of missions parsing to an empty list
    reads exactly like a robot with no history.

    Kept anyway. They cost one branch, they cannot match a bare array,
    and if iRobot ever wraps the response the parser survives it. What
    changed is that the array form is now the documented case rather
    than the fallback.

    The `responseCode`/`responseBody` hull around the vendor's own
    sample in `res/raw/atlantis_history_responses.json` is the
    SIMULATOR's wrapper, not the server's -- every `*_sim_responses.json`
    in that directory carries the same one."""
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
    "room" (lowercase), the same pattern as RegionType/DoneCode.

    FOUR OF FIVE NOW CONFIRMED FROM A SECOND SOURCE (app 3.0.0): "zone"
    and "room" appear in `robot_meta_data.dart::_parseRobotCommandRing`
    and "poly" in `_parseRobotCommandOuterRings` -- the timeline
    parsers, reading the same field this enum types. "poly" had been
    changed to lowercase by pattern alone; it is no longer a guess.

    "waypoint" REMAINS INFERRED. It appears nowhere in app 3.0.0 in any
    casing, so the lowercasing that four siblings confirm is the only
    argument for it. Left as is rather than reverted -- there is no
    evidence against it either, and `_enum_or_none()` surfaces a
    mismatch as a raw string."""

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
        if not isinstance(data, dict):
            return cls()
        return cls(command=data.get("command"), initiator=data.get("initiator"), time=data.get("time"))


@dataclass(frozen=True)
class DiscoveryEvent:
    """Confirmed (jadx): mapId, mapVersion, regionId.

    TWO VALUES OF `mapVersion` ARE MARKERS, NOT VERSIONS.
    `MapVersionType` (app 3.0.0, `map_to_pb.dart`) declares:

        none         = 0
        roomIdOffset = 99998
        geoJson      = 99999

    `99999` marks a map in iRobot's GeoJSON format, `99998` a special
    handling of room ids. Both distinguish map ORIGINS inside a field
    that otherwise carries version identifiers.

    Nothing here compares or orders `mapVersion`, so no code changes.
    Written down because a value in the 99000s is the kind of number a
    reader treats as a very high version rather than as a flag."""

    map_id: str | None = None
    map_version: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DiscoveryEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(map_id=data.get("mapId"), map_version=data.get("mapVersion"), region_id=data.get("regionId"))


@dataclass(frozen=True)
class ErrorEvent:
    """Confirmed (jadx): only field value (presumably an error code,
    analogous to MissionHistoryEntry.error_code)."""

    value: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ErrorEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(value=data.get("value"))


@dataclass(frozen=True)
class EvacEvent:
    """Confirmed (jadx): error, state -- auto-evac process (evac dock)."""

    error: int | None = None
    state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EvacEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(error=data.get("error"), state=data.get("state"))


@dataclass(frozen=True)
class LiveViewEvent:
    """Confirmed (jadx): eventId, status."""

    event_id: str | None = None
    status: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LiveViewEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(event_id=data.get("eventId"), status=data.get("status"))


@dataclass(frozen=True)
class PadDryEvent:
    """Confirmed (jadx): error, padDryState -- mop pad drying cycle."""

    error: int | None = None
    pad_dry_state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PadDryEvent:
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
        return cls(
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            ordered=data.get("ordered"),
            plan_type=_enum_or_none(PlanType, data.get("type")),
            upcoming=[_enum_or_none(PlanUpcoming, v) for v in (data.get("upcoming") or [])],
        )


@dataclass(frozen=True)
class PolygonEvent:
    """CORRECTED (this session, parallel native-analysis track,
    $$serializer.<clinit> inspection): 4 of 7 wire keys were wrong --
    mapId->p2mapId, mapVersion->p2mapvId, polyId->polyid (fully
    lowercase, not a casing variant of the property name at all),
    regionId->rid. "polyid"/"rid" specifically are NOT derivable from
    the Kotlin property name by any casing transformation -- exactly
    why the earlier "Confirmed (androguard)" DEX-field-list reading
    couldn't have caught this (same category as the CommandParams
    wire-format bug this project fixed earlier this session; see that
    class's own to_json() docstring for the general lesson). area/
    areaCleaned/poly were already correct and are unchanged.

    This is a READ-side model (from_json only) -- a wrong key here
    silently produced None for real data rather than breaking an
    outgoing command, a quieter failure mode than the CommandParams
    bug but a real one: any caller reading map_id/map_version/poly_id/
    region_id from real mission-history data would have gotten None
    every time, regardless of what the actual polygon event
    contained."""

    area: int | None = None
    area_cleaned: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    poly: list[Any] = field(default_factory=list)
    poly_id: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PolygonEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(
            area=data.get("area"),
            area_cleaned=data.get("areaCleaned"),
            map_id=data.get("p2mapId"),
            map_version=data.get("p2mapvId"),
            poly=data.get("poly") or [],
            poly_id=data.get("polyid"),
            region_id=data.get("rid"),
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
        if not isinstance(data, dict):
            return cls()
        return cls(
            error=data.get("error"),
            fluid_amount=data.get("fluidAmount"),
            fluid_replenishment_state=data.get("fluidReplenishmentState"),
        )


class RoomStatus(IntEnum):
    """How a room visit ended (app 3.0.0, `RoomEvent.RoomStatus`, whose
    @SerialName values are the numbers themselves).

    `ZoneEvent.ZoneStatus` declares the identical nine values, so this
    enum serves both.

    OFFERED, NOT IMPOSED. `RoomEvent.status` stays an int -- callers
    already compare against numbers, and swapping the type would break
    them for a naming convenience. Use `RoomStatus(event.status)` where
    a name helps.

    THE THREE THAT CHANGE WHAT A DISPLAY SHOULD SAY:
    `FINISHED_WITH_MORE_PASSES` is not "done", `SKIPPED_WILL_RETURN` is
    not "skipped", and `KIDNAPPED` means the robot was picked up rather
    than that it failed."""

    FINISHED = 0
    FINISHED_WITH_MORE_PASSES = 1
    PARTIAL_INCOMPLETE = 2
    PARTIAL_SKIPPED = 3
    KIDNAPPED = 4
    USER_ENDED = 5
    ROBOT_ABORTED = 6
    SKIPPED = 7
    SKIPPED_WILL_RETURN = 8


class TravelReason(IntEnum):
    """Why the robot left for somewhere else (app 3.0.0,
    `TravelEvent.TravelReason`).

    WORTH READING BEFORE INTERPRETING A TRAVEL EVENT: five of the twelve
    are routine mid-mission errands -- recharge, evacuate, refill, pad
    wash, relocalise -- and NOT signs that the mission ended. Treating
    any travel event as an ending is the mistake this list prevents."""

    MID_MISSION_RECHARGE = 0
    ROBOT_ENDED_THE_MISSION = 1
    EVACUATE_BIN = 2
    RELOCALIZE = 3
    MISSION_ENDED_IN_ERROR = 4
    EMPTY_TANK = 5
    USER_ENDED_THE_MISSION = 6
    ROBOT_TRAPPED = 7
    TRY_TO_PLAN_AROUND_BLOCKED_REGION = 8
    UNKNOWN_REASON_ERROR = 9
    REFILL_FLUID_RESERVOIR = 10
    PAD_WASH = 11


class TravelStatus(IntEnum):
    """How a travel leg ended (app 3.0.0, `TravelEvent.TravelStatus`).

    `REPLANNED` is the one that matters for progress tracking: the robot
    changed its route rather than failing."""

    SUCCESS = 0
    FAILED = 1
    USER_ENDED = 2
    USER_SKIPPED = 3
    REPLANNED = 4


class PadWashReason(IntEnum):
    """Why the robot went to wash its pad (app 3.0.0,
    `PadWashEvent.PadWashReason`).

    `MAX_AREA_REACHED` is the mid-mission one, and it is what
    `pwReturn = 2` with `pwAreaInterval` produces -- a real Combo 405
    runs in exactly that configuration. `END_OF_MISSION` is the other
    common case. The two are worth telling apart: only the first happens
    while cleaning is still in progress."""

    RESERVED = 0
    AFTER_REGION_COMPLETED = 1
    MAX_AREA_REACHED = 2
    END_OF_MISSION = 3


class WetOutStatus(IntEnum):
    """How a wet-out step ended (app 3.0.0,
    `WetOutEvent.WetOutStatus`)."""

    RESERVED = 0
    SUCCESS = 1
    ENDED_DUE_TO_OBSTACLES = 2
    SOFTWARE_ERROR = 3
    EXTERNAL_CAUSE = 4
    PAD_DEPLOYMENT_FAILURE = 5


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
    #: `coverage` -- how much of the room this visit actually did.
    #:
    #: NEVER READ UNTIL NOW, and it was in the payload the whole time.
    #: `RoomInfoDto` in app 3.0.0 declares it alongside `area`,
    #: `passArea`, `passCount` and `totalArea`, and iRobot's own analysis
    #: notes it makes per-room mission progress directly computable --
    #: without needing time estimates at all.
    #:
    #: The docstring above spends fourteen lines reasoning about whether
    #: `area` or `total_area` means "covered". The field that answers it
    #: was sitting in the same object, unparsed.
    coverage: float | None = None
    map_id: str | None = None
    map_version: str | None = None
    pass_area: int | None = None
    pass_count: int | None = None
    region_id: str | None = None
    #: WHAT THE NUMBERS MEAN — NOW FROM THE VENDOR, and one earlier
    #: reading was wrong.
    #:
    #: `RoomEvent.RoomStatus` (app 3.0.0, explicit numeric @SerialName
    #: values) names all nine:
    #:
    #:   0  FINISHED                    5  USER_ENDED
    #:   1  FINISHED_WITH_MORE_PASSES   6  ROBOT_ABORTED
    #:   2  PARTIAL_INCOMPLETE          7  SKIPPED
    #:   3  PARTIAL_SKIPPED             8  SKIPPED_WILL_RETURN
    #:   4  KIDNAPPED
    #:
    #: HOW THAT SCORES AGAINST THE FIELD-DERIVED READING this comment
    #: used to carry, from @utkjmitch's 49-mission archive on a Y351020
    #: (`{0: 111, 1: 53, 5: 12, 6: 10}`):
    #:
    #:   6  read as "aborted in room" -> ROBOT_ABORTED. **Right**, and
    #:      derived from nothing but sixteen `stuck` missions and the
    #:      knowledge that he rescues the robot from that corner.
    #:
    #:   5  read as "blocked / never entered" -> USER_ENDED. **Wrong.**
    #:      The playroom-door story fit the observation and still fit a
    #:      different cause: a mission the user stopped while that room
    #:      was pending. Had a door-blocked state been the answer, 7 or 3
    #:      would be the codes for it.
    #:
    #:   0/1 read as "presumably clean outcomes, nothing distinguishes
    #:      them yet" -> FINISHED and FINISHED_WITH_MORE_PASSES. The gap
    #:      is closed: 1 means the robot intends another pass.
    #:
    #: THE LESSON IS ABOUT THE HIT, NOT THE MISS. Both readings came
    #: from the same method and the same quality of evidence, and one
    #: was right. Ground truth from a household is real evidence — it
    #: just cannot tell two causes apart when only one was considered.
    #:
    #: STILL AN INT. `_enum_or_none` is not used here: callers already
    #: compare against numbers, and RoomStatus below is offered for
    #: naming rather than imposed.
    status: int | None = None
    total_area: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(
            area=data.get("area"),
            con_passes=data.get("conPasses"),
            coverage=data.get("coverage"),
            map_id=data.get("p2mapId") or data.get("pmapId") or data.get("mapId"),
            map_version=(
                data.get("p2mapvId") or data.get("pmapvId")
                or data.get("mapVersion")
            ),
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
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
    #: `wip` -- travel still in progress. `TravelInfoDto` declares it
    #: and nothing read it, so a journey under way and one finished
    #: looked the same.
    in_progress: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TravelEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(
            destination=_enum_or_none(TravelDestination, data.get("dest") or data.get("destination")),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            poly_id=data.get("polyid") or data.get("polyId"),
            reason=data.get("reason"),
            region_id=data.get("rid") or data.get("regionId"),
            status=data.get("status"),
            waypoint_id=data.get("wid") or data.get("waypointId"),
            in_progress=data.get("wip"),
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
        if not isinstance(data, dict):
            return cls()
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
        if not isinstance(data, dict):
            return cls()
        return cls(map_id=data.get("mapId"), map_version=data.get("mapVersion"), waypoint_id=data.get("waypointId"))


@dataclass(frozen=True)
class WetOutEvent:
    """Confirmed (jadx): status, type -- mop pad wet-out process."""

    status: int | None = None
    wet_out_type: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WetOutEvent:
        if not isinstance(data, dict):
            return cls()
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
    #: Same field, same omission as RoomEvent -- `ZoneInfoDto` declares
    #: it and nothing was reading it.
    coverage: float | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ZoneEvent:
        if not isinstance(data, dict):
            return cls()
        return cls(
            area=data.get("area"),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=(
                data.get("p2mapvId") or data.get("pmapvId")
                or data.get("mapVersion")
            ),
            pass_area=data.get("passArea"),
            pass_count=data.get("passCount"),
            coverage=data.get("coverage"),
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
        if not isinstance(data, dict):
            return cls()

        # THE VENDOR'S SHORT NAMES, added beside the long ones.
        #
        # `MissionEventDto` in app 3.0.0 declares `cmd`, `disc`, `poly`
        # and `tentativeLoc`. This parser read `command`, `discovery`,
        # `polygon` and `tentativeLocation` -- the readable forms, which
        # `reloc` was already the exception to.
        #
        # Both are accepted rather than swapped: the long names came
        # from somewhere, and a payload that uses them would lose four
        # event types if they were removed to tidy up.
        def _sub(key: str, parser: Any) -> Any:
            raw = data.get(key)
            return parser(raw) if raw is not None else None

        return cls(
            start_time=data.get("ts") or data.get("startTime"),
            end_time=data.get("ets") or data.get("endTime"),
            event_type=data.get("type"),
            command=_sub("cmd", CommandEvent.from_json) or _sub("command", CommandEvent.from_json),
            discovery=_sub("disc", DiscoveryEvent.from_json) or _sub("discovery", DiscoveryEvent.from_json),
            error=_sub("error", ErrorEvent.from_json),
            evac=_sub("evac", EvacEvent.from_json),
            live_view=_sub("liveView", LiveViewEvent.from_json),
            pad_dry=_sub("padDry", PadDryEvent.from_json),
            pad_wash=_sub("padWash", PadWashEvent.from_json),
            panorama=_sub("panorama", PanoramaEvent.from_json),
            plan=_sub("plan", PlanEvent.from_json),
            polygon=_sub("poly", PolygonEvent.from_json) or _sub("polygon", PolygonEvent.from_json),
            refill=_sub("refill", RefillEvent.from_json),
            relocalizing=_sub("reloc", TentativeLocationEvent.from_json) or _sub("relocalizing", TentativeLocationEvent.from_json),
            room=_sub("room", RoomEvent.from_json),
            sub_room=_sub("subRoom", SubRoomEvent.from_json),
            tentative_location=_sub("tentativeLoc", TentativeLocationEvent.from_json) or _sub("tentativeLocation", TentativeLocationEvent.from_json),
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

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mission_historymissiontimelinereport
    """

    command: str | None = None
    initiator: str | None = None
    command_time: int | None = None
    event: list[MissionTimelineEvent] = field(default_factory=list)
    fin_events: list[MissionTimelineEvent] = field(default_factory=list)
    future_events: list[MissionTimelineEvent] = field(default_factory=list)
    mission_id: str | None = None
    n_missions: int | None = None
    version: str | None = None
    timeline_request_id: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionTimelineReport:
        if not isinstance(data, dict):
            return cls()
        cmd = data.get("cmd") or {}
        return cls(
            command=cmd.get("command"),
            initiator=cmd.get("initiator"),
            command_time=cmd.get("time"),
            event=[MissionTimelineEvent.from_json(e) for e in data.get("event") or []],
            fin_events=[MissionTimelineEvent.from_json(e) for e in data.get("finEvents") or []],
            # `futureEvents` -- WHAT THE ROBOT STILL INTENDS TO DO.
            #
            # `MissionTimelineDto` declares it beside `finEvents` and
            # nothing read it. Finished events say where the robot has
            # been; these say where it is going, which is the half a
            # progress display actually needs and the half this library
            # was throwing away.
            future_events=[
                MissionTimelineEvent.from_json(e)
                for e in data.get("futureEvents") or []
            ],
            mission_id=data.get("mission_id"),
            n_missions=data.get("nMssn"),
            version=data.get("ver"),
            timeline_request_id=data.get("timelineRequestId"),
        )


