"""Interactive, low-friction session runner for stages 1 -> 1b -> 2 of
verify_region_commands.py -- built directly in response to real
friction: retyping the full command + credentials for every single
stage, and having to parse raw event reprs by eye to judge whether
anything meaningful happened.

WHAT THIS IS NOT: an unattended, auto-continuing test runner. Every
stage that actually sends something still shows the exact payload and
requires its own "y/N, this will move the robot" confirmation --
_confirm_show_send_watch() itself, reused unchanged from
verify_region_commands.py, still owns that gate. What this script adds
on top is a SEPARATE "continue to the next stage?" prompt between
stages, so a human is still deliberately in the loop for every single
robot movement, just without re-typing --blid/--favorite-id/credentials
each time.

WHY NO AUTOMATIC PASS/FAIL BRANCHING BETWEEN STAGES: there is currently
no programmatic success signal to branch on. send_routine_command_via_
cmd_topic() is fire-and-forget (no server acknowledgement at all), and
the mission-timeline events that arrive afterward are not evaluated
against anything -- "did this actually clean the requested room, or the
whole house, or nothing" is a judgment only a human physically watching
the robot can make. _summarize_events() (verify_region_commands.py)
surfaces the specific fields that make that judgment easier (echoed
region/zone id, area, initiator) -- but it reports facts, not a verdict.

SCOPE: stages 1, 1b, 2 only -- these three naturally chain off the SAME
favorite_id. Stage 3 (--list-rooms/--send-region) uses a fundamentally
different input (a real room_id + p2map_id, not a favorite) and stays
a deliberately separate, standalone step -- see this script's own
closing message for exact next-step instructions if stage 2 succeeds.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

import aiohttp

from .diagnostics import Report
from .verify_region_commands import (
    _add_initiator_if_missing,
    _build_modified_command,
    _confirm,
    _confirm_show_send_watch,
    _is_safe_command_def,
    _login_and_connect,
    _region_types,
    _summarize_events,
)


def _pick_favorite_interactively(favorites: list) -> tuple[object, int] | None:
    """NEW (this session): --favorite-id is now optional. Since this
    script already fetches every favorite anyway (needed regardless,
    to look up command_defs), requiring a separate --list-favorites
    run first just to copy an id was redundant friction -- this
    prints the same STAGE-1-ELIGIBLE listing inline and lets the
    tester pick a number instead of retyping an id.

    Only lists command_defs that pass _is_safe_command_def() (RID/ZID
    regions only, same eligibility rule as list_favorites()/stage 1
    itself) -- a TID-containing command_def is never offered here,
    consistent with this session-runner's own scope (stages 1/1b/2
    only, see the module docstring)."""
    eligible: list[tuple[object, int]] = []
    if not favorites:
        print("No favorites found on this account for this robot.")
        return None

    print(f"\n{len(favorites)} favorite(s) found:\n")
    for favorite in favorites:
        print(f"favorite_id={favorite.favorite_id!r}  name={favorite.name!r}")
        if not favorite.command_defs:
            print("  (no command_defs)")
            continue
        for i, command in enumerate(favorite.command_defs):
            region_types = _region_types(getattr(command, "regions", None))
            if _is_safe_command_def(command):
                eligible.append((favorite, i))
                print(f"  [{len(eligible)}] {favorite.name!r} command_defs[{i}] regions={region_types or '(none)'}")
            else:
                print(f"  (command_defs[{i}] contains a TID region -- not offered here)")

    if not eligible:
        print("\nNo STAGE-1-ELIGIBLE command_defs found on this account. Nothing to run.")
        return None

    choice = input(f"\nPick one [1-{len(eligible)}], or anything else to abort: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(eligible)):
        print("Aborted -- nothing sent.")
        return None
    return eligible[int(choice) - 1]


async def run_session(
    username: str, password: str, country_code: str, blid: str,
    favorite_id: str | None, command_index: int | None, suction_level: int, watch_seconds: int,
) -> None:
    report = Report()
    all_results: list[tuple[str, list]] = []

    async with aiohttp.ClientSession() as session:
        robot = await _login_and_connect(session, username, password, country_code, blid, report)

        print("\n== Fetching favorites (once for this whole session) ==")
        favorites = await robot.get_favorites()

        if favorite_id is None:
            picked = _pick_favorite_interactively(favorites)
            if picked is None:
                return
            favorite, command_index = picked
        else:
            favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
            if favorite is None:
                print(f"ERROR: no favorite with favorite_id={favorite_id!r} found on this account.")
                return
            if command_index is None:
                command_index = 0
            if not favorite.command_defs or command_index >= len(favorite.command_defs):
                print(f"ERROR: favorite {favorite_id!r} has no command_defs[{command_index}].")
                return

        original = favorite.command_defs[command_index]
        favorite_id = favorite.favorite_id  # normalize: always the real id from here on,
        # whether it came from --favorite-id or the interactive picker below.

        if not _is_safe_command_def(original):
            print(
                "ABORTED: this command_def contains a TID (ad-hoc/temporary) region. "
                "This session covers stages 1/1b/2 (RID/ZID regions only) -- see "
                "--send-adhoc in verify_region_commands.py for the separate, "
                "higher-risk stage 4 path instead."
            )
            return

        # ---- Stage 1 ----
        print("\n" + "=" * 60)
        print("STAGE 1 -- resend this favorite's own command, completely unchanged")
        print("=" * 60)
        events = await _confirm_show_send_watch(
            robot, original, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] -- EXACTLY as stored, nothing modified:",
            disconnect_after=False,
        )
        all_results.append(("Stage 1 (unchanged)", events))
        if not _confirm("\nContinue to stage 1b (adds initiator if missing)?"):
            await robot.disconnect()
            _print_final_summary(all_results)
            return

        # ---- Stage 1b ----
        print("\n" + "=" * 60)
        print("STAGE 1b -- same command, adds initiator=\"rmtApp\" if it was missing")
        print("=" * 60)
        with_initiator = _add_initiator_if_missing(original)
        if with_initiator is None:
            print(
                f"This favorite's command_def already has initiator={original.initiator!r} set "
                "-- stage 1b has nothing to add (it's identical to stage 1 for this favorite). Skipping."
            )
            events_1b: list = []
        else:
            events_1b = await _confirm_show_send_watch(
                robot, with_initiator, report, watch_seconds,
                f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
                f"command_defs[{command_index}] with initiator added (was unset -> \"rmtApp\"), "
                "nothing else changed:",
                disconnect_after=False,
            )
        all_results.append(("Stage 1b (+initiator)", events_1b))
        if not _confirm(f"\nContinue to stage 2 (changes suction level to {suction_level})?"):
            await robot.disconnect()
            _print_final_summary(all_results)
            return

        # ---- Stage 2 ----
        print("\n" + "=" * 60)
        print(f"STAGE 2 -- same favorite, suction level changed to {suction_level}")
        print("=" * 60)
        modified, original_level = _build_modified_command(original, suction_level)
        # REAL GAP FOUND (jayjay13011's field report): stage 2 never
        # added initiator either, same reason as verify_region_commands.py's
        # own send_stage_two() -- see that function's docstring for the
        # full finding. Fixed identically here.
        with_initiator_2 = _add_initiator_if_missing(modified)
        final_stage_2_command = with_initiator_2 if with_initiator_2 is not None else modified
        events_2 = await _confirm_show_send_watch(
            robot, final_stage_2_command, report, watch_seconds,
            f"Favorite: {favorite.name!r} (favorite_id={favorite_id!r})\n"
            f"command_defs[{command_index}] with suction_level changed "
            f"({original_level!r} -> {suction_level!r}), regions untouched, initiator included:",
            disconnect_after=False,
        )
        all_results.append((f"Stage 2 (suction_level -> {suction_level})", events_2))
        await robot.disconnect()

    _print_final_summary(all_results)
    print(
        "\nStage 3 (a from-scratch command for a real room, no favorite) is a deliberately "
        "separate next step -- see:\n"
        "  roombapy-prime-verify-region-commands --list-rooms --p2map-id YOUR_MAP_ID --blid "
        f"{blid}\n"
        "  roombapy-prime-verify-region-commands --send-region --p2map-id YOUR_MAP_ID "
        "--room-id REAL_ROOM_ID --region-type rid --blid ...\n"
        "for exact next-step instructions."
    )


def _print_final_summary(results: list[tuple[str, list]]) -> None:
    print("\n" + "#" * 60)
    print("# SESSION SUMMARY -- every stage attempted this run")
    print("#" * 60)
    for label, events in results:
        print(f"\n--- {label} ---")
        print(_summarize_events(events))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Low-friction session runner for verify_region_commands.py's stages 1/1b/2 -- "
            "one login, one favorite lookup, a 'continue to next stage?' prompt between each. "
            "Every sending stage still requires its own explicit y/N confirmation showing the "
            "exact payload -- see this module's own docstring for the full safety reasoning."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", default=os.environ.get("ROOMBAPY_PRIME_BLID"))
    parser.add_argument(
        "--favorite-id", default=None, metavar="FAVORITE_ID",
        help="Optional -- if omitted, the script lists your STAGE-1-ELIGIBLE favorites (fetched "
        "anyway, no extra step) and lets you pick one interactively instead.",
    )
    parser.add_argument("--command-index", type=int, default=None)
    parser.add_argument("--suction-level", type=int, default=2, help="Used at stage 2 (default: 2).")
    parser.add_argument("--watch-seconds", type=int, default=60)
    parser.add_argument(
        "--i-understand-this-will-move-my-robot", action="store_true", dest="confirmed_move",
    )
    parser.add_argument(
        "--i-understand-this-is-experimental-and-unconfirmed", action="store_true", dest="confirmed_experimental",
    )
    args = parser.parse_args()

    if not args.blid:
        print("Aborted: --blid is required (or set the ROOMBAPY_PRIME_BLID env var).")
        sys.exit(1)
    if not (args.confirmed_move and args.confirmed_experimental):
        print(
            "Aborted: both --i-understand-this-will-move-my-robot and "
            "--i-understand-this-is-experimental-and-unconfirmed are required -- this session "
            "can send up to three real, robot-moving commands."
        )
        sys.exit(1)

    username = args.username or input("iRobot account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("iRobot account password: ")

    asyncio.run(
        run_session(
            username, password, args.country_code, args.blid,
            args.favorite_id, args.command_index, args.suction_level, args.watch_seconds,
        )
    )


if __name__ == "__main__":
    main()
