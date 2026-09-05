"""Mission command payload models (RoutineCommand/CommandParams/Region).

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag, StrEnum
from typing import Any

from .enums_common import _enum_or_none
from .geometry import Position


#: THE CASE CONFLICT THIS COMMENT USED TO DESCRIBE NEVER EXISTED.
#:
#: An earlier revision recorded that app 3.0.0 spells the multi-word
#: commands in camelCase (`washPad`, `dryPad`, `stopEvac`, `stopPadDry`,
#: `fluidRefill`, `pointClean`) while this enum uses lowercase, and
#: argued at length for keeping the field-confirmed lowercase forms.
#:
#: That comparison was made against `CommandType` -- a DOMAIN enum whose
#: members mirror their own names (`dryPad -> "dryPad"`). Mirrored names
#: are not wire values; they are what an enum looks like when nobody
#: annotated it.
#:
#: THE DECISIVE SOURCE IS `mission_model.toPayload` (Dart), the function
#: that builds the command body actually published. It carries twenty
#: string literals, and **all twenty are lowercase**: `washpad`,
#: `drypad`, `stopevac`, `stoppaddry`, `flrefill`, `point_clean`,
#: `start_dnd`, `stop_dnd` and the rest. `clean_control_util
#: ._trackIrobotCleanCommandResult` carries the identical twenty.
#:
#: Every command this enum and the vendor's share matches exactly.
#: There was never anything to reconcile, and no robot ever accepted
#: two spellings.
#:
#: WORTH KNOWING WHICH SOURCE SETTLED IT, because two disagreed: one
#: extraction reported these as `CommandTypeDTO` `@SerialName` values,
#: another reported that DTO's annotation map as empty. `toPayload`
#: moots the question -- it is the code that sends, not a description of
#: it.
#:
#: TWO CONSEQUENCES, both retiring a caveat rather than adding one:
#:
#: - `flrefill` was carried as an admitted guess ("appears in no capture
#:   at all"). `CommandTypeDTO.FLUID_REFILL` serialises as exactly that.
#:   It is confirmed.
#: - `point_clean`, already confirmed from a real server-stored routine,
#:   is confirmed a second time from the vendor's own serialiser.
#:
#: THE LESSON, which cost four wrong constants below: a Kotlin enum with
#: an empty `wireValues` map has no wire values to offer. Reading its
#: member names as if they were wire values invents a vocabulary the
#: server has never seen.
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
    #: RESET IS THE REBOOT BUTTON. Confirmed in APK 3.0.0:
    #: `device_restart_page` -> `ControlSettingsRepo.restartDevice` ->
    #: `MissionCommandType.reset` (index 7), on the same `send` channel
    #: as `start`. The robot drops offline for about a minute.
    #:
    #: samm-git/irobot-explore found the same value in app 1.6.0, where
    #: the binary carries the string "Sending reset command to reboot
    #: robot". 3.0.0 has `Restart device failed` instead -- same
    #: command, different logging.
    #:
    #: Sent like any other simple command:
    #: `send_simple_command("reset")`.
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
    # ADDED (parallel native-analysis track): the one value from the
    # complete 30-entry CommandType enum genuinely missing here. The
    # other candidates that report listed (flushsluice/drypad/
    # stoppaddry) turned out to already exist further down this enum --
    # checked against the full member list rather than trusting the
    # report, which is also how this duplicate was caught.
    POINT_CLEAN = "point_clean"
    DRYPAD = "drypad"
    STOPPADDRY = "stoppaddry"
    FLUSHSLUICE = "flushsluice"
    #: DO NOT DISTURB, over the command topic. Previously DND was
    #: writable only through the settings REST call; these are a second
    #: way in.
    #:
    #: CORRECTED (APK 3.0.0): the wire values are `start_dnd` and
    #: `stop_dnd`. This enum previously carried
    #: `startDoNotDisturb`/`stopDoNotDisturb`, read off `CommandType`'s
    #: member names.
    #:
    #: THREE PLACES SEND THE SNAKE FORMS: `mission_model.toPayload`
    #: (the command body builder), `clean_control_util
    #: ._trackIrobotCleanCommandResult`, and
    #: `device_list_view_model._parseFullLiveMapData`.
    #:
    #: THE CAMELCASE FORMS APPEAR EXACTLY ONCE EACH, as members of a
    #: Dart enum whose every value is null -- names, not values. That
    #: is the same shape that produced the mistake on the Kotlin side,
    #: found again on the other side of the bridge.
    #:
    #: Removed alongside them: `POINTCLEAN_VENDOR = "pointClean"` and
    #: `FLUIDREFILL_VENDOR = "fluidRefill"`, offered as vendor
    #: alternatives "a caller with a dock that refuses one can try".
    #: They were the same mistake and there was nothing to try -- see
    #: the module comment above `MissionCommandType`.
    #:
    #: THEY CARRY NO WINDOW, confirmed from `BasicCommandBuilder`.
    #:
    #: Both appear in its explicit `supportedTypes` list, and `build()`
    #: emits `command`, `initiator` and `time` with every other position
    #: -- params, regions, point, map ids, ordered -- set to null:
    #:
    #:     {"command": "start_dnd", "time": <unix>,
    #:      "initiator": "rmtApp"}
    #:
    #: So these switch Do Not Disturb on and off **ad hoc**. The window
    #: itself is a separate matter, set through
    #: `PUT /v1/households/{id}/settings/dnd`.
    #:
    #: That makes `send_simple_command()` the right path for both --
    #: they need nothing this library does not already send.
    START_DND = "start_dnd"
    STOP_DND = "stop_dnd"
    #: `point_clean` is confirmed twice over: verbatim in a real
    #: favourite definition returned by the server (a routine named
    #: "Spot Clean" with `routine_type: SPOT_CLEAN`), and as
    #: `CommandTypeDTO.POINT_CLEAN`'s own `@SerialName`.
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

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mission_controlroutinecommand
    """

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
    #: PRESENCE MATTERS, THE VALUE DOES NOT -- for region cleans.
    #:
    #: @Echovictor37 ran a region-targeted clean with `"localApp"`
    #: instead of `"rmtApp"` and it behaved identically. The evidence
    #: trail records the field as mandatory; this narrows that to
    #: "mandatory, and not inspected".
    #:
    #: Untested for anything else, and worth not over-reading: a robot
    #: that ignores the value on one command may not ignore it on
    #: another, and iRobot's own `BasicCommandBuilder` defaults to
    #: `RmtApp` rather than leaving it out.
    initiator: str | None = None
    # Wire key "id". CARRIED AS A PASSTHROUGH ONLY, and app 3.0.0
    # weakens the case for it further.
    #
    # An earlier note here said this was "CONFIRMED to be written by the
    # real app's own buildJsonFromCommandDef -- one of exactly seven
    # fields it emits". The 2.2.4 reading says the opposite: `id`,
    # `robot_id` and `select_all` were the three fields that function
    # REMOVED unconditionally before sending. And `CommandDTO` in 3.0.0
    # declares thirteen fields with no `id` among them.
    #
    # So no version of the vendor's client sends it. The field stays
    # anyway, because it is never GENERATED here -- if a stored favorite
    # carries one, the round-trip preserves it; if it does not, nothing
    # is sent. That is the failure mode verify_region_commands' fidelity
    # check exists to catch, and preserving an unknown key costs less
    # than silently dropping it.
    command_id: str | None = None

    def to_json(self, legacy_map_keys: bool = False) -> dict[str, Any]:
        """The command as JSON.

        `legacy_map_keys` also writes `pmap_id` beside `p2map_id`.

        **OFF BY DEFAULT, and that is the cautious choice rather than
        the obvious one.** The app switches per device
        (`allowLegacyReportedValuesInCommand`), so some robot somewhere
        needs the old name -- but @Echovictor37's confirmed
        region-targeted clean used the payload WITHOUT it, and that is
        the only shape this library knows works on hardware.

        Adding a key to the one confirmed path on the strength of an app
        model is the same mistake as removing `robot_id` would be. The
        switch exists so a legacy-SKU owner can try the other shape; it
        does not flip itself.

        NEW (July 11, eighth session): id_multipolys/params/regions
        now accept either the bytecode-confirmed types
        (CommandPolygon/CommandParams/Region, see below in the module)
        or still raw dicts (backward compatibility/escape hatch for
        cases not covered by the typed models)."""
        body: dict[str, Any] = {
            # TOLERANT ON THE WAY OUT TOO, matching the way in.
            #
            # `_favorite_from_json` reads this field through
            # `_enum_or_none`, so a stored favourite carrying a command
            # this library does not model parses to `None` rather than
            # dropping the favourite. `to_json()` then raised
            # AttributeError on that same None -- so a favourite that
            # survived parsing crashed the moment somebody pressed its
            # button.
            #
            # Three shapes reach here from real stored data: a modelled
            # enum, a string the enum does not cover, and nothing at
            # all. The first two are sent as they came; the third is a
            # command def with no command, which the server stored and
            # this library will not invent a value for.
            "command": (
                self.command_type.value
                if hasattr(self.command_type, "value")
                else self.command_type
            ),
            # `robot_id` AND `select_all` ARE SENT AND SHOULD NOT BE.
            #
            # `CommandDTO` in app 3.0.0 has thirteen fields and neither
            # of these is among them -- nor `id`. iRobot's own 2.2.4
            # code stripped all three unconditionally in
            # `buildJsonFromCommandDef` before sending; 3.0 does not
            # model them at all.
            #
            # KEPT ANYWAY, deliberately. @Echovictor37 confirmed a
            # region-targeted clean on real hardware with exactly this
            # payload, these fields included. Removing them would be a
            # change to the one path in this library that is known to
            # work, on the strength of an app model rather than a test.
            #
            # The right order is: send both, confirm a clean still works
            # without them, then drop them. Not the reverse.
            "robot_id": self.asset_id,
            "ordered": self.ordered,
            # `select_all` IS INERT, CONFIRMED ON HARDWARE.
            #
            # @Echovictor37 sent `select_all: true` with a valid map_id
            # and operating_mode, both with `regions` omitted and with
            # an explicit empty list. PUBACK both times, **no effect
            # either time** -- and notably not the whole-house clean his
            # earlier CLEAN/map_id=None command produced.
            #
            # The APK says why: `CommandDTO` has thirteen fields and
            # `select_all` is not one of them. iRobot's 2.2.4 code
            # stripped it before sending; the robot never sees the key.
            #
            # REFINED: "3.0.0 does not model it" was too strong. There
            # are TWO command models with different field sets --
            # Kotlin's `CommandDTO` (thirteen fields, no `select_all`,
            # WITH `user_pmapv_id`) and Dart's `mission_model.toPayload`
            # (thirteen fields, WITH `select_all`, no `user_pmapv_id`).
            # So `select_all` is modelled in 3.0.0, just not on the path
            # this library mirrors.
            #
            # That does not revive it. @Echovictor37 sent it twice on
            # real hardware and it did nothing either time, which no
            # model can outrank. What it does explain is where the
            # second model is used: `publishRawFurnitureMissionCommand`
            # sends a raw map bypassing `CommandDTO`, and furniture
            # commands are the one path known to take that route.
            #
            # **SO THERE IS NO clean_all PAYLOAD TO FIND.** A whole-house
            # clean is `send_simple_command("start")`, which is confirmed
            # working on several robots. This path exists for regions.
            #
            # The field stays because it costs nothing and removing it
            # would change a confirmed-working region payload. It is
            # documented as inert rather than quietly dropped, so nobody
            # spends another hardware session on it.
            #
            # `select_all` NEVER TRAVELS WITH REGIONS.
            #
            # iRobot's own 2.2.4 code stripped this key before sending
            # and 3.0.0 does not model it, so the robot most likely
            # ignores it. Most likely is not the same as certainly, and
            # this key says "clean everything".
            #
            # @Echovictor37 showed what that costs when it goes wrong:
            # a command the robot accepted and answered by cleaning the
            # whole house instead of the requested room. If some
            # firmware does read `select_all`, a True beside a region
            # list is the one combination that could reproduce it.
            #
            # Nothing in this library sets `clean_all` True today. This
            # makes that stay true rather than depend on nobody ever
            # doing it.
            "select_all": self.clean_all and not self.regions,
        }
        if self.map_id is not None:
            body["p2map_id"] = self.map_id
            # BOTH CONVENTIONS, and this is not belt-and-braces.
            #
            # `CommandDTO` in app 3.0.0 carries `p2map_id`/`user_p2mapv_id`
            # AND `pmap_id`/`user_pmapv_id` as four separate nullable
            # fields -- not as alternatives. The app decides per device:
            # `allowLegacyReportedValuesInCommand` sits beside
            # `isLegacySku` and `isIrobotHomeClassicDevice`.
            #
            # We have no way to evaluate that switch, and sending only
            # the new name means a legacy-SKU robot silently gets a
            # command with no map -- which @Echovictor37 showed produces
            # a WHOLE-HOUSE CLEAN rather than an error.
            #
            # Sending both costs one key on robots that ignore it. Not
            # sending the one a device needs costs a wrong clean --
            # which is why the switch exists. It defaults off because
            # the confirmed payload did not carry it, and a confirmed
            # shape outranks a plausible one.
            if legacy_map_keys:
                body["pmap_id"] = self.map_id
        if self.id_multipolys is not None:
            body["id_multipolys"] = [
                p.to_json() if hasattr(p, "to_json") else p for p in self.id_multipolys
            ]
        if self.params is not None:
            body["params"] = self.params.to_json() if hasattr(self.params, "to_json") else self.params
        # NULL AND EMPTY ARE THE SAME THING, AND THE KEY IS OMITTED FOR
        # BOTH. Verified from the vendor app's own `MissionCommand::
        # toPayload` (Prime 3.0.0, Dart AOT): it null-checks the list,
        # then checks its length, and skips emitting `regions` on
        # either -- so whole-house is produced by the ABSENCE of the
        # key, not by a flag.
        #
        # This used to send `regions: []` for an empty list, because
        # `[] is not None`. That is a payload shape the vendor client
        # never emits, and the one place this project cannot afford to
        # be creative: scope is decided downstream by whether region
        # data is present, and a whole-house run nobody asked for is
        # the most expensive way to find out how `[]` is read.
        if self.regions:
            body["regions"] = [r.to_json() if hasattr(r, "to_json") else r for r in self.regions]
        if self.pmap_version_id is not None:
            body["user_p2mapv_id"] = self.pmap_version_id
            # THE LEGACY PAIR WAS HALF-WRITTEN. `legacy_map_keys` added
            # `pmap_id` beside `p2map_id` but left `user_pmapv_id` out,
            # even though the reasoning above is about all FOUR fields
            # being separate and nullable.
            #
            # The enum name says why it matters:
            # `MidCleanAdjustmentType.SUPPORTED_SKIP_DRC_AND_REQUIRES_USER_PMAPV_ID`
            # -- certain devices REQUIRE `user_pmapv_id`, and that value
            # exists precisely to mark them. A robot in that class got
            # the legacy map id and the modern version id, which is
            # neither convention.
            if legacy_map_keys:
                body["user_pmapv_id"] = self.pmap_version_id
        if self.spot_geometry is not None:
            body["geom"] = self.spot_geometry
        if self.favorite_id is not None:
            body["favorite_id"] = self.favorite_id
        if self.command_id is not None:
            body["id"] = self.command_id
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
    adjusted.

    TID CONFIRMED (this session, parallel native-analysis track, via
    addAdhocRegion()): TID = ad-hoc/temporary zone, as opposed to RID
    (a real, persistent room from the map) and ZID (a real, persistent
    zone). Ad-hoc regions get IDs from a reserved, hardcoded range
    (160-199, via a dedicated adHocCounter) -- explaining why this
    project's own real captured data shows room IDs like 10-16 and
    zone IDs like 100-107 in visibly separate numbering ranges. Each
    ad-hoc region is created alongside a CommandPolygon sharing the
    SAME id (the region<->geometry linking mechanism) -- see
    CommandPolygon's own docstring. Still not observed directly on a
    real device (only RID and ZID have been).

    A FOURTH TYPE EXISTS AND IS DELIBERATELY NOT LISTED HERE:
    kZoneTypeWId, found alongside kZoneTypeRId/ZId/TId in the same
    constant table (parallel APK research). Its wire value could not be
    resolved, and guessing it would be worse than omitting it.

    THE WIRE VALUE IS NOW KNOWN, AND IT IS STILL NOT ADDED. Classic
    firmware ruby-0.7.12 (j9) carries a JSON Schema for the region
    command whose `type` enum reads, verbatim:

        ["rid", "zid", "wid", "tag"]

    So "wid" was the right guess. But that is a CLASSIC contract for the
    local channel, and this enum types what the PRIME app sends over the
    cloud. `IrobotRegionType` lists exactly three members there, so a
    Prime robot still never receives "wid" from the vendor's own client
    -- and adding it here would model a value this generation has no
    evidence of using.

    Two things in that same enum are worth more than the confirmation:

      * "tag" appears nowhere in the Prime app, in any casing. It is a
        fifth value, unaccounted for on both sides.
      * "tid" is ABSENT from the Classic enum. If that firmware enforces
        it, an ad-hoc region command would be rejected on a j9 while
        being correct on Prime -- which would make the region vocabulary
        generation-specific rather than shared. Untested, and worth
        knowing before anyone runs the ad-hoc stage against a Classic
        robot.

    See docs/internal/vendor_schemas_ruby_0_7_12.json.

    The lowercase-the-prefix pattern now holds for all three known
    types (`IrobotRegionType`: rid/zid/tid), so "wid" is the obvious
    candidate -- which is a reason to expect it, not a licence to ship
    it. `IrobotRegionType` lists exactly three members, so whatever WId
    is, the Prime app does not send it.

    If a real capture ever shows a region type this enum does not
    recognise, that is very likely WID, and the observed value settles
    it. Until then it stays unmodelled rather than invented."""

    RID = "rid"
    # REVERTED TO "tid" (APK 3.0.0, Dart `IrobotRegionType`, which
    # states its wire values outright: room -> "rid", zone -> "zid",
    # temporary -> "tid").
    #
    # A previous session changed this to "furniture" and cited an
    # @SerialName for it. There is no such annotation: the Kotlin
    # `RegionDTO.RegionType` carries an EMPTY wire-value map, and its
    # member names are `room`/`zone`/`adHoc` -- "furniture" is not
    # among them either. The claim could not have come from where it
    # said it did.
    #
    # "furniture" IS in the app, which is why it looked right: it
    # appears in `targetKey`, `fromPrefix`, `debugLabel` and
    # `isFurnitureAreaRoomKey` -- internal key composition and display
    # labels. "tid" appears in `_createOneRegion` and
    # `_resolveRegionType`, which build the command. Comparing an
    # identifier where meaning was intended, one more time.
    #
    # The original inference from the rid/zid pattern was right, and the
    # correction broke it. Blast radius stayed small in both directions:
    # _is_safe_command_def() rejects TID regions outright, so only
    # stage 4 (--send-adhoc, never yet run by anyone) could have sent
    # either value.
    TID = "tid"
    ZID = "zid"


@dataclass(frozen=True)
class PadWetnessParam:
    """Three wetness levels, one per pad category.

    LEVELS CONFIRMED (APK, RobotPadWetnessLevel):
        0 Damp   1 Moderate   2 Wet   3 Invalid

    Three usable steps, not a free range.

    WRITE PATTERN CONFIRMED (APK, MoppingSettingsUIService::
    setPadWetness): the app reads the whole map, changes one entry, and
    sends all three back. It does NOT decide which category applies --
    the caller picks the category, and the other two ride along
    unchanged. Same read-modify-write shape as set_virtual_wall, where
    sending a partial list deletes what it omits.

    WIRE KEYS are the property names, camelCase, because this class
    carries no @SerialName annotations: `disposable`, `padPlate`,
    `reusable`.

    padPlate HAS ITS OWN VALUE TABLE, and this is the finding that
    stops a pad-wetness control from being built.

    MoppingAssetConstants holds four pairs of lookup tables, and two of
    them are for wetness:

        kPadWetnessMap      / kReversePadWetnessMap
        kPadPlateWetnessMap / kReversePadPlateWetnessMap

    Backed by separate schema constants -- kPadWetness,
    kPadWetnessPadPlateFieldName and kPadPlateWetnessLevel. That is not
    a naming variant of one range; it is a second mapping.

    So a `1` under `disposable` may not mean what a `1` under `padPlate`
    means, and a control writing one value across all three fields would
    be wrong for at least one of them -- silently, since the robot would
    accept it.

    The table CONTENTS are BSS constants, initialised at runtime and not
    readable statically. Of the three wire keys only `disposable` exists
    as a literal anywhere in the libraries; `reusable` and `padPlate`
    live in native constants.

    SHADOW SPELLING CONFIRMED 31 July 2026 (@chairstacker, G185020).
    A real rw-settings capture reads:

        "padWetness": {"disposable": 3, "reusable": 1, "padPlate": 1}

    camelCase, exactly as the command domain uses -- so borrowing this
    model for the shadow was correct after all. The snake_case worry
    (`pad_plate`) was unfounded.

    THE VALUE RANGE IS RESOLVED (APK, 2 August 2026): padPlate has its
    own enumeration, offset by one from the other two.

        RobotPadWetnessLevel:       0 Damp     1 Moderate  2 Wet       3 Invalid
        RobotPadPlateWetnessLevel:  0 Invalid  1 Damp      2 Moderate  3 Wet

    Two separate enums, exactly as the two lookup-table pairs above
    implied. The earlier warning was right; the second table now has
    contents.

    THIS EXPLAINS THE CAPTURE THAT LOOKED IMPOSSIBLE:

        padWetness: {"disposable": 3, "reusable": 1, "padPlate": 1}
        detectedPad: "padPlate"

    A hard pad plate is fitted, so padPlate is the live field, and 1 is
    Damp on ITS scale. The 3 under `disposable` is Invalid on
    RobotPadWetnessLevel -- no disposable pad is fitted, and the field
    says exactly that. Nothing was out of range: the value that looked
    wrong sat in the field for a pad the robot does not currently carry.

    The earlier note that a 1-based reading "does not hold" was correct
    for RobotPadWetnessLevel and wrong as a general conclusion. It is
    padPlate's own enum that begins at Invalid, which makes it
    effectively 1-based.

    ppWetLvl READS AS A COUNT of usable steps rather than as a flag:
    three steps (1..3) for a robot reporting 3, and no level choice at
    all for one reporting 0 while still mopping. An independent Home
    Assistant integration (a-mavrides/roomba_v4) builds its picker as
    range(1, N+1) from this field, which agrees.

    A GLOBAL CONTROL SHOULD NOT BE BUILT AT ALL (APK, 2 August 2026).
    Two independent findings, either of which would be enough:

    1. REGIONS WIN. CommandParams.copyWith(other) takes `other`'s
       padWetness whenever it is non-null and only falls back to its
       own. A region command carrying padWetness.padPlate = 2 therefore
       beats the global 3 from rw-settings. Seen in the field exactly
       so: one robot with global 3 and all four regions at 2.
    2. THE APP DOES NOT TREAT THIS AS USER-MODIFIABLE.
       onlyUserModifiableParams() keeps precisely one field of the 38 --
       routineModified -- and nulls the rest, padWetness among them.

    So a global slider would be the schedHold pattern again: accepted by
    the server, overridden in practice, and stating something false in
    the UI. Per-region control is a different question and has not been
    investigated.

    WHAT ANY CONTROL WOULD STILL HAVE TO GET RIGHT: which of the three
    fields to write. `detectedPad` names the pad currently fitted, and writing
    padPlate on a robot carrying a disposable pad would apply the wrong
    scale to the wrong field -- accepted by the robot, and wrong in
    silence. The category comes from detectedPad, never from a guess.

    WHAT DID COME OUT OF IT: PadSettings holds `mCategory`
    (RobotPadCategory) and `mWetLevel` (int) as SEPARATE fields in one
    object. Category and wetness are parallel values, not one encoding
    the other -- which is what the read-modify-write approach here
    already assumed, now confirmed rather than hoped.

    THERE IS A CAPABILITY FLAG, confirmed 30 July 2026 across two
    Combo robots: `ppWetLvl` (pp_wet_lvl). One reported 3, the other 0 --
    both mopping robots, so the flag distinguishes pad-wetness LEVELS
    from mopping in general. A control must gate on it rather than on
    scrub or on the dock's pad-wash flag.

    WHAT WOULD STILL SETTLE THE REST: an rw-settings capture with
    padWetness populated, from a robot whose ppWetLvl is nonzero. Diagnostics now dump shadow
    contents (ha_roomba_plus v4.0.0a14), so the next download from a
    Combo answers both the key spelling and whether padPlate's values
    share a range with the others.

    NOT MAPPED EITHER: RobotPadCategory has ten values (Invalid, Damp,
    Dry, Wet, ReusableDamp, ReusableDry, ReusableWet, Plate, All,
    NoPad) against these three fields. Grouping them by name is an
    inference from similar names and NOT a finding.

    Confirmed (androguard): NOT an enum (super = Object), but a
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
        if not isinstance(data, dict):
            return cls()
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
    """CORRECTED (this session, parallel native-analysis track,
    $$serializer.<clinit> inspection): the wire key is snake_case
    "furniture_id", not camelCase "furnitureId". The original
    "Confirmed (androguard): furnitureId" reading had read the Kotlin
    PROPERTY name from the class declaration (val furnitureId: Int),
    not an actual @SerialName annotation or serializer table -- the
    same category of mistake corrected across 18 CommandParams fields
    in the same session (see that class's own to_json() docstring for
    the full list and the general lesson: DEX/property-declaration
    reading is not equivalent to a wire-key confirmation, and
    kotlinx.serialization silently drops undeclared keys rather than
    erroring, so a wrong key here would have meant this field simply
    vanishing on the wire, not a cosmetic mismatch).

    CONTEXT: confirmed via addAdhocRegion(), whose furniture_id
    parameter is parsed directly as this field
    (Integer.parseInt(furnitureId)) -- an ad-hoc polygon's metadata
    specifically references a furniture item (matching features like
    "clean around this couch"), not an arbitrary tag. Its `id` (on the
    enclosing CommandPolygon) is shared with a Region of type TID
    created in the same call -- see RegionType.TID's own docstring for
    the full ad-hoc mechanism."""

    furniture_id: int

    def to_json(self) -> dict[str, Any]:
        return {"furniture_id": self.furniture_id}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CommandPolygonMetadata | None:
        """Parse the metadata block, or None when there is none to parse.

        RETURNS None RATHER THAN AN EMPTY INSTANCE, because there is no
        such thing as an empty one. The previous fallback was
        `return cls()` on a non-dict input, and `furniture_id` has no
        default -- so the guard meant to make malformed input safe
        raised `TypeError: missing 1 required positional argument`
        instead. A fallback that cannot construct the class it returns
        is not a fallback.

        And None is the honest answer here in its own right: an ad-hoc
        polygon's metadata references a real furniture item on the map
        (see the class docstring), so a robot that has no furniture has
        no valid value for this field. @chairstacker's Combo 405 turned
        out to be exactly that case.
        """
        if not isinstance(data, dict) or "furniture_id" not in data:
            return None
        return cls(furniture_id=data["furniture_id"])


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


#: THE CLASS IS GONE FROM 3.0.0, THE ENCODING IS NOT.
#:
#: 2.2.4 had a named `OperatingModeBitmask`; 3.0.0 dropped it and encodes
#: through `IrobotOperatingModeCodec` instead -- still `mask = 1 << bit`,
#: still the same powers of two.
#:
#: So this enum's values stay correct while the class that named them
#: does not exist any more. Worth stating, because "the vendor removed
#: it" reads like "stop using it" and here it means the opposite: they
#: moved from a model to a codec and kept the numbers.
class OperatingModeBitmask(IntFlag):
    """CONFIRMED (parallel native-analysis track, this session), and
    independently validated against this project's own real observed
    data (chairstacker) -- not just a theoretical bytecode reading.
    OperatingMode itself defines bit POSITIONS (0-9), not the final
    values; OperatingModeBitmask.getValue() combines them into the
    single int actually seen on the wire. Each named member here is
    already the final bit VALUE (2**position), matching the wire
    representation directly -- combine with | (bitwise or) the same
    way the real app does, or just construct from a raw int and let
    Python decompose it (IntFlag supports both directions).

    Validated against real data: 2 (a room-cleaning mission, per-
    region) decodes to exactly VACUUMING; 32 (a zone/combo-cleaning
    mission) to exactly VAC_MOP_COMBO_ONLY; 512 (a "Deep" profile) to
    exactly VAC_THEN_MOP; 550 -- seen as cap.oMode in get_state()'s
    shadow response on multiple real devices, previously an
    unexplained raw number -- decomposes exactly to VACUUMING |
    MOP_ONLY | VAC_MOP_COMBO_ONLY | VAC_THEN_MOP, meaning cap.oMode is
    the device's advertised SET OF SUPPORTED modes, not a single
    active one -- a genuinely new, retroactive explanation for a field
    this project had captured but never been able to interpret."""

    TRAVELING = 1
    VACUUMING = 2
    MOP_ONLY = 4
    VIDEO_STREAMING = 8
    AIR_PURIFYING = 16
    VAC_MOP_COMBO_ONLY = 32
    SCRUBBING = 64
    MOWING = 128
    MOPPING = 256
    VAC_THEN_MOP = 512

    #: READ AND WRITE DO NOT USE THE SAME ENCODING FOR "COMBO", and this
    #: enum describes the READ side.
    #:
    #: `cap.oMode = 550` decomposes to 2|4|32|512 on real hardware, so
    #: bit 32 is genuinely advertised. But `IrobotOperatingModeCodec`
    #: (app 3.0.0, decompiled) maps the UI's four mode indices to just
    #: four command values:
    #:
    #:     0 -> 0    1 -> 2 (vacuuming)
    #:     2 -> 4 (mop only)    3 -> 6 (combo)
    #:
    #: Combo as a COMMAND is 6 -- VACUUMING|MOP_ONLY -- not 32. The app
    #: never sends 32.
    #:
    #: So a caller reading `VAC_MOP_COMBO_ONLY` out of `cap.oMode` and
    #: sending it back as `operating_mode` would be sending a value the
    #: vendor's own client never emits.
    #:
    #: IT WORKS ANYWAY, AND THAT IS FIELD-OBSERVED. "Nothing here has
    #: tested it" was wrong when written: ha_roomba_plus's cleaning-mode
    #: selector sends 32 for vacuum-and-mop and records the robot's
    #: answer -- `command 32 -> status 6`, alongside
    #: `command 512 -> status 4`. Both were confirmed before the selector
    #: shipped.
    #:
    #: So the robot accepts a value its own app does not send, and the
    #: status field answers in a THIRD vocabulary. Three encodings for
    #: one concept, which is the pattern this library keeps meeting.
    #:
    #: WHAT THAT CHANGES: not the reading above -- the codec really does
    #: emit 6 -- but the conclusion drawn from it. A caller sending 32 is
    #: not stepping outside what the server accepts, only outside what
    #: the app does.
    #:
    #: 512 AND 1024 ARE NOT COMBINED AS BITS. The codec compares them
    #: with equality, not masking. 1024 has no member in `OperatingMode`
    #: at all -- unmodelled here rather than named, since what it means
    #: is unknown. A reader decomposing a raw int should expect a
    #: leftover bit rather than assume the enum is complete.


class MopInstallDetails(IntEnum):
    """How many mop pads are physically fitted (app 3.0.0,
    `MopInstallDetails`).

    FOUR STATES, NOT ON AND OFF. `onlyLeft` and `onlyRight` mean ONE of
    two pads is mounted -- which only makes sense on a robot with
    DualClean Mop Pads, and this project has one in its parts vocabulary
    already.

    THAT IS A DIFFERENT QUESTION FROM `PadCategory`. `detectedPad` says
    WHAT is fitted -- a dry pad, a wet pad, the bare plate. This says
    HOW MANY of the two mounting points are occupied. A robot can report
    `dispWet` and still be running on one pad.

    `invalid` IS -1, and arrives as 18446744073709551615 in the extract
    -- an unsigned reading of the same bit pattern. Modelled as -1
    because that is what the app means; a caller comparing against the
    unsigned form would never match.

    Not wired into a parser: no field has been identified that carries
    it, and no capture contains one. Named because "one pad of two is
    missing" is a real household state that a binary reading cannot
    express."""

    INVALID = -1
    NONE = 0
    ONLY_LEFT = 1
    ONLY_RIGHT = 2
    INSTALLED = 3


class PadCategory(StrEnum):
    """CONFIRMED @SerialName wire values (parallel native-analysis
    track) for the REST/mission-history pad field.

    SCOPE LIMIT, NARROWED BY A REAL PRIME CAPTURE. This enum was
    documented as the REST-side vocabulary only, with an explicit
    warning that `ro-currentstate.detectedPad` was NOT confirmed to use
    it -- real Classic data shows that field carrying simpler values
    ("reusable", "wet"), and no Prime capture had pinned it down.

    A Prime one now has: chairstacker's Combo 405 reports
    `detectedPad: "padPlate"` on ro-currentstate, which is exactly
    `PadCategory.PAD_PLATE`. So on Prime the two vocabularies agree, at
    least for this value.

    THE CLASSIC CAVEAT STANDS. "reusable" and "wet" are not members
    here, so a Classic robot still needs the loose comparison. Treat a
    match as informative on Prime and as a hint on Classic.

    WORTH KNOWING WHAT THIS FIELD ANSWERS: `padPlate` means the plate
    is fitted with no pad on it. App 3.0.0 infers the same state
    indirectly, from fault code 287 ("Unable to vacuum: remove Pad
    Plate") -- a state it derives from what the robot says is
    impossible. `detectedPad` states it directly, which is the better
    source where a robot sends it.

    Also worth recording: the same research suggested detectedPad might
    be an OBJECT mapping pad categories to ints (mirroring the
    confirmed {disposable, padPlate, reusable} three-int structure).
    This project's own real payload contradicts that for
    ro-currentstate specifically -- detected_pad is a plain scalar
    string there (see CurrentStateShadow's own docstring). The
    object-shaped form may well exist elsewhere; it just isn't this
    field."""

    DISP_DRY = "dispDry"
    DISP_WET = "dispWet"
    REUSABLE_DRY = "reusableDry"
    REUSABLE_WET = "reusableWet"
    PAD_PLATE = "padPlate"
    NO_PAD = "noPad"
    INVALID = "invalid"


class RobotReadinessState(IntEnum):
    """CONFIRMED (parallel native-analysis track): the values carried by
    cleanMissionStatus.not_ready and .cond_not_ready -- i.e. WHY a robot
    refused to start, which the app surfaces through
    handleConditionalStartRefuseReason(vector<RobotReadinessState>)
    rather than through any error field or rejected/report topic.

    THESE ARE ORDINAL POSITIONS, NOT WIRE VALUES -- established by
    comparing all twelve against the Classic app's own decode table
    (iRobot Home 7.18.0, 3 August 2026). Eight are checkable there and
    all eight match the INDEX:

        ours 22 MAP_VERSION_MISMATCH  =  index 22  =  wire 25
        ours 50 PRECHECK_REFUSED      =  index 50  =  wire 53

    The Classic app maps wire values above 10 down by three
    (`values()[jsonInt - 3]`), so anything looking a raw wire value up in
    this enum is off by three above 10. `name_for()` is only used for a
    diagnostics label today, so nothing acts on it -- but a caller that
    starts comparing states has to decide which of the two it holds.
    ha_roomba_plus's const.decode_not_ready() converts wire to index.

    Whether PRIME sends the index or the offset wire value is NOT
    established. Prime's own readiness values run in the 200s
    (`readiness_state` 231, 251, 284 in the app's error specs, and a
    field capture with `condNotReady: [234]`), which is a third range
    again and matches neither reading here.

    DELIBERATELY PARTIAL: the source enum has 80 values (0 "None"
    through 79 "DockUpdate"), but only the ones actually named in the
    research report are listed here. Inventing plausible names for the
    other 68 would be exactly the kind of guess this project avoids --
    an unknown value simply stays an int, which name_for() below
    reports honestly as unknown rather than mislabelling it.

    The refusal reasons most relevant to region-based cleaning (the
    project's central open blocker) are MAP_VERSION_MISMATCH (a
    favorite pointing at a stale map version) and the pad/mode pair
    NO_VAC_WITH_PAD / NO_MOP_WITHOUT_PAD (the mounted pad not matching
    the requested operating mode)."""

    NONE = 0
    MISCONFIGURED = 9
    INVALID_COMMAND = 11
    INVALID_PAD = 17
    MAP_VERSION_MISMATCH = 22
    TANK_LOW = 23
    NO_PAD = 24
    PRECHECK_REFUSED = 50
    UNUSABLE_MAP = 72
    NO_VAC_WITH_PAD = 75
    NO_MOP_WITHOUT_PAD = 76
    DOCK_UPDATE = 79

    @classmethod
    def name_for(cls, value: int | None) -> str:
        """Human-readable name for a raw wire value, or an explicit
        "unknown" marker -- never a guessed label."""
        if value is None:
            return "None"
        try:
            return cls(value).name
        except ValueError:
            return f"UNKNOWN_{value}"


class RoutineTypeParam(StrEnum):
    """CONFIRMED, AND RE-CONFIRMED FROM THE SEND PATH (app 3.0.0).
    Wire format is the enum constant NAME itself as a string (unlike
    most other enums in this module, which lowercase or otherwise
    transform their names) -- matching real observed data directly:
    "REPLAY" and "CLEAN_ALL" have both been seen on real devices
    already (see CommandParams.routine_type's own field docstring).

    ALL SIX APPEAR VERBATIM IN `routine.dart::toJson`, the function that
    serialises a routine for sending. That matters more than the count:
    uppercase values are exactly the shape that turned out wrong in four
    other enums here, all of them read off constant names. This one is
    uppercase AND correct, confirmed by the code that sends it.

    So the rule is not "uppercase is suspicious". The rule is that the
    send path decides, and it happens to say uppercase here.

    A SECOND ENCODING EXISTS AND IS NOT THIS ONE. The vendor's
    `RoutineType` numbers the same six: firstRun=0, cleanAll=1,
    cleanDirty=2, replay=3, spotClean=4, unknown=5. That is the SDK's
    internal ordinal, not what `routine.dart::toJson` writes.

    Same trap as room type, which has three encodings: a caller meeting
    a small integer in a routine-type field must not reach for these
    strings, and a command built from the ordinals would send 1 where
    "CLEAN_ALL" was meant. Only the strings go out.
    FIRST_RUN and CLEAN_DIRTY are confirmed to exist in the enum but
    have never actually been observed on a real device yet."""

    FIRST_RUN = "FIRST_RUN"
    CLEAN_ALL = "CLEAN_ALL"
    CLEAN_DIRTY = "CLEAN_DIRTY"
    REPLAY = "REPLAY"
    SPOT_CLEAN = "SPOT_CLEAN"
    UNKNOWN = "UNKNOWN"


class SuctionLevel(IntEnum):
    """CONFIRMED (parallel native-analysis track, SuctionLevel.java) --
    the real, complete enum behind CommandParams.suction_level. Purely
    numeric, ascending -- INVALID(0) is an explicit error/placeholder
    value, NOT an "Auto"/adaptive option; there is no adaptive suction
    concept at this level at all (see CarpetBoostSettings below for
    where that concept actually lives)."""

    INVALID = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    TURBO = 4


class CarpetBoostSettings(IntEnum):
    """CONFIRMED PRESENT (parallel native-analysis track,
    CarpetBoostSettings.java) but CONFIRMED DEAD CODE, NOT connected to
    CommandParams.carpet_boost or anything else -- a follow-up
    investigation found zero consumers anywhere for this enum's own
    values (.AUTO/.PERFORMANCE/.ECO), and the entire View/Fragment/XML
    UI screen it would have belonged to
    (ActivityCarpetBoostSettingsBinding, LayoutInlineCarpetBoostBinding,
    FragmentCleaningPreferencesBinding) also has zero consumers --
    leftover from an older, pre-Compose UI generation, still compiled
    into the APK but never actually instantiated in the current app.
    Kept here only as a documented dead end, so a future session
    doesn't waste time re-investigating the same lead: this is NOT the
    real mechanism behind carpet_boost -- see CommandParams.carpet_boost's
    own docstring for what actually is."""

    PERFORMANCE = 0
    ECO = 1
    AUTO = 2


@dataclass(frozen=True)
class CommandParams:
    """39 fields, matching CommandParams's actual field count
    (docstring previously said 37 -- stale by two fields,
    no_auto_passes/routine_type were added in later sessions via real
    observed data and this count was never updated), each optional
    (boxed Integer/Boolean in Kotlin = all nullable). This is the
    complete parameter surface for a mission command -- covers
    suction power (suctionLevel), pad wetness (padWetness), carpet
    boost (carpetBoost), room confinement (room_confine), timebox
    (timebox), drive speed for steering commands (vleft/vright) and
    many more. Meaning of some more cryptic individual fields (noKOZ,
    odoaMode, rankOverlap, gentleMode) not further investigated.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mission_controlcommandparams
    """

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
    #: `edgeOnly` and `quiet` -- both new in app 3.0.0's
    #: `CommandParamsDTO`, both absent from 2.2.4 and therefore from
    #: this model until the 3.0 analysis listed them.
    #:
    #: Their VALUES are unknown. Integer per the DTO, and nothing here
    #: guesses at a range: a caller that has a value from a capture can
    #: pass it, and one that does not sends nothing.
    edge_only: int | None = None
    quiet: int | None = None
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
    bit pattern, similar to cap.oMode from get_state()).

    DECODED (this session, parallel native-analysis track): this is a
    bitmask -- see OperatingModeBitmask (above in this module) for the
    confirmed bit-to-meaning mapping, independently validated against
    this project's own real data. Kept as a plain int here (not
    changed to OperatingModeBitmask directly) to avoid any
    serialization-behavior change to existing callers -- wrap a raw
    value with OperatingModeBitmask(value) to decode it meaningfully,
    e.g. OperatingModeBitmask(550) decomposes to exactly VACUUMING |
    MOP_ONLY | VAC_MOP_COMBO_ONLY | VAC_THEN_MOP."""
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
    configuration.

    DECODED (this session, parallel native-analysis track): the full
    enum is RoutineTypeParam (above in this module) --
    FIRST_RUN/CLEAN_ALL/CLEAN_DIRTY/REPLAY/SPOT_CLEAN/UNKNOWN, wire
    format is the constant name itself as a string. Kept as a plain
    str here for the same reason as operating_mode above (no
    serialization-behavior change) -- wrap with
    RoutineTypeParam(value) to validate/work with it as an enum."""
    room_confine: bool | None = None
    rotate: int | None = None
    routine_modified: bool | None = None
    """CONFIRMED (this session, parallel native-analysis track,
    RoutineCommandBuilder.calculateModifiedFlag()): this is a COMPUTED
    comparison value, not a free-form field to set arbitrarily. The
    real app derives it by comparing the command currently being
    built against the original favorite it came from, on three axes:
    region count, region order/IDs (compared positionally), and each
    region's "user-modifiable" params specifically (see this class's
    own docstring for the exact 7-field non-user-modifiable list).
    PRACTICAL CONSEQUENCE: hand-building a RoutineCommand from a
    favorite_id needs this value set correctly to match real app
    behavior, not left as an arbitrary guess or omitted -- the safest
    test design avoids the whole question by resending an EXISTING
    favorite's command_def completely unchanged (naturally "not
    modified", whatever the correct unmodified value turns out to be)
    rather than constructing a new one that would need this
    computed."""
    schedule_hold: bool | None = None
    """CLOSED (this session, parallel native-analysis track): the only
    RoutineCommandBuilder field matching "schedule smart profile" is
    this one (wire key schedHold, already confirmed present in real
    shadow data independent of this question). setScheduleSmartProfile()
    itself is confirmed DEAD CODE -- a builder setter that's never
    actually called anywhere in the real app. Not a gap needing a
    field of its own; the branch in build() that reads it never runs
    against a real device, so scheduleSmartProfile is always false in
    practice and doesn't need to be modeled or set for an
    app-consistent command."""
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
        """Only set (non-None) fields are included.

        CORRECTED (this session, parallel native-analysis track,
        $$serializer.<clinit> inspection -- the stronger evidence than
        the earlier "DEX field list" reading this class's own
        docstring used to cite, which read Kotlin PROPERTY names, not
        @SerialName wire keys; kotlinx.serialization silently DROPS
        undeclared keys, so 18 fields here were being sent under keys
        the real device would have discarded entirely, not just
        cosmetically misnamed): adaptiveCleaning->adaptive,
        captureMode->capture_mode, cleanScoreId->clean_score_id,
        executeInPlace->execute_in_place, manualUpdate->manUpd,
        monitorMode->monitor_mode, noPersistentPass->noPP,
        roomConfine->room_confine, rotate->rot,
        routineModified->routine_modified, scheduleHold->schedHold,
        smartCleanId->smart_clean_id, streamOnRoute->stream_on_route,
        timeboxMinutes->timebox, translate->trans,
        velocityLeft->vleft, velocityRight->vright (plus
        CommandPolygonMetadata's furnitureId->furniture_id, a separate
        class). noAutoPasses is a SPECIAL CASE, deliberately NOT
        touched: it doesn't appear in the confirmed serializer list at
        all (that list has noPersistentPass/noPP instead), but is kept
        because it's independently confirmed from real live data
        (chairstacker's cleanSchedule2[].cmdStr, session 27) -- a
        genuinely different field, not a spelling variant of
        no_persistent_pass, confirmed by checking the Kotlin class's
        own field list directly (which DOES have both fields
        separately) rather than assuming one subsumes the other."""
        raw = {
            "adaptive": self.adaptive_cleaning,
            "binPause": self.bin_pause,
            "capture_mode": self.capture_mode,
            "carpetBoost": self.carpet_boost,
            "clean_score_id": self.clean_score_id,
            "profile": self.cleaning_profile,
            "ecoCharge": self.eco_charge,
            "execute_in_place": self.execute_in_place,
            "gentleMode": self.gentle_mode,
            "heatedWater": self.heated_water,
            "edgeOnly": self.edge_only,
            "quiet": self.quiet,
            "manUpd": self.manual_update,
            "monitor_mode": self.monitor_mode,
            "noKOZ": self.no_koz,
            "noAutoPasses": self.no_auto_passes,
            "noPP": self.no_persistent_pass,
            "odoaMode": self.odoa_mode,
            "openOnly": self.open_only,
            "operatingMode": self.operating_mode,
            "padWashAfter": self.pad_wash_after,
            "padWashArea": self.pad_wash_area,
            "padWetness": self.pad_wetness.to_json() if self.pad_wetness is not None else None,
            "rankOverlap": self.rank_overlap,
            "replay_of": self.replay_of,
            "routine_type": self.routine_type,
            "room_confine": self.room_confine,
            "rot": self.rotate,
            "routine_modified": self.routine_modified,
            "schedHold": self.schedule_hold,
            "swScrub": self.scrub,
            "smart_clean_id": self.smart_clean_id,
            "speed": self.speed,
            "stream_on_route": self.stream_on_route,
            "suctionLevel": self.suction_level,
            "timebox": self.timebox_minutes,
            "trans": self.translate,
            "twoPass": self.two_pass,
            "vacHigh": self.vac_high,
            "vleft": self.velocity_left,
            "vright": self.velocity_right,
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
        if not isinstance(data, dict):
            return cls()
        pad_wetness_data = data.get("padWetness")
        pad_wetness = None
        if pad_wetness_data:
            pad_wetness = PadWetnessParam(
                disposable=pad_wetness_data.get("disposable"),
                pad_plate=pad_wetness_data.get("padPlate"),
                reusable=pad_wetness_data.get("reusable"),
            )
        return cls(
            adaptive_cleaning=data.get("adaptive"),
            bin_pause=data.get("binPause"),
            capture_mode=data.get("capture_mode"),
            carpet_boost=data.get("carpetBoost"),
            clean_score_id=data.get("clean_score_id"),
            cleaning_profile=data.get("profile"),
            eco_charge=data.get("ecoCharge"),
            execute_in_place=data.get("execute_in_place"),
            gentle_mode=data.get("gentleMode"),
            heated_water=data.get("heatedWater"),
            edge_only=data.get("edgeOnly"),
            quiet=data.get("quiet"),
            manual_update=data.get("manUpd"),
            monitor_mode=data.get("monitor_mode"),
            no_koz=data.get("noKOZ"),
            no_auto_passes=data.get("noAutoPasses"),
            no_persistent_pass=data.get("noPP"),
            odoa_mode=data.get("odoaMode"),
            open_only=data.get("openOnly"),
            operating_mode=data.get("operatingMode"),
            pad_wash_after=data.get("padWashAfter"),
            pad_wash_area=data.get("padWashArea"),
            pad_wetness=pad_wetness,
            rank_overlap=data.get("rankOverlap"),
            replay_of=data.get("replay_of"),
            routine_type=data.get("routine_type"),
            room_confine=data.get("room_confine"),
            rotate=data.get("rot"),
            routine_modified=data.get("routine_modified"),
            schedule_hold=data.get("schedHold"),
            scrub=data.get("swScrub"),
            smart_clean_id=data.get("smart_clean_id"),
            speed=data.get("speed"),
            stream_on_route=data.get("stream_on_route"),
            suction_level=data.get("suctionLevel"),
            timebox_minutes=data.get("timebox"),
            translate=data.get("trans"),
            two_pass=data.get("twoPass"),
            vac_high=data.get("vacHigh"),
            velocity_left=data.get("vleft"),
            velocity_right=data.get("vright"),
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
    #: `region_name` and `region_type` -- NEW IN APP 3.0.0's `RegionDTO`
    #: and absent from 2.2.4, so this model had no way to know of them.
    #:
    #: Note the collision: the wire key `region_type` is NOT this
    #: dataclass's `region_type`, which serialises as `type`. iRobot
    #: added a second, differently-named concept beside the existing
    #: one. Both are written when set.
    #:
    #: Their purpose is unconfirmed -- a name alongside an id suggests
    #: the app sends what it displayed, so a robot could echo it back in
    #: the timeline. Nothing here depends on that reading; the fields
    #: are carried, not interpreted.
    region_label: str | None = None
    region_kind: str | None = None
    name: str | None = None
    params: CommandParams | None = None

    def to_json(self) -> dict[str, Any]:
        # "region_id", SETTLED BY FIELD DATA (DaRealGuGu, a26).
        #
        # This used to emit "id", and the docstring above recorded the
        # open question honestly: reads showed "region_id", writes were
        # assumed to want "id". Two confirmed-working region commands
        # settle it -- both carried "region_id", and the robot echoed
        # them back unchanged in its own mission timeline.
        #
        # The from-scratch command (stage 3), which still emitted "id",
        # was delivered with a PUBACK and did nothing at all. Same
        # robot, same map, same room, minutes apart.
        # AN EMPTY REGION ID IS NOT A REGION.
        #
        # `from_json` turns a server-sent `null` into `""` -- a default
        # that a `.get(key, "")` cannot distinguish from an absent key,
        # which is a recurring shape in this codebase. The result is a
        # command that names a room and does not.
        #
        # @Echovictor37 showed what an under-addressed command does: a
        # missing map id produced a PUBACK and a WHOLE-HOUSE clean. This
        # is the same failure one field over, and refusing is the only
        # honest answer -- we cannot target a region we cannot name.
        if not self.region_id:
            raise ValueError(
                "region_id is empty, so this command names no room. A "
                "region command with no id is accepted by the robot and "
                "does something other than what was asked."
            )
        body: dict[str, Any] = {"region_id": self.region_id, "type": self.region_type.value}
        if self.region_label is not None:
            body["region_name"] = self.region_label
        if self.region_kind is not None:
            body["region_type"] = self.region_kind
        if self.name is not None:
            body["name"] = self.name
        if self.params is not None:
            body["params"] = self.params.to_json()
        return body

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Region:
        if not isinstance(data, dict):
            return cls()
        params_data = data.get("params")
        return cls(
            region_id=data.get("region_id") or data.get("id", ""),
            region_type=_enum_or_none(RegionType, data.get("type")) or RegionType.RID,
            name=data.get("name"),
            # `region_name` IS WHERE ZONE NAMES ACTUALLY LIVE.
            #
            # We wrote this field and never read it back. APK 3.0.0:
            # `IrobotTimelineRegionNameResolver` reads
            # `cmd.regions[].region_name` -- the timeline events
            # themselves carry no name (`RobotTimelineZone` has only
            # `zid`).
            #
            # So a zone's label comes from the COMMAND that cleaned it,
            # not from the map. @chairstacker's `--list-rooms` shows
            # `name=None` for every ZID while his app timeline reads
            # "Guest Access Zone" and "Living Room @Wall" -- both true
            # at once, because they are different places.
            #
            # `data.get("name")` above is the map-side name and stays.
            region_label=data.get("region_name"),
            params=CommandParams.from_json(params_data) if params_data else None,
        )


# WHETHER THE ROBOT IS VACUUMING OR MOPPING RIGHT NOW: not on the wire.
#
# Asked because a tester scheduled "vacuum then mop" and both halves
# reported `cycle: clean` -- nothing distinguished them, so Home
# Assistant could not say which one was running.
#
# APK analysis found the app's answer and it is a COMPUTED value, not a
# field. `ResolvedDetailedMissionStatus` distinguishes:
#
#     25  CleaningVacuuming
#     26  CleaningMopping
#     27  CleaningComboVacuumAndMop
#     28  CleaningComboMopAndScrub
#     29  CleaningComboVacuumMopAndScrub
#     30  CleaningMopOnly
#     39  CleaningPadWashing
#     10  CleaningPadWetOut
#
# matching UI strings "Vacuuming %s", "Mopping %s", "Mopping with
# SmartScrub %s". But `RobotStatusV2` as a whole is reducer output: the
# app derives it from raw values rather than reading it.
#
# THE RAW INGREDIENT WE HAVE is cleanMissionStatus.operatingMode, and
# the APK gives a partial decoding:
#
#     2   Vacuuming
#     4   MopOnly
#     32  VacMopComboOnly
#     512 VacThenMop
#
# TWO REASONS NOT TO BUILD ON IT YET.
#
# First, a real capture reports operatingMode 6, which is in none of
# those categories. As a bitmask 6 is 2|4 -- vacuum plus mop-only, which
# is contradictory as a single mode. So either the values are not flags,
# or the decoding is incomplete.
#
# Second, both captures showing a value have `cycle: none` and `phase:
# charge` -- the robots were docked. So operatingMode may describe the
# NEXT or LAST mission rather than a current one, and nobody has a
# capture from mid-mission to tell the difference.
#
# SETTLED 1 August 2026 (@jouwdan, W155042): operatingMode does NOT
# describe the current activity.
#
# He captured the same robot twice:
#
#     docked    cycle=none   phase=charge  operatingMode=2
#     cleaning  cycle=clean  phase=run     operatingMode=2
#
# `cycle` and `phase` moved; operatingMode did not.
#
# HIS ROBOT IS VACUUM-ONLY, so on its own that is ambiguous -- a robot
# that can only vacuum would report "vacuuming" either way. The docked
# capture is what decides it: a robot sitting on the dock CHARGING is
# not vacuuming, and the field said 2 anyway.
#
# So operatingMode carries the mission's configured mode, not what the
# robot is doing at this second. It cannot answer "vacuuming or mopping
# right now" on any robot.
#
# CORRECTED SAME DAY (@DaRealGuGu, N185240). The conclusion above holds
# for a vacuum-only robot and is wrong as a general statement: on a
# Combo the value DOES move.
#
# Three captures from one robot:
#
#     docked, idle                      operatingMode = 2
#     pad washing                       operatingMode = 6
#     scheduled combo mission running   operatingMode = 6
#
# And the scheduled mission's own command carries a DIFFERENT number in
# its region parameters:
#
#     regions[].params.operatingMode = 32   (VacMopComboOnly)
#     cleanMissionStatus.operatingMode = 6
#
# SO THERE ARE TWO SEPARATE USES OF THE SAME NAME, and conflating them
# is what made 6 look impossible:
#
#   - IN A COMMAND it names the requested job. 32 asks for a combined
#     vacuum-and-mop run.
#   - IN THE MISSION STATUS it is a bitmask of what is currently
#     engaged. 6 is 2|4 -- vacuuming and mopping at once, which is
#     exactly what a combo job executes as.
#
# That also explains jouwdan's vacuum-only robot reading 2 while
# charging: with only one bit available it has nothing else to report,
# so the value looks static when it is not.
#
# FULLY SETTLED (@DaRealGuGu, 1 Aug 2026) with a capture taken during
# the MOPPING half of a scheduled vacuum-then-mop run:
#
#     command  regions[].params.operatingMode = 512   (VacThenMop)
#     status   cleanMissionStatus.operatingMode = 4   (MopOnly)
#
# The whole sequence from one robot:
#
#     docked, idle       phase=charge   mode=2   vacuum
#     pad washing        phase=padWash  mode=6   combo
#     combo running      phase=run      mode=6   2|4, both engaged
#     mopping half       phase=run      mode=4   mop only
#
# So the status field DOES track the current activity, including inside
# a two-phase job. "Is it vacuuming or mopping right now" is answerable:
#
#     2  vacuuming
#     4  mopping
#     6  both at once
#
# The command number is separate and stays separate: 512 asks for
# vacuum-then-mop, 32 for a combined run, and neither ever appears in
# the status field.
#
# PART OF THE ANSWER IS ALREADY VISIBLE, from a different direction.
# The app's RobotMissionPhase has twelve values including PadWashing and
# Refilling, and two of those ARE on the wire as separate dock fields:
# `dock.pwState` and `dock.pdState`, both fully modelled here as
# DockState members and both already surfaced as sensors.
#
# So "the robot is having its pad washed" is answerable today; "the
# robot is mopping rather than vacuuming" is not. Worth knowing before
# anyone treats the whole question as blocked -- the dock half of it is
# not.
