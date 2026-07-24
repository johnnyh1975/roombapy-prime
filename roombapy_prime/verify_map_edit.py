"""Manual, observed verification of map editing against a real Prime/V4
robot's actual, in-use map.

DELIBERATELY SEPARATE from diagnostics.py, for the same reason
diagnostics.py itself NEVER edits maps automatically, and for the same
reason verify_mission_commands.py exists as its own script rather than
being folded into diagnostics.py: this actually changes real, saved
data about a robot the person genuinely uses. It exists only because
there needs to be a way to test that ONCE, deliberately, while
watching -- not to make the automated diagnostics script "safer".

WHY THIS IS MORE CAUTIOUS THAN verify_mission_commands.py, DELIBERATELY:

Mission commands, before their own live test, had two independently
converging pieces of evidence for the correct transport (this
project's own native disassembly, and a third-party project reporting
success). Map editing (`edit_map()`, the V1 command family) had NO
such corroboration until this session: the exact JSON envelope around
each command's `to_v1_command_body()` output (see models/map_editing.py's
V1 section) was originally an ANALOGY assumption from the V2 pattern,
never independently confirmed -- and a first live test against that
assumption (chairstacker) failed with HTTP 500, prompting a full live
APK decompilation of EditMapV1Request.java this session, which
confirmed the real envelope shape is structurally different
("command"/"params"-nested, not "type"/flat) across all nine V1
commands.

UPDATE: the corrected structure has now been LIVE-CONFIRMED, not just
decompiled -- a second run (chairstacker, same session as the fix)
renamed a real room ("Master Bathroom" -> "Master Bathroom
[roombapy-prime-test]"), confirmed in the real app, then reverted it
back, also confirmed in the app. See SetRoomMetadataV1's own docstring
in models/map_editing.py. This script's caution level remains
unchanged regardless -- this confirms the envelope shape and
SetRoomMetadataV1 specifically, not the other eight V1 command types
(SplitRoom, MergeRooms, SetPermanentAreas, DeletePermanentAreas,
SetVirtualWalls, AdjustFurniture, RenameRoomV1, SetRoomTypeV1), none of
which have been live-tested at all yet.

For that reason, THIS SCRIPT DELIBERATELY ONLY TESTS ONE COMMAND TYPE
(SetRoomMetadataV1), on either of its two fields: renaming an
existing, already-named room to a clearly-marked test name and
immediately renaming it back (the default), OR changing an existing
room's category to a different one and immediately changing it back
(--test-category, added once the rename direction was confirmed
working -- see RoomMetadataEntry.category's own docstring for the
matching read-side field this needed). Nothing else from the V1
vocabulary (SplitRoom, MergeRooms, SetPermanentAreas,
DeletePermanentAreas, SetVirtualWalls, AdjustFurniture, the deprecated
RenameRoomV1/SetRoomTypeV1 pair) is attempted here -- those either
aren't cleanly reversible at all (a merge/split can't be undone by
calling the inverse operation, since the original boundary information
is gone), carry meaningfully higher risk for a first live test, or (the
deprecated pair specifically) aren't even the current app's own path
anymore. A successful rename test is useful evidence about the
general V1 envelope shape and about SetRoomMetadataV1 specifically
(now confirmed twice), but does NOT by itself confirm any of the other
command types.

SAFETY DESIGN (same doubly-secured pattern as verify_mission_commands.py):
1. --i-understand-this-will-edit-my-map must be explicitly set at
   startup, or the script aborts immediately, before it even logs in.
2. An interactive confirmation is asked before EVERY step that changes
   anything (the test rename, and separately the revert) -- including
   a display of exactly what's about to be sent, and which room
   (by its current, real name) is about to be affected.

FLOW:
  1. Fetch active map versions, then download+parse each one's map
     bundle to list rooms that currently HAVE a name (room names come
     exclusively from the map bundle, not from get_active_map_versions()
     itself -- confirmed via APK decompilation this session, see
     _fetch_bundle_rooms()'s docstring). A room with no name is skipped
     entirely for this test -- see _pick_test_room()'s docstring for
     why: there'd be no reliable way to revert it back to "no name"
     afterward.
  2. Let the user choose which named room to test on, showing the
     room_id and current name explicitly.
  3. Rename it to "{original name} [roombapy-prime-test]".
  4. Ask the user to confirm in the real app that the name actually
     changed -- an accepted HTTP response is not proof the edit had
     any real effect, only that the server didn't reject the request.
  5. Immediately rename it back to the original name, and ask for the
     same confirmation.

The result is summarized as a markdown report just like
verify_mission_commands.py, including a pre-filled GitHub issue link
and --dump-config support (same redaction logic).

USAGE:
  roombapy-prime-verify-map-edit \\
      --username you@example.com --country-code US --blid BLID123 \\
      --i-understand-this-will-edit-my-map

Credentials same as diagnostics.py: ROOMBAPY_PRIME_PASSWORD env var
or interactive prompt, never as a command-line argument.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import webbrowser
from typing import Any

import aiohttp

from .diagnostics import Report, _redact_raw_capture, _report_topic_prefix_status, build_issue_url
from .models import (
    P2MapVersion,
    RoomCategory,
    RoomFeature,
    SetRoomMetadataV1,
    parse_active_map_versions,
    parse_map_bundle,
)
from .prime_factory import PrimeFactory
from .verify_mission_commands import _confirm

_TEST_SUFFIX = " [roombapy-prime-test]"


async def _fetch_bundle_rooms(robot: Any, typed_versions: list[P2MapVersion]) -> list[tuple[str, RoomFeature]]:
    """Downloads and parses the map bundle for every active map version,
    returning (p2map_id, RoomFeature) pairs for every room feature
    found -- REPLACES the old get_active_map_versions()-based room
    lookup entirely (this session).

    WHY THE SWITCH: a full APK decompilation, prompted by jadestar1864's
    real capture showing named rooms nested under
    get_active_map_versions()'s own "rooms_metadata" (see the removed
    _pick_test_room() docstring's history in git blame for that
    capture), found that the app itself never reads room names from
    this endpoint at all -- at ANY level of richness. The REST call
    behind get_active_map_versions() (fetchActiveVersions()) actually
    deserializes to a List<P2MapData> (package
    com.irobot.irobotdata.maps.internal.p2maps.editing.common.responses)
    with a confirmed, complete field list (create_time/last_p2mapv_ts/
    p2map_id/active_p2mapv_id/name [the MAP name, not room-specific]/
    state/user_orientation_rad/visible) -- no room-metadata field
    anywhere. The app then further reduces this to just id+version
    (P2MapIdentifier) for its own internal use. Neither class declares
    anything room-related.

    ONE OPEN CAVEAT, not fully closeable by decompilation alone:
    kotlinx.serialization silently ignores unknown JSON keys by
    default, so it remains theoretically possible the server sends
    additional fields (e.g. a real "rooms_metadata") that the app's own
    model simply never declares and therefore never reads or displays.
    This script deliberately doesn't rely on that possibility: even if
    the server-side data were real, there'd be no way to know whether
    it stays in sync with what the app actually shows the user -- and
    this script needs a name it can safely revert to. The map bundle's
    RoomFeature/RoomFeatureProperties (models/map_bundle.py), by
    contrast, is bytecode-confirmed as something the app itself reads
    and displays.

    Geometry fields are read into the returned RoomFeature objects
    (parse_map_bundle() itself returns raw dicts; RoomFeature.from_json()
    is applied here) but never printed/logged by this script -- see
    diagnostics.py's map bundle handling for the same "a floor plan is
    more personal than most other data" rule."""
    rooms: list[tuple[str, RoomFeature]] = []
    for version in typed_versions:
        p2map_id = getattr(version, "p2map_id", None)
        p2mapv_id = getattr(version, "active_p2mapv_id", None)
        if not (p2map_id and p2mapv_id):
            continue
        try:
            link = await robot.get_map_geojson_link(p2map_id, p2mapv_id)
            # CONFIRMED (session 48): "map_url" via P2MapURL$$serializer.
            url = link.get("map_url") or next(
                (v for v in link.values() if isinstance(v, str) and v.startswith("http")), None
            )
            if not url:
                continue
            bundle_bytes = await robot.download_map_bundle(url)
            parsed = parse_map_bundle(bundle_bytes)
        except Exception as exc:  # noqa: BLE001
            print(f"  (map bundle check for {p2map_id!r} failed: {type(exc).__name__}: {exc})")
            continue
        rooms_file = parsed.get("rooms")
        if not isinstance(rooms_file, list):
            continue
        for entry in rooms_file:
            if isinstance(entry, dict):
                rooms.append((p2map_id, RoomFeature.from_json(entry)))
    return rooms


def _pick_test_room(rooms: list[tuple[str, RoomFeature]]) -> tuple[str, str, str] | None:
    """Returns (p2map_id, room_id, current_name) for the first named
    room found across all fetched map bundles, or None if no named
    room exists anywhere. See _fetch_bundle_rooms() for why this reads
    from the map bundle rather than get_active_map_versions() (this
    session's finding).

    Deliberately requires an EXISTING name, not just any room: this
    script's whole safety design rests on being able to revert to a
    known-good original value. RenameRoomV1's `name` field is a plain
    required string (see models/map_editing.py) -- there's no confirmed way to
    "clear" a name back to none, so a room that currently has no name
    is not a safe candidate for this test at all, regardless of how
    the test itself goes."""
    for p2map_id, feature in rooms:
        name = feature.properties.name
        room_id = feature.feature_id
        if name and room_id:
            return p2map_id, room_id, name
    return None


async def run(username: str, password: str, country_code: str, blid: str) -> tuple[Report, dict[str, Any]]:
    report = Report()
    raw_capture: dict[str, Any] = {}

    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")
        await robot.connect()
        report.add("MQTT connection", "OK")

        _report_topic_prefix_status(report, robot)
        raw_capture["Discovery deployment object (for irbt_topic_prefix)"] = robot.deployment

        print("\n== Finding a room to test on ==")
        try:
            map_versions = await robot.get_active_map_versions()
        except Exception as exc:  # noqa: BLE001
            report.add("Fetching active map versions", "FAILED", f"{type(exc).__name__}: {exc}")
            await robot.disconnect()
            return report, raw_capture
        report.add("Fetching active map versions", "OK", f"{len(map_versions)} map version(s) found")
        raw_capture["Active map versions"] = [getattr(v, "__dict__", str(v)) for v in map_versions]

        typed_versions = parse_active_map_versions(map_versions)

        # SOURCE SWITCH (this session): room names now come exclusively
        # from the downloaded map bundle, not get_active_map_versions().
        # A full APK decompilation confirmed the app itself never reads
        # room names from that endpoint, at any level of richness (the
        # underlying REST response class, P2MapData, has no room field
        # either) -- see _fetch_bundle_rooms()'s docstring for the full
        # evidence trail and the one remaining theoretical caveat.
        print(
            "\nFetching the map bundle to find a named room -- get_active_map_versions() itself "
            "never carries room names (confirmed via APK decompilation, see _fetch_bundle_rooms()'s "
            "docstring). Geometry/polygon fields are read but never printed or captured here."
        )
        bundle_rooms = await _fetch_bundle_rooms(robot, typed_versions)
        picked = _pick_test_room(bundle_rooms)
        if picked is None:
            report.add(
                "Finding a named room to test on",
                "SKIPPED",
                f"{len(bundle_rooms)} room feature(s) found across all map bundles, but none had "
                "both a name and a room_id. This test needs a room to already have a name, so it "
                "has something known-good to revert back to. Nothing was sent.",
            )
            await robot.disconnect()
            return report, raw_capture
        p2map_id, room_id, original_name = picked
        report.add(
            "Finding a named room to test on", "OK",
            f"room_id={room_id!r}, current name={original_name!r} (p2map_id={p2map_id!r})",
        )

        test_name = f"{original_name}{_TEST_SUFFIX}"
        print(f"\n{'=' * 60}")
        print(f"TEST ROOM: {original_name!r} (room_id={room_id})")
        print(f"About to temporarily rename it to: {test_name!r}")
        print(
            "Using SetRoomMetadataV1 (command='set_room_metadata') -- LIVE-CONFIRMED: a "
            "previous run of this exact test (chairstacker) succeeded, confirmed in the real "
            "app, both the rename and the revert back. A prior attempt before that, with the "
            "OLD, incorrectly-shaped RenameRoom envelope, had failed with HTTP 500 -- fixed "
            "via full APK decompilation of EditMapV1Request.java, then confirmed working live."
        )
        if not _confirm("Proceed with the rename?"):
            report.add("Rename room (test)", "SKIPPED", "not confirmed by user")
            await robot.disconnect()
            return report, raw_capture

        try:
            result = await robot.edit_map(p2map_id, SetRoomMetadataV1(room_id=room_id, name=test_name))
            raw_capture["edit_map response (rename to test name)"] = result
            print(f"  Server response: {result}")
        except Exception as exc:  # noqa: BLE001
            report.add("Rename room (test)", "FAILED", f"{type(exc).__name__}: {exc}")
            print(
                "\nThe rename call itself failed -- this is actually useful, safe information "
                "(the envelope guess is likely wrong), not a dangerous outcome. Not attempting "
                "the revert, since nothing should have changed."
            )
            await robot.disconnect()
            return report, raw_capture

        renamed_confirmed = _confirm(
            f'Please check the real app now: does the room show as "{test_name}"? (y/n)'
        )
        if renamed_confirmed:
            report.add("Rename room (test)", "OK", f"confirmed by user in the real app: now {test_name!r}")
        else:
            report.add(
                "Rename room (test)", "FAILED",
                "server accepted the request without error, but the name did NOT actually change "
                "in the app -- the envelope is likely being silently ignored server-side",
            )

        print(f"\n{'=' * 60}")
        print(f"Reverting room_id={room_id} back to its original name: {original_name!r}")
        if not _confirm("Proceed with the revert?"):
            report.add(
                "Revert room name", "SKIPPED",
                f"NOT confirmed by user -- room_id={room_id} may still show the test name "
                f"{test_name!r}. Please revert this manually in the app.",
            )
            await robot.disconnect()
            return report, raw_capture

        try:
            result = await robot.edit_map(p2map_id, SetRoomMetadataV1(room_id=room_id, name=original_name))
            raw_capture["edit_map response (revert to original name)"] = result
            print(f"  Server response: {result}")
        except Exception as exc:  # noqa: BLE001
            report.add(
                "Revert room name", "FAILED",
                f"{type(exc).__name__}: {exc} -- room_id={room_id} may still show the test name "
                f"{test_name!r}. Please revert this manually in the app.",
            )
            await robot.disconnect()
            return report, raw_capture

        reverted_confirmed = _confirm(
            f'Please check the real app again: is the room back to "{original_name}"? (y/n)'
        )
        if reverted_confirmed:
            report.add("Revert room name", "OK", f"confirmed by user in the real app: back to {original_name!r}")
        else:
            report.add(
                "Revert room name", "FAILED",
                f"server accepted the revert without error, but the app does NOT show "
                f"{original_name!r} -- room_id={room_id} may need manual correction in the app",
            )

        await robot.disconnect()

    return report, raw_capture


def _pick_test_room_with_category(rooms: list[tuple[str, RoomFeature]]) -> tuple[str, str, str, RoomCategory] | None:
    """Returns (p2map_id, room_id, current_name, current_category) for
    the first bundle room found with BOTH a name (for display/
    identification) AND a category value that actually parses as a
    known RoomCategory -- same bundle source switch as _pick_test_room()
    (this session), for the same reason (see _fetch_bundle_rooms()).

    THE ADDED WRINKLE HERE, specific to category: RoomFeatureProperties.
    room_type (models/map_bundle.py) is deliberately left as a raw,
    unconfirmed value -- only its FIELD NAME is bytecode-confirmed, not
    which value space it uses (a human-readable string enum? the same
    numeric codes as the unrelated, edit-side RoomType? something else
    entirely?). RoomCategory (models/enums_common.py) is the confirmed
    WRITE-side enum for SetRoomMetadataV1 specifically -- there's no
    confirmation the read-side room_type uses the same value space at
    all. Rather than assume they match, this function tries to parse
    room_type as a RoomCategory and simply SKIPS the room if that
    fails -- a wrong guess here would write back a category on revert
    that doesn't match what the room actually had, which is exactly
    the class of mistake this script's whole design exists to avoid."""
    for p2map_id, feature in rooms:
        name = feature.properties.name
        room_id = feature.feature_id
        raw_type = feature.properties.room_type
        if not (name and room_id and raw_type is not None):
            continue
        try:
            category = RoomCategory(raw_type)
        except (ValueError, TypeError):
            continue
        return p2map_id, room_id, name, category
    return None


async def run_category_test(username: str, password: str, country_code: str, blid: str) -> tuple[Report, dict[str, Any]]:
    """Stage: change a room's CATEGORY (not name) via SetRoomMetadataV1,
    the same LIVE-CONFIRMED command already used for renaming -- this
    is NOT a new, untested command, just a second field on one that's
    already been observed to actually work against a real robot in
    both directions. Same capture-then-revert safety pattern as the
    rename test above."""
    report = Report()
    raw_capture: dict[str, Any] = {}

    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")
        await robot.connect()
        report.add("MQTT connection", "OK")

        _report_topic_prefix_status(report, robot)

        print("\n== Finding a room with a known category to test on ==")
        try:
            map_versions = await robot.get_active_map_versions()
        except Exception as exc:  # noqa: BLE001
            report.add("Fetching active map versions", "FAILED", f"{type(exc).__name__}: {exc}")
            await robot.disconnect()
            return report, raw_capture

        typed_versions = parse_active_map_versions(map_versions)

        # SOURCE SWITCH (this session) -- same reasoning as run()'s own
        # comment: room data comes from the map bundle now, never from
        # get_active_map_versions(). See _fetch_bundle_rooms()'s docstring.
        bundle_rooms = await _fetch_bundle_rooms(robot, typed_versions)
        picked = _pick_test_room_with_category(bundle_rooms)
        if picked is None:
            report.add(
                "Finding a room with a known category", "SKIPPED",
                f"{len(bundle_rooms)} room feature(s) found across all map bundles, but none had "
                "both a name and a room_type that parses as a known RoomCategory (see "
                "_pick_test_room_with_category()'s docstring on why a non-matching value is "
                "skipped rather than guessed at)",
            )
            await robot.disconnect()
            return report, raw_capture
        p2map_id, room_id, room_name, original_category = picked
        report.add(
            "Finding a room with a known category", "OK",
            f"room_id={room_id!r} ({room_name!r}), current category={original_category!r}",
        )

        # Pick any OTHER category as the temporary test value, deterministically.
        other_categories = [c for c in RoomCategory if c != original_category]
        test_category = other_categories[0]

        print(f"\n{'=' * 60}")
        print(f"TEST ROOM: {room_name!r} (room_id={room_id})")
        print(f"Current category: {original_category!r} -> temporarily changing to: {test_category!r}")
        print(
            "Using SetRoomMetadataV1 (command='set_room_metadata') -- the SAME command already "
            "LIVE-CONFIRMED for renaming, just its other field (room_type/category instead of name)."
        )
        if not _confirm("Proceed with the category change?"):
            report.add("Change room category (test)", "SKIPPED", "not confirmed by user")
            await robot.disconnect()
            return report, raw_capture

        try:
            result = await robot.edit_map(p2map_id, SetRoomMetadataV1(room_id=room_id, room_type=test_category))
            raw_capture["edit_map response (category change)"] = result
            print(f"  Server response: {result}")
        except Exception as exc:  # noqa: BLE001
            report.add("Change room category (test)", "FAILED", f"{type(exc).__name__}: {exc}")
            await robot.disconnect()
            return report, raw_capture

        changed_confirmed = _confirm(
            f"Please check the real app now: does the room show category {test_category.value!r}? (y/n)"
        )
        if changed_confirmed:
            report.add("Change room category (test)", "OK", f"confirmed by user in the real app: now {test_category!r}")
        else:
            report.add(
                "Change room category (test)", "FAILED",
                "server accepted the request without error, but the category did NOT actually "
                "change in the app -- the envelope is likely being silently ignored server-side",
            )

        print(f"\n{'=' * 60}")
        print(f"Reverting room_id={room_id} back to its original category: {original_category!r}")
        if not _confirm("Proceed with the revert?"):
            report.add(
                "Revert room category", "SKIPPED",
                f"NOT confirmed by user -- room_id={room_id} may still show {test_category!r}. "
                "Please revert this manually in the app.",
            )
            await robot.disconnect()
            return report, raw_capture

        try:
            result = await robot.edit_map(p2map_id, SetRoomMetadataV1(room_id=room_id, room_type=original_category))
            raw_capture["edit_map response (category revert)"] = result
            print(f"  Server response: {result}")
            report.add("Revert room category", "OK", f"reverted to {original_category!r}")
        except Exception as exc:  # noqa: BLE001
            report.add("Revert room category", "FAILED", f"{type(exc).__name__}: {exc}")

        await robot.disconnect()

    return report, raw_capture


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manual, observed verification of map editing (room rename OR category, both via "
            "SetRoomMetadataV1 -- see the module docstring for why the OTHER eight V1 command "
            "types remain untested) against a REAL Prime/V4 robot's REAL, in-use map."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument(
        "--blid",
        default=os.environ.get("ROOMBAPY_PRIME_BLID"),
        help="The exact target device must be chosen deliberately, no 'first device found'. "
        "Falls back to ROOMBAPY_PRIME_BLID env var.",
    )
    parser.add_argument(
        "--i-understand-this-will-edit-my-map",
        action="store_true",
        dest="confirmed",
        help="Required. Without this flag the script aborts immediately, before any login.",
    )
    parser.add_argument("--output", default=None, metavar="PATH")
    parser.add_argument("--dump-config", default=None, metavar="PATH")
    parser.add_argument("--no-issue-link", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument(
        "--test-category", action="store_true",
        help="Test room CATEGORY (not name) instead -- same LIVE-CONFIRMED SetRoomMetadataV1 "
        "command, its other field. Deprecated SetRoomTypeV1 is NOT tested by this script at "
        "all (the current app doesn't use it anymore, see the module docstring).",
    )
    args = parser.parse_args()
    if not args.blid:
        print("Aborted: --blid is required (or set the ROOMBAPY_PRIME_BLID env var).")
        sys.exit(1)

    if not args.confirmed:
        print(
            "Aborted: --i-understand-this-will-edit-my-map is missing. This script edits a REAL "
            "room name on a REAL device's REAL map -- see the module docstring."
        )
        sys.exit(1)

    username = args.username or input("iRobot account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Password: ")

    print(f"\nTARGET DEVICE: {args.blid}")
    if args.test_category:
        print("This script is about to temporarily change one room's CATEGORY on this device's")
        print("real map, then change it back. Same command already confirmed for renaming.")
    else:
        print("This script is about to temporarily rename one room on this device's real map, then")
        print("rename it back. See the module docstring for why only this one operation is tested.")
    if not _confirm("Continue?"):
        print("Aborted.")
        sys.exit(0)

    if args.test_category:
        report, raw_capture = asyncio.run(run_category_test(username, password, args.country_code, args.blid))
    else:
        report, raw_capture = asyncio.run(run(username, password, args.country_code, args.blid))
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Summary: {ok} OK, {failed} failed, {skipped} skipped ==")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Report saved to {args.output}")

    if args.dump_config:
        import json

        redacted = _redact_raw_capture(raw_capture, [username, password])
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(redacted, f, indent=2, default=str, ensure_ascii=False)
        print(f"Redacted raw responses saved to {args.dump_config}")

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Feedback for the maintainers ==")
        print("If you'd like to share this report:")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


if __name__ == "__main__":
    main()
