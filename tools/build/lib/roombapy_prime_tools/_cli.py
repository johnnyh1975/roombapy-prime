"""Shared CLI scaffolding for the diagnostic scripts.

WHY THIS EXISTS: the same three blocks -- account arguments, BLID
validation, credential prompting -- were copied into all ten scripts,
roughly 400 lines of pure duplication. That is not merely untidy; it
has already caused real bugs. Each copy drifted slightly, so a helper
that exists under one name in one script is absent or differently named
in the next, and a change made "everywhere" reliably misses one. During
one session alone this produced an undefined-name crash (a login helper
copied from a script that had it into one that did not) and three
separate cases of a fix landing in the standalone script but not its
session-runner twin.

DELIBERATELY NOT A FRAMEWORK. These are three small, independently
usable functions, not a base class or a run() harness that owns the
control flow. The scripts differ genuinely from one another -- staged
risk gates, several actions each, different confirmation flows -- and a
framework that tried to unify all of that would either grow endless
parameters or force scripts to fight it. Each function here removes one
specific piece of copied code and nothing more; a script can use all
three, one, or none.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys


def add_account_arguments(parser: argparse.ArgumentParser) -> None:
    """Adds the three arguments every diagnostic script needs, with
    their environment-variable fallbacks.

    The env vars matter more than they look: a field tester runs
    several of these commands in a row, and retyping a 32-character
    BLID each time is exactly where transcription errors come from.
    ROOMBAPY_PRIME_BLID was added after a tester asked for precisely
    this."""
    parser.add_argument(
        "--username",
        default=os.environ.get("ROOMBAPY_PRIME_USERNAME"),
        help="iRobot account email. Falls back to ROOMBAPY_PRIME_USERNAME, then prompts.",
    )
    parser.add_argument(
        "--country-code",
        default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"),
        help="Two-letter country code of your iRobot account. Falls back to "
        "ROOMBAPY_PRIME_COUNTRY, then defaults to US.",
    )
    parser.add_argument(
        "--blid",
        default=os.environ.get("ROOMBAPY_PRIME_BLID"),
        help="The exact target device -- never 'first device found'. Falls back to "
        "ROOMBAPY_PRIME_BLID.",
    )


def require_blid(args: argparse.Namespace) -> None:
    """Exits with a clear message if no BLID was given.

    Deliberately NOT argparse's own required=True: several scripts have
    a reconnaissance action that legitimately runs without a target
    device, and making it globally required would block exactly the
    safe, read-only stage a tester should start with. Checking here
    lets each script decide when it actually needs one."""
    if not args.blid:
        print("Aborted: --blid is required (or set the ROOMBAPY_PRIME_BLID env var).")
        sys.exit(1)


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """Returns (username, password), prompting only for what's missing.

    The password is deliberately never a command-line argument -- it
    would land in shell history and in any pasted terminal output, and
    these scripts are routinely pasted into issue reports.
    ROOMBAPY_PRIME_PASSWORD exists for unattended runs; getpass keeps
    it off the screen otherwise.

    Call this AFTER validating actions and safety flags. Asking for
    credentials before knowing whether the command is even valid means
    a tester types their password and only then learns they forgot a
    flag -- a real complaint from a field tester, and the reason every
    script now validates first."""
    username = args.username or input("iRobot account email: ")
    password = (
        os.environ.get("ROOMBAPY_PRIME_PASSWORD")
        or getpass.getpass("iRobot account password: ")
    )
    return username, password
