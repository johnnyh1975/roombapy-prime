#!/usr/bin/env python3
"""Every value set in this library, checked against the vendor's own.

WHY THIS EXISTS, stated plainly because the reason is a mistake made
repeatedly and not a hypothetical.

The APK research produced 607 vendor enums. Working from it by SEARCHING
-- grepping for the name you expect -- fails in one specific way: you
look for `padDryDur`, find nothing, and conclude no vendor set exists.
The set was there under `DryDurType`. The same session invented labels
`off/low/high` for `pwHeat` while `HeatType` said `noHeat/defaultHeat/
highHeat`, and shipped `pwHeat` ungated while `DockPadWashingType`
defined exactly which docks offer which levels.

Three misses, one afternoon, all the same shape: the vendor names things
by CONCEPT and the wire names them by FIELD, so searching by field name
finds nothing and absence reads as proof.

So this check does not ask "did you look?". It asks two questions that
cannot be answered by looking:

  1. For every value set with a declared vendor enum -- do they match?
  2. For every value set WITHOUT one -- is that absence declared?

The second is the one that would have caught all three. An undeclared
set fails, and the only way to pass is to state, in writing, which enum
it came from or why none exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Library enum -> vendor enum whose values it must match.
#:
#: Only enums whose members carry WIRE VALUES belong here. An enum that
#: exists purely to name numbers this library reads (RoomStatus and its
#: neighbours) is checked the same way -- its numbers are the vendor's.
CHECKED: dict[str, str] = {
    "CleaningProfileType": "ProfileType",
    "RegionType": "IrobotRegionType",
    "RoomStatus": "RoomEvent.RoomStatus",
    "TravelReason": "TravelEvent.TravelReason",
    "TravelStatus": "TravelEvent.TravelStatus",
    "PadWashReason": "PadWashEvent.PadWashReason",
    "WetOutStatus": "WetOutEvent.WetOutStatus",
    # FOUND BY MATCHING VALUE SETS, not by looking up a field name --
    # nobody would have searched for "EditMapV2Request.RoomType".
    "RoomType": "EditMapV2Request.RoomType",
    # THE DOCK CAPABILITY FAMILY, surfaced by vendor_gap_report.py
    # rather than by anyone thinking to look. `dock.cap` was six opaque
    # integers described in code as "levels, not flags" with no idea
    # what the levels meant; the vendor names every one.
    "ScrubSupport": "ScrubSupport",
    "PointCleanSupport": "CapDSpot",
    "MidMissionAdjustments": "MidMissionCleanAdjustmentsType",
    "DockEvacuation": "DockEvacuationType",
    "DockPadDrying": "DockPadDryingType",
    "DockPadWashing": "DockPadWashingType",
    "DockPadWetOut": "DockPadWetOutType",
    "DockFluidRefill": "DockFluidRefillType",
    "DockDetergent": "DockDetergentType",
    "MapEditingError": "P2MapEditingErrorCode",
    "MapEditStatus": "MapEditResult",
    "MapVerifyResult": "MapVerifyResult",
    "RoomTypeValue": "IrobotP2MapRoomTypeValue",
    "RoomTypeSourceValue": "IrobotP2MapRoomTypeSource",
    "MissionType": "MissionType",
    "TimelineEventPhase": "RobotTimelineEventPhase",
    "FaultScene": "FaultScene",
}

#: Value sets that match a vendor enum EXCEPT for named members, with
#: the reason each is excluded.
#:
#: A third category exists because the second would be a lie for these:
#: the vendor enum is real and ours does match it, apart from members
#: whose value the decompiler could not resolve. Filing them under "no
#: vendor enum" would discard a genuine mapping to avoid admitting a
#: gap in the extract.
PARTIAL: dict[str, tuple[str, frozenset[str], str]] = {
    "Initiator": (
        "Initiator",
        frozenset({"GigyaDefinitions.Providers.GOOGLE"}),
        "The Google member's value is an unresolved constant reference "
        "in the extract, not a literal. Shipping it as a wire value "
        "would ship the decompiler's placeholder; omitting it loses "
        "nothing, since `initiator` stays a str and an unrecognised "
        "value passes through.",
    ),
}

#: Value sets with NO vendor enum, each with the reason it has none.
#:
#: Being on this list is a claim, and a wrong one is worse than an
#: unchecked set: it tells the next person the question is settled.
#: "I searched for the field name and found nothing" is NOT a reason --
#: that is precisely the failure this file exists to prevent.
NO_VENDOR_ENUM: dict[str, str] = {
    "MissionCommandType": (
        "Spans Classic and Prime. The Prime half is confirmed from "
        "mission_model.toPayload; the Classic values (clean, quick, "
        "wake, rechrg ...) predate the Prime app and appear in no "
        "vendor enum because that app never sends them."
    ),
    "DoneCode": (
        "Searched app 3.0.0 in full: no DoneCode enum under any name, "
        "and none of the nineteen values appears in a done-code "
        "context. Only 'ok' is confirmed, from real data."
    ),
    "CoverageStrategy": (
        "Searched in every casing: HYBRID_COVERAGE_PLANNER, "
        "hybridCoveragePlanner, hybrid_coverage_planner, and the key "
        "coverageStrategy. App 3.0.0 contains none of them. The values "
        "are Kotlin constant names and are marked unverified in code."
    ),
    "DockState": (
        "84 numeric codes from iRobot's own status spec. App 3.0.0 has "
        "no counterpart enum; the dock capability types "
        "(DockPadWashingType and friends) cover capabilities, not "
        "states."
    ),
    "SuctionLevel": (
        "An IntEnum: the wire carries the integer and the member names "
        "are labels only. The vendor's CleanWindSuction is a Picea "
        "enum for a different device class."
    ),
    "OperatingModeBitmask": (
        "A bitmask, not an enum of wire values. Individual bits are "
        "documented from cap.oMode decomposition; the codec that emits "
        "command values is described in the class docstring."
    ),
    # --- CONFIRMED FROM THE SEND PATH RATHER THAN AN ENUM ---
    #
    # These two ARE vendor-confirmed, just not by an enum: their values
    # appear as string literals in the functions that build the payload.
    # Code beats declaration, so they are settled -- there is simply no
    # enum for the checker to compare against.
    "RoutineTypeParam": (
        "All six values appear verbatim in routine.dart::toJson, the "
        "function that serialises a routine for sending. Uppercase and "
        "correct -- the send path decides, not the casing."
    ),
    "TravelDestination": (
        "dock/zone/room/poly appear in robot_meta_data.dart's timeline "
        "parsers. 'waypoint' appears nowhere in app 3.0.0 in any casing "
        "and remains inferred; noted as such in its docstring."
    ),
    # --- THIS PROJECT'S OWN VOCABULARY, NOT THE WIRE'S ---
    #
    # Presentation and API-surface enums. They never travel to a robot,
    # so a vendor enum would be the wrong thing to compare them against.
    "CleaningMode": "Public API vocabulary for callers; not a wire value.",
    "CleaningPasses": "Public API vocabulary; the wire field is twoPass, a bool.",
    "LiquidAmountLevel": "Public API vocabulary for pad wetness levels.",
    "SoftwareScrub": "Public API on/off wrapper over the swScrub integer.",
    "CarpetBoostSettings": (
        "Three-value API wrapper over carpetBoost. Value-set matching is "
        "meaningless for a 0/1/2 set -- it matches dozens of unrelated "
        "vendor enums by coincidence, which is why it is declared here "
        "rather than mapped."
    ),
    "MissionPreferenceSwitcherType": "This library's grouping of mission toggles.",
    "PlanType": "This library's routine-plan vocabulary.",
    "PlanUpcoming": "This library's target-kind vocabulary for planned stops.",
    "RobotReadinessState": (
        "Numeric readiness codes from iRobot's status spec, not an enum "
        "in app 3.0.0."
    ),
    "ResolvedMissionStatus": "Derived by this library from phase and cycle.",
    "TimeEstimateConfidence": (
        "Carries BOTH vocabularies on purpose -- 2.2.4's constant names "
        "and 3.0.0's Confidence wire values -- because which one a "
        "firmware sends is not established. A single-enum comparison "
        "cannot express that."
    ),
    "TimeEstimateTimeUnit": "Singular and plural spellings of a unit string.",
    # --- MAP BUNDLE, WHICH IS REST/GeoJSON AND NOT THE APP'S ENUMS ---
    "PolicyZoneCategory": (
        "GeoJSON bundle vocabulary, confirmed from real bundles. The "
        "app's map editing enums are a different surface."
    ),
    "RoomCategory": "GeoJSON bundle vocabulary, confirmed from real bundles.",
    "RoomTypeSource": (
        "Enum NAMES used as placeholder strings -- no wire string was "
        "ever seen. App 3.0.0's IrobotP2MapRoomTypeSource gives the "
        "numeric form instead, modelled as RoomTypeSourceValue; the "
        "string half remains unconfirmed."
    ),
    "FurnitureType": (
        "Numeric furniture ids from the map bundle. The app's "
        "MapFurnitureDataType names 25 kinds but is a different "
        "encoding, so comparing the two sets would assert a "
        "correspondence nobody has established."
    ),
    # --- SEARCHED AND ABSENT ---
    "RankOverlap": (
        "Searched app 3.0.0 for DEEP_CLEAN/DETAIL_CLEAN/EXTENDED_CLEAN "
        "in every casing and as a value set: absent. Kotlin constant "
        "names, unverified as wire values -- same standing as "
        "CoverageStrategy."
    ),
    "PadCategory": (
        "The @SerialName reading, confirmed on Prime by a real capture "
        "(detectedPad reads 'padPlate'). App 3.0.0 declares no "
        "PadCategory enum for the checker to compare against."
    ),
    "MapEditRejectionReason": (
        "Merged from six vendor enums -- CarpetInvalidReason, "
        "CleanZoneInvalidReason, KeepoutZoneInvalidReason, "
        "RoomMergeInvalidReason, RoomSplitUnavailableReason and "
        "ThresholdInvalidReason -- because they overlap heavily and a "
        "caller wants one vocabulary. No single vendor enum to compare "
        "against; each value is verbatim from one of the six."
    ),
    "MopInstallDetails": (
        "Values match the vendor's exactly except `invalid`, which the "
        "extract reports as 18446744073709551615 -- an unsigned reading "
        "of -1. Modelled as -1 because that is what the app means, so a "
        "value-set comparison cannot pass."
    ),
    "TraversalType": (
        "region/zone, from the mission timeline's traversal events. The "
        "vendor's TraversalDirection and TraversalEdgeBehavior are "
        "different concepts under a similar name -- a reminder that "
        "matching on the name alone would have produced a wrong answer "
        "here rather than no answer."
    ),
    "VacuumPowerLevel": (
        "Public API vocabulary. The vendor's nearest enum by name, "
        "BatteryPowerLevel, is about charge state and unrelated."
    ),
    # --- TWO THAT LOOKED LIKE MATCHES AND ARE NOT ---
    #
    # Both were proposed by value-set matching and both were rejected on
    # inspection: `Frequency` and `P2MapHazardInfo.HazardType` carry an
    # EMPTY wireValues map, so their members are Kotlin constant names.
    # A set of constant names matching ours proves the two lists agree,
    # not that either is what the server sends.
    #
    # This is the mistake that cost four wrong vocabularies earlier --
    # caught here by the tool proposing a match and the check rejecting
    # it, which is the intended sequence.
    "ScheduleFrequency": (
        "ONCE/WEEKLY/BI_WEEKLY/MONTHLY are confirmed from real schedules "
        "this project has read. The vendor's Frequency enum has an empty "
        "wireValues map, so it corroborates nothing on its own."
    ),
    "HazardType": (
        "P2MapHazardInfo.HazardType lists the same sixteen kinds but has "
        "an empty wireValues map -- constant names, not wire values. Our "
        "values come from real map bundles."
    ),
}


def _library_enums() -> dict[str, dict[str, object]]:
    import enum
    import importlib
    import inspect
    import pkgutil

    import roombapy_prime

    found: dict[str, dict[str, object]] = {}
    modules = [roombapy_prime]
    for info in pkgutil.walk_packages(roombapy_prime.__path__, "roombapy_prime."):
        if ".tests" in info.name:
            continue
        try:
            modules.append(importlib.import_module(info.name))
        except Exception:  # noqa: BLE001, S112
            continue
    for module in modules:
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, enum.Enum)
                and obj.__module__.startswith("roombapy_prime")
            ):
                found[obj.__name__] = {m.name: m.value for m in obj}
    return found


def main() -> int:
    from roombapy_prime.vendor_reference import (  # noqa: PLC0415
        VendorReferenceError,
        has_enum,
        wire_values,
    )

    library = _library_enums()
    problems: list[str] = []

    for lib_name, vendor_name in CHECKED.items():
        if lib_name not in library:
            problems.append(
                f"{lib_name}: declared as checked against {vendor_name}, "
                f"but no such enum exists in the library any more"
            )
            continue
        if not has_enum(vendor_name):
            problems.append(
                f"{lib_name}: declared against {vendor_name}, which is not "
                f"in vendor_reference.json"
            )
            continue
        # NORMALISED, because JSON has no int/str distinction the way
        # Python does and comparing raw values reported five false
        # disagreements on identical numbers.
        ours = {str(v) for v in library[lib_name].values()}
        try:
            theirs = {str(v) for v in wire_values(vendor_name)}
        except VendorReferenceError as err:
            problems.append(f"{lib_name}: {err}")
            continue
        if ours != theirs:
            problems.append(
                f"{lib_name} disagrees with {vendor_name}\n"
                f"      ours:   {sorted(map(str, ours))}\n"
                f"      vendor: {sorted(map(str, theirs))}"
            )

    for lib_name, (vendor_name, excluded, _why) in PARTIAL.items():
        if lib_name not in library:
            problems.append(f"{lib_name}: listed in PARTIAL but not in the library")
            continue
        if not has_enum(vendor_name):
            problems.append(f"{lib_name}: PARTIAL against unknown {vendor_name}")
            continue
        ours = {str(v) for v in library[lib_name].values()}
        theirs = {str(v) for v in wire_values(vendor_name)} - set(excluded)
        if ours != theirs:
            problems.append(
                f"{lib_name} disagrees with {vendor_name} beyond its declared "
                f"exclusions\n      only ours:   {sorted(ours - theirs)}\n"
                f"      only vendor: {sorted(theirs - ours)}"
            )

    undeclared = sorted(
        set(library) - set(CHECKED) - set(NO_VENDOR_ENUM) - set(PARTIAL)
    )
    for name in undeclared:
        problems.append(
            f"{name}: no vendor enum declared and no reason given. Add it "
            f"to CHECKED with the vendor enum it comes from, or to "
            f"NO_VENDOR_ENUM with why none exists. Searching for the "
            f"FIELD name is not a search -- the vendor names enums by "
            f"concept (padDryDur -> DryDurType, pwHeat -> HeatType)."
        )

    stale = sorted(
        (set(CHECKED) | set(NO_VENDOR_ENUM) | set(PARTIAL)) - set(library)
    )
    for name in stale:
        problems.append(f"{name}: listed here but no longer exists in the library")

    if problems:
        print("Vendor value-set check FAILED:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    from roombapy_prime.vendor_reference import _data  # noqa: PLC0415

    print(
        f"OK: {len(CHECKED)} value set(s) match their vendor enum, "
        f"{len(PARTIAL)} match with declared exclusions, "
        f"{len(NO_VENDOR_ENUM)} documented as having none, "
        f"{len(_data()['enums'])} vendor enums available to check against."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
