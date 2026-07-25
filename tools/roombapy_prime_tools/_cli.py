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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp

from roombapy_prime.diagnostics import Report
from roombapy_prime.auth import is_prime_sku, login
from roombapy_prime.prime_factory import PrimeFactory


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


def field(obj, name: str, default=None):
    """Reads a field whether the object is a dict or a typed model.

    THIRD OCCURRENCE OF THE SAME BUG (this session). Several REST
    wrappers return plain `list[dict]` -- get_active_map_versions()
    among them -- while others return parsed models, and the call sites
    could not tell which. getattr() on a dict silently returns the
    default, so the failure is never an error: it is a report full of
    `None` that looks like the robot had nothing to say.

    Field cases it produced, all reported as puzzling results rather
    than crashes:
      - the pad check reporting "no operatingMode in regions" for a
        payload that visibly carried one on every region
      - --list-maps printing "name='(unnamed)'  --p2map-id None" for a
        map that certainly has both
      - the map-version pre-flight reporting "no active_p2mapv_id"

    Use this instead of getattr() for anything that crossed a REST or
    MQTT boundary."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def pick_robot_interactively(login_result, target_blid: str | None) -> str | None:
    """Lets the user choose which robot to target when the account has
    more than one and none was named.

    Uses is_prime_sku() to mark which robots this library can actually
    talk to, and offers the single Prime one as the default -- but does
    NOT choose silently. The SKU table is explicitly incomplete for
    platforms nobody has field-tested, so an unrecognised SKU means
    "unknown", not "classic". Marking what we know while leaving the
    choice with the person is honest about that gap; auto-selecting
    would put a partly-guessed table in the path of a command that
    moves someone's robot.

    Returns None if the user declines or input is unavailable, in which
    case the caller should abort rather than pick something.

    Why this matters concretely: a field run sent an entire
    region-command session to a Roomba 980 because the library picked
    whichever robot came first in a dictionary. The 980 cannot speak
    this protocol at all -- the same log showed its ro-currentstate
    shadow returning 404, which a V4 device always has."""
    robots = getattr(login_result, "robots", None) or {}
    if target_blid or len(robots) <= 1:
        return target_blid or (next(iter(robots), None) if robots else None)

    print(f"\n== This account has {len(robots)} robots ==")
    entries = list(robots.items())
    prime_indexes = []
    for i, (blid, entry) in enumerate(entries, start=1):
        name = field(entry, "name", None) or "(unnamed)"
        sku = field(entry, "sku", None) or "?"
        if is_prime_sku(sku):
            prime_indexes.append(i)
            marker = "Prime/V4 -- this library can talk to it"
        else:
            marker = "not a known Prime SKU -- this library probably cannot talk to it"
        print(f"  [{i}] {name!r}  sku={sku}  blid={blid}")
        print(f"      {marker}")

    default = prime_indexes[0] if len(prime_indexes) == 1 else None
    if default is not None:
        prompt = f"Pick one [1-{len(entries)}], or Enter for [{default}]: "
    else:
        prompt = f"Pick one [1-{len(entries)}], or anything else to abort: "
    print("\nThe names shown are the ones you gave the robots in the iRobot app.")

    try:
        choice = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice and default is not None:
        choice = str(default)
    if not choice.isdigit() or not (1 <= int(choice) <= len(entries)):
        return None

    chosen = entries[int(choice) - 1][0]
    print(
        f"\nUsing {chosen}. To skip this prompt next time:\n"
        f"  export ROOMBAPY_PRIME_BLID={chosen}\n"
    )
    return chosen


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


def confirm(prompt: str) -> bool:
    """Interactive yes/no gate, defaulting to NO.

    Accepts y/yes/j/ja in any case; anything else -- including simply
    pressing Enter -- aborts. Deliberately restrictive: an accidental
    keystroke must not move somebody's robot.

    Was implemented seven times, identically, across the scripts.
    Different docstrings, same two lines of logic."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


@asynccontextmanager
async def connected_robot(
    username: str, password: str, country_code: str, blid: str,
    report: Report | None = None, print_summary: bool = True,
    connect_mqtt: bool = False,
) -> AsyncIterator[tuple[object, Report]]:
    """Opens a session, logs in, and guarantees the report is redacted
    and printed however the block exits.

    THE SHAPE THIS REPLACES appeared in ten scripts: create a Report,
    open an aiohttp session, build a robot, record the login, do the
    work, redact credentials out of the report, print the summary. Six
    lines of envelope around each piece of actual logic.

    That duplication is also why those scripts sit at 20-45% test
    coverage. Testing the orchestration meant rebuilding the same mock
    scaffolding ten times over, so mostly nobody did. Here it is one
    path, covered once, for every caller.

    Redaction is in a `finally` and is NOT optional: a report printed
    after an exception is exactly when someone pastes it into an issue,
    and that is the worst possible moment to leak a password.

    Printing IS optional, because the scripts genuinely differ here --
    some finish inside the async function, others hand the report back
    to main() to write it to a file first. Splitting the two along
    safety-versus-presentation lines keeps the guarantee that matters
    unconditional while accommodating the real variation."""
    report = report if report is not None else Report()
    async with aiohttp.ClientSession() as session:
        try:
            # Log in explicitly so the account's robot list can be shown
            # before anything else happens, then hand the result to the
            # factory so this is still exactly one login.
            login_result = await login(session, username, password, country_code)
            chosen = pick_robot_interactively(login_result, blid)
            if chosen is None:
                raise RuntimeError(
                    "No robot chosen -- aborting rather than picking one. Pass --blid or set "
                    "ROOMBAPY_PRIME_BLID."
                )
            blid = chosen
            _report_account_robots(login_result, blid, report)
            robot = await PrimeFactory.create_prime_robot(
                session, username, password, country_code, blid,
                login_result=login_result,
            )
            report.add("Login", "OK", f"BLID={getattr(robot, 'blid', blid)}")
            # A genuine second variant, not a deviation: the scripts that
            # send MQTT commands need the connection established before
            # they can do anything, and want it recorded as its own check
            # so a failure is attributable. The read-only REST scripts
            # would just be opening a connection they never use.
            if connect_mqtt:
                await robot.connect()
                report.add("MQTT connection", "OK")
            yield robot, report
        finally:
            report.redact(username, password)
            if print_summary:
                report.print_final_summary()


def _report_account_robots(login_result, target_blid: str, report: Report) -> None:
    """Prints every robot on the account and marks which one is being
    targeted.

    FOUND THE HARD WAY (DaRealGuGu): the login response has always
    contained the full robot list, and no script has ever shown it. His
    account has two robots, which was invisible in every run he did --
    and it only came up because a payload in his log happened to carry a
    robot_id that did not match the BLID he had logged in with.

    Also prints each robot's own robot_id next to its BLID, because
    those are NOT always the same value, and code elsewhere in this
    project quietly assumes they are (see PrimeRobot's household
    lookup). On an account where they differ, that assumption breaks
    silently."""
    robots = getattr(login_result, "robots", None) or {}
    if len(robots) <= 1:
        return

    print(f"\n== {len(robots)} robots on this account ==")
    for entry_blid, entry in robots.items():
        marker = "->" if entry_blid == target_blid else "  "
        name = field(entry, "name", None) or "(unnamed)"
        sku = field(entry, "sku", None) or "?"
        robot_id = field(entry, "robot_id", None)
        print(f"  {marker} {name!r}  sku={sku}  blid={entry_blid}")
        if robot_id and robot_id != entry_blid:
            print(f"       robot_id={robot_id}  (differs from blid)")
    print("  (-> marks the one this run targets)")
    report.add(
        "Account robots", "OK",
        f"{len(robots)} robots on this account; targeting {target_blid}",
    )
