"""Staged test package for SetVirtualWallsV1 ("set_virtual_wall") --
virtual walls, keep-out zones, and no-mop zones, never tested live
before this script existed. Read models/map_editing.py's own
policy_zones_to_virtual_walls()/policy_zone_to_virtual_wall()
docstrings first for the full, confirmed categorization rule this
script depends on.

WHY THIS IS SAFE DESPITE BEING A "NEW OBJECT" COMMAND: a real field
report initially suggested SetVirtualWallsV1 might work by
add/delta semantics (only the changed object sent). Direct
confirmation from the real app's own deleteVirtualWall()
implementation settled this: it works by REPLACE semantics -- read
the CURRENT full list, remove/add the target, send the WHOLE list
back. That means the exact same "read current, resend unchanged"
stage-1 philosophy already used by every other staged script in this
project applies here too, and does NOT require understanding
CommandPolygon's own still-unconfirmed coordinate system at all --
existing coordinates are preserved byte-for-byte, never recomputed.

THE STAGED APPROACH:

  Stage 1 (--update-unchanged): downloads the current map bundle,
  reads policyZones.geojson, converts every entry to its correct
  VirtualWallV1 subtype (policy_zones_to_virtual_walls(), the
  confirmed categorization rule), and resends this exact list via
  SetVirtualWallsV1, completely unchanged. Confirms the write path
  accepts a real, complete list without error.

  Stage 2 (--drop-one-wall): removes ONE existing entry, confirms the
  robot accepted a list that differs from the one it had, then puts
  the entry back and confirms the map is as it was.

  THE OPEN QUESTION IS "does a CHANGED list work", AND REMOVAL ANSWERS
  IT WITHOUT NEW GEOMETRY. This stage was deferred for a long time on
  the grounds that it "would need a real, user-supplied polygon
  geometry to add" -- true of ADDING, and not true of removing. A
  removal is the current list minus one entry, with every surviving
  coordinate preserved byte-for-byte, exactly as stage 1 does. The
  unconfirmed CommandPolygon coordinate system is never touched, so
  the thing that made stage 2 look risky was only ever half of it.

  WHY THIS IS THE HALF WORTH HAVING. Every confirmed write so far
  resent the existing zones unchanged, and `set_virtual_wall` replaces
  the whole shared list. So the one behaviour nobody has ever
  observed is the one that matters most in practice: what the robot
  does with a list that is missing something. If a user deletes a
  keep-out zone in Home Assistant one day, this is the code path that
  runs.

  IT RESTORES, AND THE RESTORE IS THE POINT. The original list is
  captured before anything is sent and resent afterwards, so the map
  ends where it started. If the restore itself fails, the exact JSON
  needed to repair the map by hand is printed -- a tester must never
  be left with a map in a state this script created and cannot undo.

  Stage 3 (still not built): ADDING a new object. That one really does
  need user-supplied geometry, and the coordinate system is still
  unconfirmed -- the same reason region-commands' stage 4 defers ad-hoc
  geometry.

TWO SAFETY GATES (same reasoning as verify_schedule_write.py's own
two-gate design):
  1. --i-understand-this-changes-real-map-zones
  2. An interactive y/N confirmation, showing the exact JSON payload
     immediately before it's sent.

WHAT TO DO IF SOMETHING LOOKS WRONG: re-run --list-walls to see
current state. Since stage 1 only ever resends what was just read,
the safest recovery is simply running --update-unchanged again --
each run re-reads the current (by then already-restored, if stage 1
itself is what you're worried about) state fresh.
"""

from __future__ import annotations

import argparse
import json
import sys


from ._cli import add_account_arguments, confirm, connected_robot, field, require_blid, resolve_credentials, run_script




async def _fetch_current_walls(robot, p2map_id: str, p2mapv_id: str):
    """Shared by both --list-walls and --update-unchanged: download
    the current bundle, read policyZones.geojson, convert to
    VirtualWallV1 -- returns (raw_policy_zone_features, virtual_walls)."""
    from roombapy_prime.models import PolicyZoneFeature, parse_map_bundle
    from roombapy_prime.models.map_editing import policy_zones_to_virtual_walls

    link = await robot.get_map_geojson_link(p2map_id, p2mapv_id)
    url = link.get("map_url") or next(
        (v for v in link.values() if isinstance(v, str) and v.startswith("http")), None
    )
    if not url:
        raise ValueError(f"get_map_geojson_link() response had no usable URL: {link!r}")

    bundle_bytes = await robot.download_map_bundle(url)
    parsed = parse_map_bundle(bundle_bytes)
    raw_policy_zones = parsed.get("policyZones")
    if raw_policy_zones is None:
        return [], []

    raw_features = raw_policy_zones.get("features") if isinstance(raw_policy_zones, dict) else raw_policy_zones
    features = [PolicyZoneFeature.from_json(f) for f in (raw_features or [])]
    walls = policy_zones_to_virtual_walls(features)
    return features, walls


async def list_maps(username: str, password: str, country_code: str, blid: str) -> None:
    """NEW (this session): stage 0 needs BOTH --p2map-id and
    --p2mapv-id, but nothing in this project actually printed the
    latter -- and taking it from a stored favorite would be actively
    wrong, since a favorite carries the version that was current when
    it was SAVED (user_p2mapv_id), which may since have been
    superseded. That is exactly the MAP_VERSION_MISMATCH case the
    region-command work is investigating. This prints the CURRENTLY
    ACTIVE pair straight from get_active_map_versions(), making this
    script self-sufficient instead of sending testers hunting."""
    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):
        maps = await robot.get_active_map_versions()

    if not maps:
        print("\nNo maps found for this robot.")
        report.add("List maps", "OK", "no maps on this account")
    else:
        print(f"\n{len(maps)} map(s) found:\n")
        for m in maps:
            name = field(m, "name") or "(unnamed)"
            print(f"  name={name!r}")
            print(f"    --p2map-id  {field(m, 'p2map_id')}")
            print(f"    --p2mapv-id {field(m, 'active_p2mapv_id')}")
        report.add("List maps", "OK", f"{len(maps)} map(s)")
        print(
            "\nCopy the two IDs of the map you want into:\n"
            "  roombapy-prime-verify-virtual-wall-write --list-walls "
            "--p2map-id <...> --p2mapv-id <...>"
        )



async def warn_if_map_version_is_stale(robot, p2map_id: str, p2mapv_id: str, report) -> bool:
    """Warns when the caller passed a map version the robot has moved on
    from. Returns True if the version is current (or unknowable).

    FOUND IN THE FIELD (DaRealGuGu). He restarted his robot between
    tests, which re-versioned the map, then ran with the older version
    id -- and got "No policyZones.geojson data found". That result is
    ambiguous in the worst way: it reads as "you have no keep-out
    zones" when it might equally mean "we looked in a version that no
    longer exists".

    Neither he nor we could tell which, and the script said nothing
    about the difference. Since map re-versioning on restart is now
    confirmed behaviour rather than a theory, an unnoticed stale id is
    a realistic way to produce a confidently wrong empty result."""
    try:
        maps = await robot.get_active_map_versions()
    except Exception as exc:  # noqa: BLE001
        report.add("Map version freshness", "SKIPPED", f"{type(exc).__name__}: {exc}")
        return True

    for entry in maps or []:
        if field(entry, "p2map_id") != p2map_id:
            continue
        active = field(entry, "active_p2mapv_id")
        if not active:
            report.add("Map version freshness", "SKIPPED", "robot reported no active version")
            return True
        if active == p2mapv_id:
            report.add("Map version freshness", "OK", f"--p2mapv-id matches the active {active!r}")
            return True
        report.add(
            "Map version freshness", "FAILED",
            f"you passed --p2mapv-id {p2mapv_id!r} but the robot's active version is "
            f"{active!r}. Map versions change when the robot re-maps or is restarted, so an "
            "empty result here would be ambiguous: it could mean you have no zones, or that "
            f"we looked in a version that no longer exists. Re-run with --p2mapv-id {active}",
        )
        return False

    report.add("Map version freshness", "SKIPPED", f"map {p2map_id!r} not in the active list")
    return True


async def list_walls(username: str, password: str, country_code: str, blid: str, p2map_id: str, p2mapv_id: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing."""
    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):
        fresh = await warn_if_map_version_is_stale(robot, p2map_id, p2mapv_id, report)
        features, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)

    if not features:
        print("No policyZones.geojson data found for this map (or the map bundle had none).")
        if not fresh:
            print(
                "\nTREAT THIS RESULT AS INCONCLUSIVE: the map version you passed is not the\n"
                "robot's current one (see the report above). An empty result may simply mean\n"
                "we looked in a version that no longer exists. Re-run with the active version."
            )
        else:
            print(
                "\nThe map version you passed IS the robot's current one, so this is a real\n"
                "result: this map genuinely has no keep-out zones or virtual walls. That is\n"
                "still worth reporting."
            )
        return

    print(f"\n{len(features)} raw policyZones feature(s), {len(walls)} converted to VirtualWallV1:\n")
    for feature, wall in zip(features, walls + [None] * (len(features) - len(walls)), strict=True):
        # KNOWN omissions read differently from unknown ones, and the
        # old message conflated them (arielgr: a Threshold zone printed
        # as "dropped -- Threshold or unrecognized", which reads like a
        # gap in this library and is not).
        #
        # Thresholds are doorway markers. They live in policyZones
        # alongside keep-out zones, but they are NOT virtual walls: the
        # app edits them through set_thresholds, a separate command with
        # its own status field. Dropping them here is correct --
        # resending them as virtual walls would be wrong.
        if wall is not None:
            kind = type(wall).__name__
        elif (feature.properties.zone_type or "") == "Threshold":
            kind = "(skipped -- a doorway threshold, edited via set_thresholds, not a wall)"
        else:
            kind = f"(DROPPED -- zone_type {feature.properties.zone_type!r} is not recognised; please report)"
        print(f"  id={feature.feature_id!r} zone_type={feature.properties.zone_type!r} -> {kind}")

    print(
        "\nTo resend this exact combined list unchanged: "
        "roombapy-prime-verify-virtual-wall-write --update-unchanged "
        f"--p2map-id {p2map_id} --p2mapv-id {p2mapv_id} "
        "--i-understand-this-changes-real-map-zones"
    )


async def send_update_unchanged(
    username: str, password: str, country_code: str, blid: str, p2map_id: str, p2mapv_id: str,
    only_first_wall: bool = False,
) -> None:
    from roombapy_prime.models.map_editing import SetVirtualWallsV1

    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):

        print("\n== Reading current policy zones ==")
        try:
            features, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)
        except Exception as exc:  # noqa: BLE001
            report.add("Reading current policy zones", "FAILED", f"{type(exc).__name__}: {exc}")
            return
        report.add("Reading current policy zones", "OK", f"{len(features)} feature(s), {len(walls)} wall(s)")

        if only_first_wall and len(walls) > 1:
            # NARROWING TEST (this session). Three request envelopes have
            # now been genuinely sent and all rejected with HTTP 500, so
            # response_type is ruled out -- the first negative result in
            # this investigation that was actually earned.
            #
            # What remains untested is whether the problem is the command
            # SHAPE or something about this particular list. Unlike
            # rename_room, which is confirmed live, set_virtual_wall has
            # never been observed on the wire from the real app: its
            # entire structure comes from decompilation.
            #
            # One wall is the cheapest way to split that question.
            print(
                f"\n== --only-first-wall: sending 1 of {len(walls)} wall(s) =="
                "\n(If this is accepted while the full list was not, the problem is "
                "the list or one entry in it, not the command shape.)"
            )
            walls = walls[:1]

        # BUILT AFTER the truncation, not before.
        #
        # The first version of this built the command first and then
        # trimmed the list it had already been given. The banner said
        # "sending 1 of 2" and the payload printed both walls -- so the
        # run answered the question it was meant to replace, and
        # DaRealGuGu spent a session on a test that never ran.
        #
        # Second time this exact class of mistake has cost a tester an
        # evening in this project: a28 added a parameter to one layer
        # and not the next. Both were invisible until real output was
        # read carefully.
        command = SetVirtualWallsV1(walls=walls)
        payload = command.to_v1_command_body()

        print(f"\nResending {len(walls)} wall(s) -- EXACTLY as read, nothing modified.")
        print(
            f"The leading {len(walls)} in virwall is the wall COUNT, not a wall -- "
            "the piece that was missing until now and the reason every previous\n"
            "attempt returned HTTP 500."
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        if not confirm("\nSend this EXACT payload now? This changes real map zones."):
            print("Aborted by user -- nothing sent.")
            return

        # TRIES SEVERAL ENVELOPES IN ONE RUN (this session).
        #
        # Two field runs resent two untouched zones and got HTTP 500
        # both times -- once with a payload carrying a genuine extra
        # point, and again after that was corrected. So the extra point
        # was a real deviation from the documented format but not the
        # cause, and testing one guess per round is too slow when each
        # round costs a tester their evening.
        #
        # `response_type` is the least-verified part of this request:
        # "link" asks the server for a presigned DOWNLOAD url, which is
        # confirmed for FETCHING a map and may be meaningless on an
        # EDIT. This module's own docstring has said as much all along.
        #
        # The variants stop at the first success, so a working one ends
        # the run rather than sending the same edit repeatedly.
        # TYPE VARIANTS REMOVED AGAIN (this session), unused.
        #
        # They were built to probe whether the wall id should be an Int
        # and the type code a String -- and APK bytecode then settled
        # both directly: id is a String (no boxing), type code an Int
        # (Integer.valueOf + Number cast). Sending variants that are
        # already known wrong would spend real writes on someone's real
        # map and add noise to the result.
        #
        # The actual cause turned out to be elsewhere entirely: the
        # virwall array starts with a COUNT. See SetVirtualWallsV1.
        #
        # response_type stays varied: all three shapes were genuinely
        # sent and rejected before the count was known, so they were
        # rejected for the count and tell us nothing about response_type
        # after all. Worth re-running now that the payload is correct.
        variants: list[tuple[str, str | None]] = [
            ("response_type omitted entirely", None),
            ('response_type="link" (the app default)', "link"),
            ('response_type="binary"', "binary"),
        ]
        local_bug = False
        print(f"\n== Sending -- trying {len(variants)} request shapes, stopping at the first success ==")
        for label, response_type in variants:
            print(f"\n-- {label} --")
            try:
                result = await robot.edit_map(p2map_id, command, response_type=response_type)
            except TypeError as exc:
                # A TypeError here never reached the network -- it is a
                # bug in THIS code, not an answer from the server.
                #
                # HAPPENED FOR REAL (a28): response_type was added to
                # the REST client and not to the robot wrapper, so all
                # three variants died before a single request went out
                # -- and the summary below still announced that
                # response_type was ruled out. It had not been tested
                # at all.
                #
                # Reporting a local failure as a server result is the
                # same mistake as the PUBACK false signal earlier in
                # this project's history, and it wastes a tester's
                # entire evening.
                report.add(
                    f"edit_map() -- {label}", "FAILED",
                    f"LOCAL BUG, request never sent -- {type(exc).__name__}: {exc}",
                )
                print(f"   NOT SENT -- bug in this script: {exc}")
                local_bug = True
                continue
            except Exception as exc:  # noqa: BLE001
                report.add(
                    f"edit_map() -- {label}", "FAILED", f"{type(exc).__name__}: {exc}",
                )
                print(f"   failed: {type(exc).__name__}: {exc}")
                continue

            report.add(f"edit_map() -- {label}", "OK", f"response: {result!r}")
            print(f"   ACCEPTED: {result!r}")
            print(
                "\nThis shape worked. Please check the iRobot app: the zones should look\n"
                "exactly as they did before, since this resent them unchanged."
            )
            return

        if local_bug:
            print(
                "\nNOTHING WAS ACTUALLY SENT. The failures above are bugs in this script,\n"
                "not answers from the server -- so this run rules out nothing at all.\n"
                "Please report it; the fix belongs on our side."
            )
        else:
            print(
                "\nAll shapes were sent and all were rejected. That rules out response_type\n"
                "as the cause, which is worth knowing -- it was the least-verified part of\n"
                "the request."
            )



def shift_wall(wall, dx: float, dy: float):
    """Return a copy of `wall` with every coordinate moved by (dx, dy).

    THE COORDINATE SYSTEM DOES NOT HAVE TO BE KNOWN FOR THIS TO BE
    CORRECT, which is the whole reason this is the next stage rather
    than "add a new zone".

    `policy_zone_to_virtual_wall()` passes geometry through UNCHANGED --
    confirmed by native analysis, no transformation anywhere from the
    bundle read to the wire. So the numbers in a command are the numbers
    from `policyZones.geojson`, in whatever frame and unit that file
    uses. Adding a constant to them moves the zone by that amount in
    that same unit, whatever it turns out to be.

    AND THAT IS ALSO HOW THE UNIT GETS ANSWERED. Move a zone by a known
    delta, look at the app, and the observed distance against the delta
    gives the scale. That is a measurement, not a guess -- and the
    question "metres or millimetres" has been open on the edit path
    since it was first modelled.

    Every wall subtype keeps its own id and type; only positions change.
    """
    import dataclasses

    from roombapy_prime.models.geometry import LineString, Polygon

    def _move(geometry):
        if isinstance(geometry, Polygon):
            return Polygon(
                coordinates=[
                    [(x + dx, y + dy) for x, y in ring]
                    for ring in geometry.coordinates
                ]
            )
        if isinstance(geometry, LineString):
            return LineString(
                coordinates=[(x + dx, y + dy) for x, y in geometry.coordinates]
            )
        raise TypeError(f"cannot shift {type(geometry).__name__}")

    for attr in ("polygon", "geometry", "line"):
        if hasattr(wall, attr):
            return dataclasses.replace(wall, **{attr: _move(getattr(wall, attr))})

    # A subtype storing its points some other way must not be moved by
    # guessing -- silently returning it unchanged would look like a
    # successful move that did nothing, which is the one result this
    # stage cannot distinguish from a robot that ignores the edit.
    raise TypeError(
        f"{type(wall).__name__} has no recognised geometry field; "
        "refusing to move it rather than sending it unchanged"
    )


def wall_extent(wall) -> float:
    """Largest side of the wall's bounding box, for sizing a sane delta."""
    for attr in ("polygon", "geometry", "line"):
        geometry = getattr(wall, attr, None)
        if geometry is None:
            continue
        coords = getattr(geometry, "coordinates", None) or []
        flat = coords[0] if coords and isinstance(coords[0], list) else coords
        if not flat:
            return 0.0
        xs = [p[0] for p in flat]
        ys = [p[1] for p in flat]
        return max(max(xs) - min(xs), max(ys) - min(ys))
    return 0.0


async def send_drop_one_wall(
    username: str, password: str, country_code: str, blid: str,
    p2map_id: str, p2mapv_id: str, index: int,
) -> None:
    """Stage 2: send the list MINUS one entry, then put it back.

    The first write in this project's history that changes what the
    robot holds rather than restating it.
    """
    from roombapy_prime.models.map_editing import SetVirtualWallsV1

    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):

        print("\n== Reading current policy zones ==")
        try:
            features, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)
        except Exception as exc:  # noqa: BLE001
            report.add("Reading current policy zones", "FAILED", f"{type(exc).__name__}: {exc}")
            return
        report.add("Reading current policy zones", "OK", f"{len(walls)} wall(s)")

        if len(walls) < 2:
            # ONE WALL IS NOT ENOUGH, and the reason is not squeamishness.
            # Dropping the only entry sends an EMPTY list, and an empty
            # list is a different question -- "does the robot accept a
            # removal" and "does the robot accept having no zones at
            # all" would be answered by one run and indistinguishable in
            # the result. This project has spent enough evenings on
            # results that answered two questions at once.
            print(
                f"\nThis map has {len(walls)} wall(s). Stage 2 needs at least 2.\n"
                "\nWith one wall, removing it sends an empty list -- which asks a "
                "different question (does an EMPTY list work) and would not tell "
                "us what a changed list does. Add a second zone in the iRobot app "
                "and re-run, or run this on a map that has two."
            )
            report.add("Stage 2", "SKIPPED", f"needs >=2 walls, found {len(walls)}")
            return

        if not 0 <= index < len(walls):
            print(f"\n--index {index} is out of range: this map has {len(walls)} wall(s), 0-{len(walls)-1}.")
            return

        # CAPTURED BEFORE ANYTHING IS SENT. The restore must not depend
        # on re-reading the map afterwards: if the drop succeeds and the
        # re-read then fails, a re-read-based restore would have nothing
        # to send and the tester would be left with a map this script
        # broke.
        original = list(walls)
        victim = walls[index]
        remaining = [w for i, w in enumerate(walls) if i != index]

        print(f"\n== Dropping wall {index} of {len(walls)} ==")
        print(f"Removing:  {victim!r}")
        print(f"Sending {len(remaining)} of {len(original)} wall(s).")

        restore_payload = SetVirtualWallsV1(walls=original).to_v1_command_body()
        print(
            "\nRESTORE PAYLOAD, printed BEFORE the change so you have it even if "
            "this script dies mid-run. If anything goes wrong, this is the exact "
            "body that puts the map back:"
        )
        print(json.dumps(restore_payload, indent=2, ensure_ascii=False))

        drop_command = SetVirtualWallsV1(walls=remaining)
        print("\nAbout to send:")
        print(json.dumps(drop_command.to_v1_command_body(), indent=2, ensure_ascii=False))

        if not confirm(
            f"\nSend the list WITHOUT wall {index}? This really removes a zone "
            "from your map. It is put back immediately afterwards."
        ):
            print("Aborted by user -- nothing sent.")
            return

        try:
            result = await robot.edit_map(p2map_id, drop_command)
        except TypeError as exc:
            # LOCAL BUG, never reached the network. Reported as such
            # rather than as a server answer -- doing otherwise cost
            # DaRealGuGu an evening once already (see stage 1).
            report.add("Drop one wall", "FAILED", f"LOCAL BUG, not sent -- {exc}")
            print(f"\n   NOT SENT -- bug in this script: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            report.add("Drop one wall", "FAILED", f"{type(exc).__name__}: {exc}")
            print(f"\n   Rejected: {type(exc).__name__}: {exc}")
            print(
                "\nNOTHING WAS CHANGED -- a rejected edit leaves the map alone, so "
                "no restore is needed. This is a real result: a changed list is "
                "refused where an unchanged one is accepted."
            )
            return

        report.add("Drop one wall", "OK", f"response: {result!r}")
        print(f"\n   ACCEPTED: {result!r}")

        # VERIFY BY READING, not by trusting the response. An accepted
        # edit and a stored edit are different claims, and this project
        # has confirmed several commands that were acknowledged and did
        # nothing.
        #
        # READ THE VERSION THE EDIT PRODUCED, NOT THE ONE ON THE COMMAND
        # LINE. Every accepted edit mints a new p2mapv_id and returns it;
        # the id the user passed in still points at the map as it was
        # before. Re-reading that one reports "unchanged" whatever
        # happened, which is not a weak check but a broken one -- it
        # cannot produce any other answer.
        #
        # It did exactly that on the first real run (@chairstacker,
        # issue #89): the script printed "ACCEPTED BUT NOT STORED", its
        # loudest possible warning, while he watched the zone disappear
        # from the app and come back. The robot was right and the check
        # was wrong.
        new_version = result.get("p2mapv_id") if isinstance(result, dict) else None
        if not new_version:
            print(
                "\n   The edit response carried no new p2mapv_id, so there is "
                "nothing to verify against. Skipping the check rather than "
                "re-reading the old version, which would report 'unchanged' "
                "no matter what happened."
            )
            report.add("Verify removal", "SKIPPED", "no p2mapv_id in response")
            new_version = None

        print(f"\n== Verifying: reading map version {new_version} ==")
        try:
            if new_version is None:
                raise RuntimeError("no new map version to read")
            _, after = await _fetch_current_walls(robot, p2map_id, new_version)
            print(f"Map now reports {len(after)} wall(s) (was {len(original)}).")
            report.add(
                "Verify removal", "OK" if len(after) == len(original) - 1 else "UNEXPECTED",
                f"{len(after)} wall(s) after dropping 1 of {len(original)}",
            )
            if len(after) == len(original):
                print(
                    "\nACCEPTED BUT NOT STORED -- the count is unchanged. That is the "
                    "most important outcome this script can produce: the write path "
                    "reports success and the robot keeps its old list."
                )
        except Exception as exc:  # noqa: BLE001
            report.add("Verify removal", "FAILED", f"{type(exc).__name__}: {exc}")
            print(f"   could not re-read: {exc}")

        # RESTORE, unconditionally and without asking. The tester agreed
        # to a removal that is put back; leaving the decision to a second
        # prompt would let a distracted "n" end the run with a zone
        # missing.
        print("\n== Restoring the original list ==")
        try:
            restore_result = await robot.edit_map(
                p2map_id, SetVirtualWallsV1(walls=original)
            )
        except Exception as exc:  # noqa: BLE001
            report.add("Restore", "FAILED", f"{type(exc).__name__}: {exc}")
            print(
                f"\n   RESTORE FAILED: {type(exc).__name__}: {exc}\n"
                "\n*** YOUR MAP IS MISSING ONE ZONE. ***\n"
                "\nThe restore payload is printed above. You can also simply redraw "
                "the zone in the iRobot app -- it is a normal edit, nothing is "
                "corrupted. Please report this: a failing restore is the one "
                "outcome this script must never produce silently."
            )
            return

        report.add("Restore", "OK", f"response: {restore_result!r}")
        print(f"   restored: {restore_result!r}")

        try:
            restored_version = (
                restore_result.get("p2mapv_id")
                if isinstance(restore_result, dict)
                else None
            ) or p2mapv_id
            _, final = await _fetch_current_walls(robot, p2map_id, restored_version)
            ok = len(final) == len(original)
            report.add("Verify restore", "OK" if ok else "UNEXPECTED",
                       f"{len(final)} wall(s), expected {len(original)}")
            print(
                f"\nFinal count: {len(final)} wall(s), started with {len(original)}."
                + ("  Map is back as it was." if ok else "  MISMATCH -- please check the app.")
            )
        except Exception as exc:  # noqa: BLE001
            report.add("Verify restore", "FAILED", f"{type(exc).__name__}: {exc}")
            print(f"   could not re-read: {exc}")

        print(
            "\nPlease also look at the map in the iRobot app. A count matching is "
            "good evidence, but only your eyes can confirm the zone came back in "
            "the right place."
        )


async def send_move_one_wall(
    username: str, password: str, country_code: str, blid: str,
    p2map_id: str, p2mapv_id: str, index: int, delta: float | None,
) -> None:
    """Stage 2b: move one existing zone, then put it back.

    Answers two questions in one run that no previous stage could:
    whether the robot stores a list whose CONTENT changed (not just its
    length), and what the coordinates actually mean.
    """
    from roombapy_prime.models.map_editing import SetVirtualWallsV1

    async with connected_robot(
        username, password, country_code, blid
    ) as (robot, report):

        print("\n== Reading current policy zones ==")
        try:
            _, walls = await _fetch_current_walls(robot, p2map_id, p2mapv_id)
        except Exception as exc:  # noqa: BLE001
            report.add("Reading current policy zones", "FAILED", f"{type(exc).__name__}: {exc}")
            return
        report.add("Reading current policy zones", "OK", f"{len(walls)} wall(s)")

        if not walls:
            print("\nThis map has no zones to move. Draw one in the iRobot app first.")
            return
        if not 0 <= index < len(walls):
            print(f"\n--index {index} is out of range: {len(walls)} wall(s), 0-{len(walls)-1}.")
            return

        original = list(walls)
        target = walls[index]

        # DELTA SIZED FROM THE ZONE ITSELF, not from a guessed unit.
        # A quarter of the zone's own longest side is visible in the app
        # and cannot leave the map: the zone stays roughly where it was,
        # overlapping its old position. A fixed number would be either
        # invisible or off the floor plan depending on whether the frame
        # turns out to be metres or millimetres -- which is the very
        # thing this run is meant to find out.
        extent = wall_extent(target)
        if delta is None:
            delta = extent / 4 if extent else 0.0
        if not delta:
            print(
                "\nCould not size a delta from this zone (extent 0) and none was "
                "given. Pass --delta explicitly if you know the frame."
            )
            return

        print(f"\n== Moving wall {index} of {len(walls)} ==")
        print(f"Zone extent (longest side): {extent}")
        print(f"Shifting by (+{delta}, +{delta}) in the map's own units.")
        print(
            "\nTHE UNIT IS WHAT THIS RUN MEASURES. The numbers come from "
            "policyZones.geojson and reach the robot untransformed, so this "
            "delta is in whatever unit that file uses. Compare how far the "
            "zone appears to move in the app against the zone's own size: a "
            "quarter of its width is the expected shift."
        )

        try:
            moved = shift_wall(target, delta, delta)
        except TypeError as exc:
            report.add("Move one wall", "FAILED", f"LOCAL: {exc}")
            print(f"\n   NOT SENT -- {exc}")
            return

        changed = [moved if i == index else w for i, w in enumerate(original)]

        restore_payload = SetVirtualWallsV1(walls=original).to_v1_command_body()
        print(
            "\nRESTORE PAYLOAD, printed before the change so it exists even if "
            "this run dies partway:"
        )
        print(json.dumps(restore_payload, indent=2, ensure_ascii=False))

        command = SetVirtualWallsV1(walls=changed)
        print("\nAbout to send:")
        print(json.dumps(command.to_v1_command_body(), indent=2, ensure_ascii=False))

        if not confirm(
            f"\nMove zone {index} by ({delta}, {delta})? It is moved back "
            "immediately afterwards."
        ):
            print("Aborted by user -- nothing sent.")
            return

        try:
            result = await robot.edit_map(p2map_id, command)
        except TypeError as exc:
            report.add("Move one wall", "FAILED", f"LOCAL BUG, not sent -- {exc}")
            print(f"\n   NOT SENT -- bug in this script: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            report.add("Move one wall", "FAILED", f"{type(exc).__name__}: {exc}")
            print(f"\n   Rejected: {type(exc).__name__}: {exc}")
            print("\nNothing was changed -- a rejected edit leaves the map alone.")
            return

        report.add("Move one wall", "OK", f"response: {result!r}")
        print(f"\n   ACCEPTED: {result!r}")
        print(
            "\n*** LOOK AT THE MAP IN THE IROBOT APP NOW, BEFORE CONTINUING. ***\n"
            "\nThis is the only moment the moved zone exists. The restore below "
            "puts it back, and no re-read can tell you what it LOOKED like.\n"
            "\nWorth capturing: did it move at all, roughly how far compared to "
            "the zone's own width, and in which direction."
        )
        confirm("\nSeen it? Press y to restore the original position.")

        print("\n== Restoring the original position ==")
        try:
            restore_result = await robot.edit_map(
                p2map_id, SetVirtualWallsV1(walls=original)
            )
        except Exception as exc:  # noqa: BLE001
            report.add("Restore", "FAILED", f"{type(exc).__name__}: {exc}")
            print(
                f"\n   RESTORE FAILED: {type(exc).__name__}: {exc}\n"
                "\n*** ONE ZONE IS IN THE WRONG PLACE. *** The restore payload is "
                "printed above, and the zone can also simply be redrawn in the "
                "iRobot app -- nothing is corrupted. Please report this."
            )
            return

        report.add("Restore", "OK", f"response: {restore_result!r}")
        print(f"   restored: {restore_result!r}")
        print(
            "\nPlease confirm in the app that the zone is back where it started."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 1 test for SetVirtualWallsV1 (\"set_virtual_wall\"): resend the current, "
            "complete virtual-wall/keep-out-zone/no-mop-zone list completely unchanged. See "
            "this module's own docstring for the full staged-risk explanation."
        )
    )
    add_account_arguments(parser)
    # NOT required=True (this session): --list-maps exists precisely to
    # OBTAIN these two, so demanding them up front made it unusable.
    # Checked below, only for the stages that actually need them.
    parser.add_argument("--p2map-id", default=None)
    parser.add_argument("--p2mapv-id", default=None, help="From --list-maps, or get_active_map_versions()'s own active_p2mapv_id.")
    parser.add_argument(
        "--list-maps", action="store_true",
        help="Stage 0a: print each map's p2map_id and its CURRENTLY ACTIVE version id, ready "
        "to paste into --list-walls. Sends nothing, and needs no IDs itself.",
    )
    parser.add_argument(
        "--list-walls", action="store_true",
        help="Stage 0: list current virtual walls/zones for this map. Sends nothing.",
    )
    parser.add_argument(
        "--update-unchanged", action="store_true",
        help="Stage 1: resend the current, complete list unchanged.",
    )
    parser.add_argument(
        "--only-first-wall", action="store_true",
        help="Stage 1b: resend only the FIRST wall instead of the whole list. "
        "Splits one question into two -- if a single wall is accepted while the "
        "full list is rejected, the problem is the list or a specific entry in it; "
        "if a single wall is rejected too, the command shape itself is wrong. "
        "Both outcomes are informative, which was not true of the whole-list test "
        "on its own.",
    )
    parser.add_argument(
        "--drop-one-wall", action="store_true",
        help="Stage 2: send the list MINUS one entry, verify the removal, then put "
        "it back. The first write that CHANGES what the robot holds. Needs at "
        "least 2 walls, and restores automatically.",
    )
    parser.add_argument(
        "--index", type=int, default=0,
        help="Which wall --drop-one-wall removes (0-based, from --list-walls). "
        "Default 0.",
    )
    parser.add_argument(
        "--move-one-wall", action="store_true",
        help="Stage 2b: move one existing zone by a delta sized from the zone "
        "itself, pause so you can look at the app, then move it back. Answers "
        "what the coordinates MEAN without needing to know it in advance.",
    )
    parser.add_argument(
        "--delta", type=float, default=None,
        help="How far --move-one-wall shifts, in the map's own units. Default: "
        "a quarter of the zone's longest side, which is visible in the app and "
        "cannot leave the floor plan whatever the unit turns out to be.",
    )
    parser.add_argument("--i-understand-this-changes-real-map-zones", action="store_true")
    args = parser.parse_args()
    require_blid(args)

    if not (args.list_maps or args.list_walls or args.update_unchanged
            or args.drop_one_wall or args.move_one_wall):
        print(
            "Nothing to do -- start with --list-maps (safe, sends nothing, and gives you the "
            "two IDs the other stages need)."
        )
        return

    if (args.list_walls or args.update_unchanged or args.drop_one_wall
            or args.move_one_wall) and not (args.p2map_id and args.p2mapv_id):
        print(
            "Aborted: --p2map-id and --p2mapv-id are both required for this stage. "
            "Run --list-maps first to get them."
        )
        sys.exit(1)

    if (args.update_unchanged or args.drop_one_wall or args.move_one_wall) and not args.i_understand_this_changes_real_map_zones:
        print("Aborted: --i-understand-this-changes-real-map-zones is missing.")
        sys.exit(1)

    username, password = resolve_credentials(args)

    if args.list_maps:
        sys.exit(run_script(list_maps(username, password, args.country_code, args.blid)))
        return

    if args.list_walls:
        sys.exit(run_script(list_walls(username, password, args.country_code, args.blid, args.p2map_id, args.p2mapv_id)))
        return

    if args.move_one_wall:
        sys.exit(run_script(
            send_move_one_wall(
                username, password, args.country_code, args.blid,
                args.p2map_id, args.p2mapv_id, args.index, args.delta,
            )
        ))
        return

    if args.drop_one_wall:
        sys.exit(run_script(
            send_drop_one_wall(
                username, password, args.country_code, args.blid,
                args.p2map_id, args.p2mapv_id, args.index,
            )
        ))
        return

    if args.update_unchanged:
        sys.exit(run_script(
            send_update_unchanged(
                username, password, args.country_code, args.blid,
                args.p2map_id, args.p2mapv_id, args.only_first_wall,
            )
        ))
        return


if __name__ == "__main__":
    main()
