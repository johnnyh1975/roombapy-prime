"""Systematic, staged test package for favorite writes
(create_favorite()/update_favorite()/delete_favorite()) -- never
tested live before this script existed. Read those methods' own
docstrings in prime_robot.py first.

THE STAGED APPROACH, same general philosophy as
verify_region_commands.py/verify_schedule_write.py's own staged
scripts -- each stage only worth attempting once the previous one is
confirmed working:

  Stage 1 (--update-unchanged): resend an EXISTING favorite's own
  data, completely unchanged, via update_favorite(). get_favorites()
  already returns fully-typed FavoriteV1 objects (including properly
  reconstructed RoutineCommand entries in command_defs -- see
  rest_client.py's own _favorite_from_json() for the evidence trail),
  so this needs no new parsing code -- fetch, find, resend as-is.

  Stage 2 (--update-color): the safest available MODIFICATION --
  changing only a favorite's own `color` field. Deliberately chosen
  because it's purely cosmetic (how the favorite is displayed) and
  cannot affect what the favorite actually cleans or when -- the
  favorite's own command_defs are resent completely unchanged
  alongside it.

  Stage 3 (--create-and-delete-test): tests create_favorite() and
  delete_favorite() TOGETHER, self-cleaning -- same "do it, confirm
  it, immediately revert" philosophy already established for map
  editing's own rename-then-revert test. Creates a minimal,
  clearly-named test favorite (empty command_defs -- no region data
  needed at all for this), asks you to confirm it appeared in the
  real app, then deletes it again. Does NOT leave a stray test
  favorite behind if you follow through both confirmations.

TWO SEPARATE SAFETY GATES (same reasoning as
verify_schedule_write.py's own two-gate design -- see that module's
own docstring for why these staged writes don't need a third,
"experimental hypothesis" flag on top of the change-acknowledgment
one: favorite reads are already confirmed live and well-modeled, only
the write path's acceptance is unconfirmed here):
  1. --i-understand-this-changes-a-real-favorite
  2. An interactive y/N confirmation, showing the EXACT JSON payload
     immediately before it's sent.

WHAT TO DO IF SOMETHING LOOKS WRONG: re-run --list-favorites to see
current state. For stage 1/2, --update-unchanged with a saved copy of
the original data (e.g. from --list-favorites output) restores it --
there is no live "undo" beyond sending the original data back, since a
favorite write's effect is what the favorite itself now says, not an
immediate one-time action a command like "stop" could interrupt.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import getpass
import json
import os
import sys

import aiohttp

from .diagnostics import Report
from .prime_factory import PrimeFactory


def _confirm(prompt: str) -> bool:
    """Interactive confirmation -- ONLY "j"/"ja"/"y"/"yes" (case
    doesn't matter) counts as approval, anything else (including just
    pressing Enter) aborts. Same convention as this project's other
    diagnostic scripts."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


async def list_favorites(username: str, password: str, country_code: str, blid: str) -> None:
    """Stage 0 -- pure reconnaissance, sends nothing."""
    async with aiohttp.ClientSession() as session:
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        favorites = await robot.get_favorites()

    if not favorites:
        print("No favorites found on this account.")
        return

    print(f"\n{len(favorites)} favorite(s) found:\n")
    for favorite in favorites:
        print(
            f"favorite_id={favorite.favorite_id!r}  name={favorite.name!r}  "
            f"color={favorite.color!r}  icon={favorite.icon!r}  "
            f"command_defs={len(favorite.command_defs)}"
        )
    print(
        "\nTo resend one unchanged: roombapy-prime-verify-favorite-write "
        "--update-unchanged FAVORITE_ID --i-understand-this-changes-a-real-favorite"
    )
    print(
        "To change only its color: roombapy-prime-verify-favorite-write "
        "--update-color FAVORITE_ID --color '#FF0000' --i-understand-this-changes-a-real-favorite"
    )


async def _confirm_show_send(robot, favorite_id: str, favorite, report: Report, description: str) -> None:
    payload = favorite.to_json()
    print(f"\n{description}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if not _confirm("\nSend this EXACT payload now? This changes a real favorite."):
        print("Aborted by user -- nothing sent.")
        return

    print("\n== Sending ==")
    result = await robot.update_favorite(favorite_id, favorite)
    report.add("update_favorite()", "OK", f"response: {result!r}")


async def send_update_unchanged(
    username: str, password: str, country_code: str, blid: str, favorite_id: str,
) -> None:
    report = Report()
    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")

        print("\n== Fetching favorites ==")
        favorites = await robot.get_favorites()
        favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
        if favorite is None:
            print(f"ERROR: no favorite with favorite_id={favorite_id!r} found.")
            return

        await _confirm_show_send(
            robot, favorite_id, favorite, report,
            f"favorite_id={favorite_id!r} -- EXACTLY as stored, nothing modified:",
        )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")


def _build_recolored_favorite(favorite, new_color: str):
    """Stage 2's core logic, pulled out of the async I/O so it's
    directly unit-testable -- same lesson as
    verify_region_commands.py's own _build_modified_command() and
    verify_schedule_write.py's own _build_disabled_schedules(): an
    executing test catches real construction bugs a syntax check
    alone cannot. Returns (modified_favorite, original_color)."""
    original_color = favorite.color
    modified = dataclasses.replace(favorite, color=new_color)
    return modified, original_color


async def send_update_color(
    username: str, password: str, country_code: str, blid: str, favorite_id: str, new_color: str,
) -> None:
    report = Report()
    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")

        print("\n== Fetching favorites ==")
        favorites = await robot.get_favorites()
        favorite = next((f for f in favorites if f.favorite_id == favorite_id), None)
        if favorite is None:
            print(f"ERROR: no favorite with favorite_id={favorite_id!r} found.")
            return

        modified, original_color = _build_recolored_favorite(favorite, new_color)

        await _confirm_show_send(
            robot, favorite_id, modified, report,
            f"favorite_id={favorite_id!r} color: {original_color!r} -> {new_color!r}, "
            "everything else (including command_defs) unchanged:",
        )

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")


async def create_and_delete_test(username: str, password: str, country_code: str, blid: str) -> None:
    """Stage 3 -- tests create_favorite() and delete_favorite()
    together, self-cleaning. Creates a minimal, clearly-named test
    favorite (empty command_defs -- no region data needed), asks for
    confirmation it appeared in the real app, then deletes it again.
    Same "do it, confirm it, revert it" philosophy already established
    for map editing's own rename-then-revert test."""
    from .models.favorites import FavoriteV1

    report = Report()
    test_name = "[roombapy-prime-test]"

    async with aiohttp.ClientSession() as session:
        print("\n== Login ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")

        test_favorite = FavoriteV1(name=test_name, command_defs=[])
        payload = test_favorite.to_json()
        print(f"\nAbout to create a minimal test favorite (empty command_defs, name={test_name!r}):")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        if not _confirm("\nCreate this test favorite now?"):
            print("Aborted by user -- nothing created.")
            return

        print("\n== Creating ==")
        create_result = await robot.create_favorite(test_favorite)
        report.add("create_favorite()", "OK", f"response: {create_result!r}")
        created_id = create_result.get("favorite_id") if isinstance(create_result, dict) else None
        if not created_id:
            print(
                f"WARNING: create_favorite()'s response didn't contain a recognizable "
                f"favorite_id (response: {create_result!r}) -- cannot proceed to delete "
                f"automatically. Check the real app and delete '{test_name}' manually if it "
                "was actually created."
            )
            report.redact(username, password)
            ok, failed, skipped = report.summary()
            print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")
            return

        print(f"\nCreated with favorite_id={created_id!r}.")
        if not _confirm(f"Did '{test_name}' appear as a new favorite in the real app? Confirm to proceed to delete it"):
            print(
                f"Not confirmed -- leaving '{test_name}' (favorite_id={created_id!r}) in place. "
                "Delete it manually via the real app, or re-run this script's delete step "
                "separately if one gets added later."
            )
            report.add("delete_favorite()", "SKIPPED", "not confirmed by user")
        else:
            print("\n== Deleting ==")
            delete_result = await robot.delete_favorite(created_id)
            report.add("delete_favorite()", "OK", f"response: {delete_result!r}")

    report.redact(username, password)
    ok, failed, skipped = report.summary()
    print(f"\nSummary: {ok} OK, {failed} failed, {skipped} skipped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Staged test package for favorite writes (create_favorite()/update_favorite()/"
            "delete_favorite()). See this module's own docstring for the full staged-risk "
            "explanation before using --update-unchanged/--update-color/--create-and-delete-test."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", default=os.environ.get("ROOMBAPY_PRIME_BLID"), help="The exact target device -- no 'first device found'. Falls back to ROOMBAPY_PRIME_BLID env var.")
    parser.add_argument(
        "--list-favorites", action="store_true",
        help="Stage 0: list favorites. Sends nothing.",
    )
    parser.add_argument(
        "--update-unchanged", metavar="FAVORITE_ID", default=None,
        help="Stage 1: resend this favorite's own data unchanged.",
    )
    parser.add_argument(
        "--update-color", metavar="FAVORITE_ID", default=None,
        help="Stage 2: resend this favorite with only its color changed.",
    )
    parser.add_argument("--color", default=None, help="New color value for --update-color (required with it).")
    parser.add_argument(
        "--create-and-delete-test", action="store_true",
        help="Stage 3: create a minimal test favorite, confirm it, then delete it again.",
    )
    parser.add_argument("--i-understand-this-changes-a-real-favorite", action="store_true")
    args = parser.parse_args()
    if not args.blid:
        print("Aborted: --blid is required (or set the ROOMBAPY_PRIME_BLID env var).")
        sys.exit(1)

    # Validate flags/arguments BEFORE ever prompting for credentials --
    # a bare or malformed invocation should abort immediately with a
    # clear message, the same way this project's older diagnostic
    # scripts already do, not ask for a Prime account login first and
    # only THEN explain what went wrong.
    if not (args.list_favorites or args.update_unchanged or args.update_color or args.create_and_delete_test):
        print(
            "Nothing to do -- pass --list-favorites (safe, sends nothing), --update-unchanged, "
            "--update-color, or --create-and-delete-test."
        )
        return

    needs_write_flag = args.update_unchanged or args.update_color or args.create_and_delete_test
    if needs_write_flag and not args.i_understand_this_changes_a_real_favorite:
        print("Aborted: --i-understand-this-changes-a-real-favorite is missing.")
        sys.exit(1)
    if args.update_color and not args.color:
        print("Aborted: --update-color needs --color.")
        sys.exit(1)

    username = args.username or input("iRobot account email: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("iRobot account password: ")

    if args.list_favorites:
        asyncio.run(list_favorites(username, password, args.country_code, args.blid))
        return

    if args.update_unchanged:
        asyncio.run(
            send_update_unchanged(username, password, args.country_code, args.blid, args.update_unchanged)
        )
        return

    if args.update_color:
        asyncio.run(
            send_update_color(username, password, args.country_code, args.blid, args.update_color, args.color)
        )
        return

    if args.create_and_delete_test:
        asyncio.run(create_and_delete_test(username, password, args.country_code, args.blid))
        return


if __name__ == "__main__":
    main()
