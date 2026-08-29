"""One entry point for the write operations that have no verifier yet.

WHY ONE TOOL AND NOT TWELVE.

Nine of this library's twenty-one write operations have a dedicated
verify script. The other twelve were built from decompiled APK code and
have never been sent to a real robot -- the same starting position that
made `set_virtual_wall` fail for months while looking complete.

Twelve more scripts, each with its own name and its own arguments, would
not get run. A tester wrote "it's all just Greek to me" about a single
existing tool with three stages, and he is right: the barrier is not
willingness, it is having to work out which command to type.

So: one command, a list, and a risk level per entry.

    roombapy-prime-verify-writes --list
    roombapy-prime-verify-writes set_map_name --new-name "Test"

A CHECK MUST DERIVE ITS PAYLOAD, NEVER INVENT ONE.

Both failures in the first field run came from the same mistake, and
neither was the robot's:

  - the quiet-hours check resent an empty settings object, because that
    account had no quiet hours set. HTTP 400.
  - the schedule check built a schedule from a name and nothing else --
    no robot, no days, no time, no commands. HTTP 500.

Both were reported as "this endpoint does not work". Both endpoints were
fine. A check that makes up a payload tests the server's willingness to
accept nonsense, which is not the question being asked.

So every write check here now reads the current value first and sends
that back, changed minimally or not at all. When there is nothing to
read, the check SKIPS and says why. A robot with no schedules cannot
test schedule creation, and that is an honest result rather than a
reason to guess.

WHAT IS DELIBERATELY NOT OFFERED.

`delete_map`, `reset_robot` and `reset_robot_parts` are absent, and that
is not an oversight.

A tester who accidentally deletes a map loses weeks of mapping, every
zone and every room name on it -- to confirm a command nobody wants to
use. `reset_robot_parts` takes no part argument and appears to reset all
consumable counters at once, irreversibly. `reset_robot` is a factory
reset.

The confirmation prompt is not enough protection for those. Somebody
reading a list of things to try, in a language they only half follow,
will eventually try one. The safest interface is the one that does not
list them.

They can still be called directly from the library by anyone who has
read what they do.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from roombapy_prime.ids import id_problem

from ._cli import (
    add_account_arguments,
    confirm,
    connected_robot,
    require_blid,
    resolve_credentials,
    run_script,
)


def _failure_detail(exc: BaseException) -> str:
    """The exception, plus whatever the SERVER said about it.

    RestError already carries the response body in `raw_response`, and
    this layer threw it away: the message is only "HTTP 500 from <url>",
    which is what a tester pasted back after a failed run.

    An HTTP 500 with no body is nearly useless -- and the body was
    sitting in the exception object the whole time. Same shape as the
    two bugs in b5/b6: the information existed, the reporting layer did
    not show it.
    """
    detail = f"{type(exc).__name__}: {exc}"
    body = getattr(exc, "raw_response", None)
    if body:
        detail += f"\n      server said: {str(body)[:800]}"
    return detail


@dataclass(frozen=True)
class NoResult:
    """A check that ran cleanly and produced no evidence either way.

    "0 household(s) on this account" was reported as a passing check for
    two field rounds. It was neither a pass nor a failure -- the check
    had not answered its own question -- but the only two outcomes this
    tool could express were OK and FAILED, so it picked OK.

    Reuses the existing SKIPPED status rather than adding a new one:
    SKIPPED already means "this run produced no result", it is already
    counted separately from OK in Report.summary(), and a status the
    shared Report class does not know about would print as "?".
    """

    detail: str


def _indent_json(data: Any, indent: int = 3) -> str:
    """Pretty-printed JSON, indented to sit under the current output.

    Falls back to repr() for anything not JSON-serialisable: this
    function exists to make a response visible, so it must not be the
    thing that raises on an unexpected response.
    """
    import json

    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(data)
    pad = " " * indent
    return "\n".join(pad + line for line in text.splitlines())


def _raw_containers(raw: Any) -> list[Any]:
    """The `household_schedules` list, whatever shape `raw` turns out
    to be.

    FOUND IN THE b6 BUG HUNT, introduced by b6 itself: this was
    `(raw or {}).get("household_schedules")`, which raises
    AttributeError on any truthy non-dict -- a bare list, a string, an
    error envelope. The whole point of this release is that an
    unexpected response shape must be VISIBLE, and a crash here would
    take the diagnostic output down with it. The reporting path must
    never be the thing that fails.
    """
    if not isinstance(raw, dict):
        return []
    containers = raw.get("household_schedules")
    return containers if isinstance(containers, list) else []


def _raw_schedule_count(raw: Any) -> int:
    """How many schedule objects the SERVER sent, counted without using
    this project's models at all.

    The point of the cross-check is to be independent of the parser it
    is checking, so this walks the raw structure by hand.
    """
    total = 0
    for container in _raw_containers(raw):
        if isinstance(container, dict):
            total += len(container.get("schedules") or [])
    return total


def _parse_schedules(raw: Any) -> list[Any]:
    """The raw response read through this project's own models.

    SchedulesList.schedules is list[dict] -- raw dicts, NOT
    HouseholdSchedule instances. Reading `.options` off them returns
    None on every real response, which is exactly how this check came
    to report nothing. They have to be parsed explicitly.
    """
    from roombapy_prime.models.schedules_dnd import (
        HouseholdSchedule,
        SchedulesResponse,
    )

    if not isinstance(raw, dict):
        return []
    response = SchedulesResponse.from_json(raw)
    return [
        HouseholdSchedule.from_json(entry)
        for container in response.household_schedules
        for entry in container.schedules
        if isinstance(entry, dict)
    ]


@dataclass(frozen=True)
class WriteCheck:
    """One write operation a tester can try."""

    name: str
    #: "safe" -- reversible and visible in the app
    #: "risky" -- reversible only by recreating something by hand
    risk: str
    summary: str
    #: What the tester should look at afterwards. Every check has one:
    #: a write this library reports as accepted is not the same as a
    #: write that did what the tester expected, and that difference is
    #: what the whole virtual-wall investigation turned on.
    verify_by: str
    runner: Callable[..., Any]
    extra_args: tuple[str, ...] = ()


async def _set_map_name(robot: Any, args: argparse.Namespace) -> Any:
    maps = await robot.get_active_map_versions()
    if not maps:
        raise RuntimeError("this robot has no saved maps")
    p2map_id = args.p2map_id or maps[0].get("p2map_id")
    old = maps[0].get("name")
    print(f"   renaming map {p2map_id}")
    print(f"   from {old!r} to {args.new_name!r}")
    return await robot.set_map_name(p2map_id, args.new_name)


async def _set_map_orientation(robot: Any, args: argparse.Namespace) -> Any:
    maps = await robot.get_active_map_versions()
    if not maps:
        raise RuntimeError("this robot has no saved maps")
    p2map_id = args.p2map_id or maps[0].get("p2map_id")
    # RADIANS, not degrees -- the parameter is orientation_rad. Passing
    # 90 expecting a quarter turn would rotate the map by about 14 full
    # revolutions, landing somewhere arbitrary.
    # WITHOUT --orientation, RESEND WHAT THE MAP ALREADY HAS.
    #
    # The abort notice promises this check "resends what is already
    # there", and it did not: it sent the argument default of 0.0
    # regardless. @DaRealGuGu's map went from -0.0035 rad to 0.0 --
    # invisible on screen, and still a change made by a check that said
    # it made none.
    #
    # A tool that misdescribes itself is worse than one that refuses:
    # the whole point of these checks is that a tester can trust the
    # summary before running them.
    requested = args.orientation
    if requested is None:
        current = None
        for entry in (await robot.get_active_map_versions()) or []:
            if isinstance(entry, dict) and entry.get("p2map_id") == p2map_id:
                current = entry.get("user_orientation_rad")
                break
        requested = current if isinstance(current, (int, float)) else 0.0
        print(f"   resending the map's current orientation ({requested} rad)")
    else:
        print(f"   setting orientation {requested} rad on map {p2map_id}")
    return await robot.set_map_orientation(p2map_id, float(requested))


async def _household_id(robot: Any, args: argparse.Namespace) -> str:
    """The household id, from the argument or the account.

    Several of these calls need it, and getting it wrong is not a
    visible failure -- the request simply addresses somebody else's
    household and comes back empty.

    SO IT SAYS WHICH ONE IT PICKED. `get_household_id()` chooses the
    household containing this robot; on an account with a Classic and a
    Prime robot there is more than one to choose from, and every empty
    answer from a household-scoped endpoint has had two possible
    readings ever since. A tester's output that does not name the
    household cannot distinguish them, and that ambiguity has now cost
    three rounds.
    """
    if args.household_id:
        print(f"   household {args.household_id} (from --household-id)")
        return str(args.household_id)
    household_id = await robot.get_household_id()
    if not household_id:
        raise RuntimeError("could not determine a household id for this account")
    print(
        f"   household {household_id} (resolved from the account; override "
        "with --household-id)"
    )
    return str(household_id)


async def _set_dnd(robot: Any, args: argparse.Namespace) -> Any:
    """Sends the account's own quiet hours back unchanged.

    THE BODY THIS BUILT WAS WRONG IN THREE WAYS AT ONCE, which is the
    HTTP 400 the one live attempt returned. It took every non-empty
    attribute off the parsed response and sent them as a dict, so it:

      - used the PYTHON names (daily_start) instead of the wire keys
        (dailyStart)
      - mixed BOTH variants of a sealed class into one body
      - included `status`, which belongs to the response side and is
        not part of the write structure at all

    APK analysis settled the shape (2 August). DNDPutRequest
    serialises a DNDSchedule directly -- no envelope -- and DNDSchedule
    has exactly two mutually exclusive cases:

        {"dailyStart": int, "dailyEnd": int}
        {"endsAt": long}

    So this now picks whichever variant the account actually has and
    resends that one, built by the library's own DNDDailySchedule /
    DNDEndsAt models rather than assembled here.

    A resend check still needs something to resend: an account with no
    quiet hours is skipped rather than sent an invented value. That is
    the honest outcome, and inventing one would make this a write
    rather than a check.
    """
    from roombapy_prime.models.schedules_dnd import (  # noqa: PLC0415
        DNDDailySchedule,
        DNDEndsAt,
    )

    household_id = await _household_id(robot, args)
    current = await robot.get_dnd_settings(household_id)
    print(f"   current do-not-disturb settings: {current}")

    daily_start = getattr(current, "daily_start", None)
    daily_end = getattr(current, "daily_end", None)
    ends_at = getattr(current, "ends_at", None)

    if daily_start is not None and daily_end is not None:
        body = DNDDailySchedule(daily_start, daily_end).to_json()
    elif ends_at is not None:
        body = DNDEndsAt(ends_at).to_json()
    else:
        print(
            "   no quiet hours are configured on this account, so there is\n"
            "   nothing to resend. Set some in the iRobot app first if you\n"
            "   want to exercise this path -- inventing a value here would\n"
            "   make this a write rather than a check."
        )
        return NoResult(
            "no quiet hours set on this account. `dnd_read` is the check that "
            "helps here -- it needs no write and nobody has yet seen a "
            "populated response"
        )

    # THE VARIANT IS NAMED, and the body printed. The whole reason this
    # check failed for a release was a body nobody had looked at.
    print(f"   variant: {'daily' if 'dailyStart' in body else 'ends-at'}")
    print("   request body (as sent):")
    print(_indent_json(body, indent=5))

    if not confirm("Send the SAME settings back unchanged?"):
        return None
    return await robot.set_dnd_settings(household_id, body)


async def _order_favorite(robot: Any, args: argparse.Namespace) -> Any:
    """Moves the first favourite to position 0 -- where it already is.

    order_favorite takes ONE favourite and where to put it, not a full
    ordering. So the no-op is "put the first one first", which is the
    least destructive call this endpoint allows.
    """
    favorites = await robot.get_favorites()
    if not favorites:
        raise RuntimeError("this account has no favorites to reorder")
    first = favorites[0]
    favorite_id = getattr(first, "favorite_id", None) or getattr(first, "id", None)
    if not favorite_id:
        raise RuntimeError(f"could not read a favorite id from {first!r}")
    print(f"   moving favorite {favorite_id} to position 0 -- where it already is")
    return await robot.order_favorite(str(favorite_id), insert_at=0)


#: Keys whose values are masked before the household response is
#: printed. Both are account-level identity, neither is needed to
#: answer the question this check asks, and the output is meant to be
#: pasted into a public issue.
#:
#: Deliberately short. Everything else -- household_id,
#: household_robots[].robot_id, household_name -- IS the evidence: the
#: open question is which household holds which robot on a mixed
#: account, and masking the identifiers would remove the answer along
#: with the risk. The tool already prints BLIDs unmasked in its header.
_HOUSEHOLD_MASK_KEYS = frozenset({"owner_cognito_id", "household_users"})


def _masked(data: Any) -> Any:
    """Recursively masks _HOUSEHOLD_MASK_KEYS. Structure is preserved:
    a masked key still shows that it was present, which is itself a
    fact about the response shape."""
    if isinstance(data, dict):
        return {
            k: ("[MASKED]" if k.lower() in _HOUSEHOLD_MASK_KEYS else _masked(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_masked(item) for item in data]
    return data


def _household_entries(raw: Any) -> list[dict[str, Any]]:
    """The household dicts in a get_user_households() response.

    Handles exactly the two shapes PrimeRobot.get_household_id() already
    handles -- a bare list, or a single household as a top-level dict --
    and nothing else. No guessing at wrapper keys: an unrecognised shape
    is fully visible in the raw output printed above this, which is a
    better outcome than a plausible-looking guess that silently returns
    an empty list. That guess is the bug this check is being fixed for.
    """
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict) and ("household_id" in raw or "household_robots" in raw):
        return [raw]
    return []


def _schedule_line(index: int, schedule: Any) -> str:
    options = schedule.options
    start = options.start
    return (
        f"       [{index}] schedule_id={schedule.schedule_id!r} "
        f"enabled={options.enabled} "
        f"frequency={options.frequency} "
        f"start={None if start is None else (start.day, start.hour, start.min)}"
    )


async def _list_schedules(robot: Any, args: Any) -> Any:
    """Every household on the account, and what schedules each holds.

    WHY THIS EXISTS. A tester whose schedules demonstrably fire -- his
    missions carry `initiator: cloud` -- was told by two separate code
    paths that he has none. Both call get_schedules() with one household
    id, and an empty answer cannot distinguish "no schedules" from
    "wrong household".

    His account has a Classic and a Prime robot, and picking the right
    household on a mixed account has gone wrong in this project before.

    So: ask every household, print what each returns. If one of them has
    the schedule, that is the answer. If none does, the endpoint is not
    where these schedules live and that is a different finding.

    WHY IT PRINTS RAW JSON. The first version of this check did all
    three of its reads with getattr() against values that are plain
    dicts -- the households list, each household, and each schedule
    (SchedulesList.schedules is list[dict], as its own docstring says).
    Every one of those returns None on a dict, so the check reported
    "0 household(s) on this account" for every account in existence,
    and never called get_schedules() at all. It reported that as a
    passing check.

    The tester's output was byte-identical to what a working account
    produces. A whole field round produced no information, and the
    round before it had ended the same way.

    So the raw server response is printed FIRST, before anything in
    this project touches it, and the parsed reading is printed after it
    as a cross-check. When the two disagree, the disagreement is the
    finding -- and it is legible without another round trip to the
    tester.
    """
    raw_households = await robot.get_user_households()
    print("   raw get_user_households() response:")
    print(_indent_json(_masked(raw_households)))

    entries = _household_entries(raw_households)
    print(f"   {len(entries)} household(s) recognised in that response")
    if raw_households and not entries:
        print(
            "   NOTE: the response above is not empty but no household could be\n"
            "   read out of it. That is a parsing gap in this tool, not an empty\n"
            "   account -- the raw response above is the finding."
        )

    summary: list[dict[str, Any]] = []
    for household in entries:
        household_id = household.get("household_id") or household.get("id")
        if not household_id:
            print("     (a household entry with no household_id -- see raw above)")
            summary.append({"household_id": None, "error": "no household_id in entry"})
            continue

        print(f"\n     household {household_id}:")
        try:
            raw_schedules = await robot.get_schedules_raw(str(household_id))
        except Exception as exc:  # noqa: BLE001
            print(f"       {type(exc).__name__}: {exc}")
            summary.append({"household_id": str(household_id), "error": str(exc)})
            continue

        print("       raw get_schedules() response:")
        print(_indent_json(raw_schedules, indent=7))

        parsed = _parse_schedules(raw_schedules)
        print(f"       parsed: {len(parsed)} schedule(s)")
        for i, schedule in enumerate(parsed):
            print(_schedule_line(i, schedule))

        raw_count = _raw_schedule_count(raw_schedules)
        if raw_count != len(parsed):
            print(
                f"       DISAGREEMENT: the raw response above contains {raw_count} "
                f"schedule object(s),\n"
                f"       but this project parsed {len(parsed)}. The parser is wrong, "
                "not the account."
            )

        summary.append({
            "household_id": str(household_id),
            "raw_schedule_count": raw_count,
            "parsed_schedule_count": len(parsed),
        })

    if not summary:
        return NoResult(
            "no household could be read from this account -- see the raw "
            "response above; this is not the same as having no schedules"
        )
    if all(entry.get("raw_schedule_count") == 0 for entry in summary):
        return NoResult(
            f"{len(summary)} household(s) queried, none returned a schedule. "
            "If the app shows one, this endpoint is not where it lives"
        )
    return summary


def _raw_automation_count(raw: Any) -> int:
    """How many objects the SERVER sent, counted without the parser.

    Deliberately independent of parse_automations(): the point of a
    cross-check is to disagree with the thing it checks. Nobody has seen
    a response, so the container is unknown -- a bare list and a dict
    wrapping one are equally likely -- and this counts dicts in either
    rather than assuming a key name.
    """
    # ELEMENTS, not dicts. Counting only dicts was the first version and
    # it defeated the purpose: a list of two things this project cannot
    # read would have counted 0 against 0 parsed, reported no
    # disagreement, and passed as "an account with no automations".
    #
    # Found by the guard in tools/tests that enforces exactly this rule
    # -- which is the point of having it rather than remembering.
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, list):
                return len(value)
    return 0


async def _dnd_read(robot: Any, args: Any) -> Any:
    """Quiet hours, read only. Nothing is sent.

    THE LAST UNBUILT FEATURE OF THIS LINE, and not for want of asking.
    Nobody has ever seen a populated response: on three accounts the
    reply comes back with `status` empty and every other field null,
    because none of those users has quiet hours set in the app.

    So the library's DND model has four fields and no populated example
    behind any of them, and the write body was never investigated at
    all. The single live write attempt returned HTTP 400 -- from a check
    that resent an empty settings object, which is what writing a shape
    you have never read gets you.

    ONE PERSON WITH QUIET HOURS CONFIGURED, running this, unblocks it.
    No write, no risk, and the raw response is printed before anything
    here parses it.
    """
    household_id = await _household_id(robot, args)
    print("   GET .../settings/dnd  (read only, nothing is sent)")
    try:
        raw = await robot.get_dnd_settings_raw(household_id)
    except Exception as exc:  # noqa: BLE001
        print(f"       {_failure_detail(exc)}")
        return NoResult(f"the endpoint did not answer: {type(exc).__name__}")

    print("   raw response:")
    print(_indent_json(raw))

    from roombapy_prime.models.schedules_dnd import (  # noqa: PLC0415
        DNDStatusResponse,
    )

    parsed = DNDStatusResponse.from_json(raw) if isinstance(raw, dict) else None
    print("   parsed:", parsed)

    # READ BACK AS A CLOCK TIME, so the tester confirms the unit by
    # glancing at their app instead of doing arithmetic.
    #
    # APK analysis puts these at minutes since midnight (hour * 60 +
    # minute, range 0-1439) -- but the formula came from the general
    # schedule-conflict check rather than the DND path itself, so it is
    # well-founded rather than proven. Printing the reading turns the
    # first real response into a confirmation or a refutation on the
    # spot, with no second round.
    for label, value in (("dailyStart", getattr(parsed, "daily_start", None)),
                         ("dailyEnd", getattr(parsed, "daily_end", None))):
        if isinstance(value, int) and 0 <= value <= 1439:
            print(f"     {label} = {value}  -> {value // 60:02d}:{value % 60:02d} "
                  "if minutes since midnight")
        elif value is not None:
            print(f"     {label} = {value!r}  -> NOT a minutes-since-midnight "
                  "value; that reading is wrong")

    # COUNTED WITHOUT THE PARSER, the same rule every read check here
    # follows: a shape this project cannot read must never pass as an
    # empty account.
    raw_fields = sum(
        1 for value in (raw or {}).values() if value not in (None, {}, [], "")
    ) if isinstance(raw, dict) else 0
    parsed_fields = sum(
        1 for value in (
            (parsed.daily_start, parsed.daily_end, parsed.ends_at, parsed.status)
            if parsed else ()
        ) if value not in (None, {}, [], "")
    )
    if raw_fields != parsed_fields:
        print(f"   DISAGREEMENT: the raw response carries {raw_fields} populated "
              f"field(s),\n   this project read {parsed_fields}. The model is "
              "incomplete, not the account.")
        return NoResult(
            f"the response carries {raw_fields} populated field(s) that this "
            "project does not fully model -- the raw response above is the finding"
        )

    if not parsed_fields:
        return NoResult(
            "no quiet hours are configured on this account. That is a valid "
            "answer, but not the one that unblocks the feature -- it needs a run "
            "from someone who has set them in the iRobot app"
        )
    return [{"populated_fields": parsed_fields}]


#: rw-settings fields the iRobot app's own product profiles list as user
#: settings, with the value sets it offers. Confirmed for eleven of the
#: twelve profiles -- the twelfth is `classic-series`, which has an
#: EMPTY settings list: the new app shows Classic robots no settings at
#: all.
#:
#: `chrgLrPtrn` and `childLock` are the only two whose read AND write
#: syntax the profile records outright:
#:
#:     set: {"chrgLrPtrn": %d}     get: {"setting": "chrgLrPtrn"}
#:
#: The other 76 carry a literal "TBD" -- which means the profile author
#: did not fill it in, NOT that no command exists. @chairstacker
#: changed padDryDur and pwAreaInterval through the app and produced
#: two diagnostics files proving it, so those paths plainly work.
_SETTING_PROBES: tuple[tuple[str, str, str], ...] = (
    ("chrgLrPtrn", "charge light ring pattern", "0, 1, 2 -- write syntax confirmed"),
    # `audio`, NOT `audioVolume`. The guessed name is why this probe
    # reported "not on this robot" for a setting the robot plainly has:
    # @DaRealGuGu's key list contains `audio` and no `audioVolume`.
    #
    # A guess in a probe list does not just fail to find its field -- it
    # says the field is absent, which reads as a fact about the
    # hardware. Second time in this file that a wrong name became a
    # wrong claim about somebody's robot.
    # THE VENDOR'S OWN 24 WRITABLE KEYS, from
    # `RobotServiceHandler.settingFromKey` in app 3.0.0. Two entries
    # here were wrong, and both are ones that failed in the field:
    #
    #   `audio`        -> `audio.volume`. The app addresses the sub-key
    #                     with a dot, it does not write the whole map.
    #                     @jouwdan's write of `audio` = {"volume": 100}
    #                     got no UPDATE response at all.
    #
    #   `evacAllowed`  -> NOT IN THE LIST. It is readable and appears in
    #                     `rw-settings`, but it is not one of the 24 the
    #                     app writes. @DaRealGuGu's write of it was the
    #                     one that failed on re-read.
    #
    # Neither is proof -- a robot may accept more than its app sends.
    # But asking for a key the vendor never writes, and calling the
    # silence a bug, is how three testers spent a week on this check.
    ("audio.volume", "audio volume", "range unknown -- read the current value first"),
    # THE VALUE SETS ARE PER MODEL, so no single list belongs here.
    #
    # iRobot's product profiles give six different sets across nine
    # series: [4,6,9,12] for the 205/405, [3,4,5] for the 505,
    # [2,3,4] for the 410/510, [3,4,6] for the 705, [4,5,6] for the
    # 715 Combo. Wash frequency splits too: [5,10,15], [6,8,10],
    # [10,15,20].
    #
    # @DaRealGuGu's N185240 reads `padDryDur: 3` and
    # `pwAreaInterval: 8` -- neither is in the list this hint used to
    # print, and both are in sets iRobot publishes for other series.
    # A hint naming one model's values tells most readers the wrong
    # thing.
    # NO VALUE SET IS KNOWN FOR THESE TWO.
    #
    # `product_profile.json` carries eleven setting types and neither is
    # among them; the picker lists live in the Dart layer, unreadable.
    # Sets this project once quoted per series are in no reachable
    # source -- see docs/internal/SETTING_VALUE_SETS.md.
    # THREE OF THESE HINTS WERE WRONG, and one of them misled a tester
    # into reporting a bug that was not there.
    #
    # @chairstacker read `pwReturn = 2` described as "boolean", saw the
    # app's Mop Wash Frequency screen offering Standard/Medium/High, and
    # concluded the value was stale. It is neither boolean nor stale --
    # `ReturnByMode` (app 3.0.0) declares SIX values across two ranges,
    # and 2 is `byArea`.
    #
    # Values from the vendor's own enums, not inferred:
    #   ClearFreqType  0/1/2 = every / every 2nd / every 3rd routine,
    #                  4 = on dock return, 10/15/25/30/50 = by area
    #   ReturnByMode   0/1/2 = by room / by time / by area,
    #                  100/101/102 = mission / refill / refillAndRoom
    ("padDryDur", "mop dry duration", "hours; no valid set is documented"),
    ("pwAreaInterval", "pad wash area interval", "integer; the unit is not documented"),
    (
        "autoevacFreq",
        "auto-evacuation frequency",
        "0/1/2 = every / every 2nd / every 3rd routine; 4 = on dock return; "
        "10-50 = by area. Which subset applies depends on cap.autoevac",
    ),
    ("padWashAllowed", "pad washing allowed", "integer, not a flag"),
    ("pwHeat", "pad wash heated water", "0/1/2, not a flag"),
    (
        "pwReturn",
        "mop wash frequency",
        "TWO RANGES IN ONE FIELD: 0/1/2 = by room / by time / by area; "
        "100/101/102 = mission / refill / refillAndRoom, and they are "
        "CUMULATIVE: before+after routines / also during refills / also "
        "between rooms (app 3.0.0's own subtitles, @chairstacker)",
    ),
    ("pwTimeInterval", "pad wash time interval", "integer"),
    ("padWetness.padPlate", "pad plate wetness", "sub-key, not the whole map"),
    # PRESENT ON EVERY ROBOT SEEN SO FAR, which matters for the SKUs the
    # six unblocked settings do not reach.
    #
    # @utkjmitch's dockless 104 has 18 rw-settings keys against 28 on a
    # docked N185240, and **not one of the probed candidates existed on
    # it** -- every absent key is hardware it does not have. So the write
    # path stayed unexercised on that SKU for want of something to send.
    #
    # These six are on all three accounts and are resent unchanged, so
    # the risk is the same as the rest of this check.
    ("carpetBoost", "carpet boost", "boolean"),
    ("twoPass", "two-pass cleaning", "boolean"),
    ("noAutoPasses", "disable automatic passes", "boolean"),
    ("ecoCharge", "eco charge", "boolean"),
    ("audio", "audio settings", "a map: {\"volume\": n}"),
    ("padWetness", "pad wetness", "a map, not a scalar"),
)


def _resolve_probes(
    current: dict[str, Any],
) -> tuple[list[tuple[str, str, str, Any]], list[str]]:
    """Split the probe list into present and absent, resolving dotted
    keys against their parent map.

    A DOTTED KEY IS A WRITE ADDRESS, NOT A READ KEY. `audio.volume` and
    `padWetness.padPlate` are two of the vendor's 24 writable settings,
    but the shadow reports `audio` and `padWetness` as maps -- the
    dotted form never appears as a key of its own.

    A plain `key in current` therefore reported both as "not on this
    robot" on EVERY robot, including ones that plainly have them.
    @chairstacker's run prints `not on this robot: chrgLrPtrn,
    audio.volume, pwHeat, padWetness.padPlate` and then, a few lines
    later, `audio = {'volume': 100}` and `padWetness = {..., 'padPlate':
    4}`. Two of those four were false.

    THAT IS WORSE THAN A COSMETIC SLIP. This tool's output is field
    evidence, and "not on this robot" reads as a fact about somebody's
    hardware. It is the third time in this file that a lookup error
    became a wrong claim about a tester's robot -- the earlier two were
    the guessed name `audioVolume` and a probe list that returned a key
    list of exactly ["state"].

    WHAT THE FIX EXPOSES: the dotted keys now get probed, which means
    the DOTTED WRITE PATH gets exercised for the first time. That path
    is confirmed from `settingFromKey` and has never been sent to a
    robot. Resending `padWetness.padPlate` at its current value is the
    same risk as every other resend here, with one extra unknown -- if
    the robot does not understand the dotted address, it may store a
    literal key by that name rather than updating the sub-key. The key
    list printed above the probes is what would show it.
    """
    present: list[tuple[str, str, str, Any]] = []
    missing: list[str] = []
    for key, label, note in _SETTING_PROBES:
        if "." in key:
            parent, _, leaf = key.partition(".")
            container = current.get(parent)
            if isinstance(container, dict) and leaf in container:
                present.append((key, label, note, container[leaf]))
            else:
                missing.append(key)
            continue
        if key in current:
            present.append((key, label, note, current[key]))
        else:
            missing.append(key)
    return present, missing


def _reported_settings(raw: Any) -> dict[str, Any] | None:
    """The `reported` block of an rw-settings shadow, whatever wraps it.

    Three shapes are in play and the first attempt handled none of them
    correctly: a ShadowResponse object with a `.state` attribute, a plain
    dict `{"state": {"reported": {...}}}`, and a bare reported dict.

    Written the wrong way first -- `getattr(raw, "state", None) or raw`
    followed by `.get("reported", ...)` -- which silently produced the
    OUTER dict for the middle shape and probed a key list of exactly
    ["state"]. It reported "none of these settings exist on this robot",
    which is the worst kind of wrong: a plausible negative result.
    """
    # THE PAYLOAD IS ON `.payload`, NOT `.state`.
    #
    # `get_settings()` returns a ShadowResponse, whose fields are `topic`
    # and `payload` -- there is no `state` attribute at all. Looking for
    # one fell through to the object itself, found no `reported` in it,
    # and reported "could not read rw-settings -- this robot may be
    # EPHEMERAL tier".
    #
    # @DaRealGuGu's robot reports rw-settings perfectly well; his own
    # diagnostics list it among the seeded shadows. So the check
    # answered a question about his hardware that was really a question
    # about our attribute name -- and it answered it wrongly, in a way
    # that reads like a fact about his robot.
    #
    # Six controls were waiting on this check. It has been unable to
    # succeed since it was written.
    state = getattr(raw, "payload", None)
    if state is None:
        state = getattr(raw, "state", None)
    if state is None:
        state = raw
    # The payload carries its own `state` wrapper, so unwrap until the
    # `reported` block or the bare settings are in hand. Four shapes are
    # in play and each one has bitten this function once.
    if isinstance(state, dict) and "state" in state:
        state = state["state"]
    if hasattr(state, "reported"):
        state = state.reported
    elif isinstance(state, dict) and "reported" in state:
        state = state["reported"]
    return state if isinstance(state, dict) else None


async def _firmware_catalogue(robot: Any, args: Any) -> Any:
    """Reads `/v2/firmware`. Writes nothing, and cannot.

    The interesting part is not the data but whether the call works at
    all: the app declares this path with no HTTP method, so the verb is
    inferred. A 405 would tell us more than a success would.
    """
    raw = await robot.get_firmware_raw()
    print(f"   response type: {type(raw).__name__}")
    if isinstance(raw, dict):
        print(f"   top-level keys: {sorted(raw)}")
    elif isinstance(raw, list):
        print(f"   {len(raw)} entries")
        if raw and isinstance(raw[0], dict):
            print(f"   first entry keys: {sorted(raw[0])}")
    return raw


async def _timeline_request(robot: Any, args: Any) -> Any:
    """Asks for the mission timeline and reports the id it used.

    Publishes only -- the report comes back on a topic this check does
    not subscribe to, so a caller wanting the answer needs a watcher
    running. What this establishes is whether the request is accepted.
    """
    request_id = await robot.request_mission_timeline()
    print(f"   requested timeline with timelineRequestId={request_id}")
    print(
        "   a report carrying the same id should follow on "
        "mission/timeline/report -- run a watcher alongside this to see it"
    )
    return request_id


async def _settings_roundtrip(robot: Any, args: Any) -> Any:
    """Resends each known setting at ITS OWN CURRENT VALUE.

    NOTHING CHANGES ON THE ROBOT. Every write here sends back the value
    the robot already reports, so a success proves the path and a
    failure costs nothing. Same shape as the DND and favourites checks.

    WHY IT IS WORTH RUNNING. `set_setting()` is confirmed end to end for
    childLock -- write accepted, read back, and the robot said so out
    loud. Four more fields were written and read back successfully but
    have no observable effect to check against. Nobody has tried the
    fields the app's own product profiles list as user settings.

    If this works per field, six controls become buildable that nobody
    has today: a volume slider, the charge light ring pattern, mop dry
    duration, pad wash frequency and two evacuation settings.

    THE schedHold WARNING APPLIES TO EVERY ONE OF THEM. That field
    accepts a write, reads back changed, and the robot ignores it
    completely -- the schedule stays active in the app. So a green line
    below means "the write was accepted", never "the setting works".
    Only the app or the robot's own behaviour can say the second thing.
    """
    current = _reported_settings(await robot.get_settings())
    if current is None:
        print("   rw-settings did not come back as a readable shadow")
        return NoResult("could not read rw-settings -- this robot may be EPHEMERAL tier")

    print("   rw-settings keys on this robot:")
    print(_indent_json(sorted(current), indent=5))

    present, missing = _resolve_probes(current)
    if missing:
        # Absent is a result: the profiles say which model gets which
        # dock setting, and a mop-less robot has no pad fields.
        print(f"   not on this robot: {', '.join(missing)}")
    if not present:
        return NoResult(
            "none of the probed settings exist on this robot -- the key list "
            "above is the finding"
        )

    results = []
    for key, label, note, value in present:
        print(f"\n   {key} = {value!r}   ({label}; {note})")
        if not confirm(f"Resend {key} at its current value {value!r}?"):
            print("      skipped")
            continue
        try:
            await robot.set_setting(key, value)
        except Exception as exc:  # noqa: BLE001
            print(f"      write FAILED: {_failure_detail(exc)}")
            results.append({"key": key, "write": "failed"})
            continue

        after = _reported_settings(await robot.get_settings())
        read_back = _lookup_setting(after, key)
        agreed = read_back == value
        print(f"      write accepted; read back {read_back!r} "
              f"({'unchanged, as intended' if agreed else 'CHANGED -- unexpected'})")
        results.append({"key": key, "write": "ok", "read_back_matches": agreed})

    if not results:
        return NoResult("every field was skipped")
    return results


def _lookup_setting(settings: Any, key: str) -> Any:
    """A settings value, resolving a dotted key through its parent map.

    THE SAME BUG AS `_resolve_probes` HAD, one step later. That one was
    fixed so dotted keys get probed at all; this one still did a flat
    `settings.get("audio.volume")` on a document where `audio` is a
    nested map and the dotted form is never a key of its own.

    So a dotted write always read back `None` and always reported
    `read_back_matches: False`. @chairstacker's run shows the signature
    exactly: `audio.volume` and `padWetness.padPlate` both False, while
    `audio` and `padWetness` written as whole maps both True. Two keys
    failing and their parents passing is this bug, not the robot
    refusing dotted addresses.

    WHAT IT DOES NOT PROVE. With the comparison fixed, a dotted write
    that genuinely does not stick will now say so. Nothing here has yet
    seen a successful dotted write CONFIRMED -- the previous runs could
    not have shown one.
    """
    if not settings:
        return None
    if key in settings:
        return settings[key]
    if "." not in key:
        return None
    parent, _, child = key.partition(".")
    container = settings.get(parent)
    if isinstance(container, dict):
        return container.get(child)
    return None


async def _automations(robot: Any, args: Any) -> Any:
    """Third-party triggers and geofencing, if the endpoint is alive.

    NOT schedules -- a separate subsystem. The app has hard-coded
    service ids for August Home, Ecobee, Leviton, MyQ and Wyze, plus
    geofencing keys and behaviour options (continue cleaning, pause and
    notify, end job). Automations of the kind "when I leave the house,
    run favourite X".

    THE OPEN QUESTION IS WHETHER THE URL RESPONDS AT ALL. In the app it
    is a dead constant: one reference, a static initialiser, no reader
    -- the same signature as two other strings that turned out to be
    dead. Its real path is behind the native boundary. A second Home
    Assistant integration calls this URL, but swallows any error and
    never reads the result, so a 404 would be invisible there -- that is
    not evidence the endpoint answers.

    One read on an account with automations configured settles it. A
    404 closes the topic for good, which is worth as much as a success.
    """
    print("   GET /v1/user/automations")
    print("   (dead constant in the app -- this call is the test)")
    try:
        raw = await robot.get_automations_raw()
    except Exception as exc:  # noqa: BLE001
        print(f"       {_failure_detail(exc)}")
        return NoResult(
            f"the endpoint did not answer: {type(exc).__name__}. A refusal here "
            "closes the question rather than leaving it open"
        )

    print("   raw response:")
    print(_indent_json(raw))

    from roombapy_prime.models import parse_automations  # noqa: PLC0415

    parsed = parse_automations(raw)
    print(f"   parsed: {len(parsed)} automation(s)")
    for entry in parsed:
        print(f"     {entry.automation_id}: type={entry.automation_type} "
              f"enabled={entry.enabled} favorite={entry.favorite_id}")

    # COUNTED WITHOUT THE PARSER, so a shape we do not recognise cannot
    # pass as an empty account. That confusion is the whole reason this
    # tool prints raw responses at all: for three field rounds a derived
    # zero hid the fact that nothing had been read.
    #
    # The container is unknown here -- the endpoint has never answered
    # anyone -- so this counts dicts in whatever list it finds rather
    # than assuming a key.
    raw_count = _raw_automation_count(raw)
    if raw_count != len(parsed):
        print(f"   DISAGREEMENT: the raw response holds {raw_count} object(s),\n"
              f"   this project parsed {len(parsed)}. The parser is wrong, not "
              "the account.")
        return NoResult(
            f"the endpoint answered with {raw_count} object(s) that this project "
            "could not read -- the raw response above is the finding"
        )

    if not parsed:
        return NoResult(
            "the endpoint answered and held nothing readable -- either an account "
            "with no automations, or a shape with no objects in it at all; the raw "
            "response above tells them apart"
        )
    return [{"automations": len(parsed)}]


async def _mission_history(robot: Any, args: Any) -> Any:
    """Reads the mission history and reports its SHAPE.

    WHY THIS IS WORTH A CHECK OF ITS OWN. Four Home Assistant sensors --
    clean streak, last mission, last duration, area cleaned today -- read
    a store that only the Classic path fills. The Prime path could fill
    it from this endpoint, and the mapping is a small function once the
    wire shape is known.

    It is not known. The endpoint is implemented and the model parses the
    vendor's own sample from the app's raw resources (20 entries,
    `atlantis` platform) -- but no Prime robot has ever been asked, and
    that sample is a Classic-era platform.

    TWO THINGS CAN GO WRONG AND THEY LOOK ALIKE. The envelope key is a
    guess: the parser tries `missions` and `history`, neither confirmed.
    So a response full of missions can parse to an empty list and read
    exactly like a robot with no history. This check compares the two and
    says which it is.

    Read-only. Nothing is sent to the robot.
    """
    raw = await robot.get_mission_history(robot.blid)
    print("   raw response, outermost level:")
    if isinstance(raw, dict):
        print(f"     dict with keys {sorted(raw)}")
        for key, value in raw.items():
            kind = f"list[{len(value)}]" if isinstance(value, list) else type(value).__name__
            print(f"       {key}: {kind}")
    elif isinstance(raw, list):
        print(f"     list with {len(raw)} entries")
    else:
        print(f"     {type(raw).__name__}")

    from roombapy_prime.models.mission_history import parse_mission_history

    parsed = parse_mission_history(raw)
    print(f"\n   parser produced {len(parsed)} entries")

    # The discriminator. A response that clearly holds missions but
    # parses to nothing means the envelope key is wrong -- a different
    # and much more fixable finding than an empty history.
    raw_count = 0
    if isinstance(raw, list):
        raw_count = len(raw)
    elif isinstance(raw, dict):
        raw_count = max(
            (len(v) for v in raw.values() if isinstance(v, list)), default=0
        )
    if raw_count and not parsed:
        print(f"   ** the response carries {raw_count} entries and the parser saw "
              "none -- the envelope key is wrong, not the history **")

    if parsed:
        first = parsed[0]
        print("\n   first entry, field by field:")
        for name in (
            "mission_id", "robot_id", "start_time", "timestamp", "duration_m",
            "square_feet_covered", "error_code", "done_raw",
            "number_of_evacuations", "minutes_running",
        ):
            print(f"     {name:24} {getattr(first, name, '(not modelled)')!r}")

        # A MALFORMED MISSION ID LOOKS FINE UNTIL IT IS USED AS A KEY.
        #
        # `mission_id` is a ULID (26-char Crockford base32). An empty,
        # truncated or lowercased one reads as an ordinary string here
        # and then silently fails to match anything downstream. Naming
        # the fault costs one line and saves a round of "why does this
        # mission never correlate".
        #
        # Reported, not enforced: the robot is the authority on its own
        # ids, and a format this project inferred is not grounds to
        # call real data wrong. If this fires on a real robot, the
        # assumption is what needs revisiting.
        problem = id_problem(getattr(first, "mission_id", None))
        if problem is not None:
            print(
                f"\n   ** mission_id is not a well-formed ULID: {problem} **\n"
                "      (expected 26 chars of Crockford base32. Please "
                "report this -- it may mean the format assumption is "
                "wrong, not that your robot is.)"
            )
        print("\n   raw keys on the first entry:")
        source = raw if isinstance(raw, list) else next(
            (v for v in raw.values() if isinstance(v, list) and v), []
        )
        if source and isinstance(source[0], dict):
            print(_indent_json(sorted(source[0]), indent=5))

    if not parsed and not raw_count:
        return NoResult(
            "this robot reports no mission history at all -- a valid answer, "
            "and not the one that unblocks the four sensors"
        )
    return {"raw_count": raw_count, "parsed_count": len(parsed)}


async def _clean_score(robot: Any, args: Any) -> Any:
    """Per-room cleanliness values, if the request body is right.

    The endpoint and its response keys are confirmed from the APK:
    `clean_scores[].regions[]`, each region carrying `clean_score` (a
    float from 0.0 to 1.0), `region_id` and `updated_ts`. Accumulated
    state per room rather than a mission result -- the data behind
    Smart Clean.

    WHAT IS NOT CONFIRMED IS THE REQUEST BODY. The app calls this
    through fetchCleanScoreDataForMap(), so the body sits behind the
    native boundary exactly as /v1/time-estimates did. There it turned
    out to be a single `{"robot_id": ...}`; the method name here names a
    map, so `{"p2map_id": ...}` is the analogous guess.

    Which is why this check prints the body it sends and the raw
    response it gets. A 4xx naming a field would tell us more than a
    success would, and either beats a status code on its own -- three
    field rounds have already ended that way.
    """
    maps = await robot.get_active_map_versions()
    entries = maps if isinstance(maps, list) else []
    if not entries:
        return NoResult("no maps on this robot, so there is nothing to score")

    summary: list[dict[str, Any]] = []
    for entry in entries:
        p2map_id = entry.get("p2map_id") if isinstance(entry, dict) else None
        if not p2map_id:
            continue
        print(f"\n   map {p2map_id}")
        print(f"   GET /v1/p2maps/clean-score?p2map_id={p2map_id}")
        try:
            raw = await robot.get_clean_score_raw(str(p2map_id))
        except Exception as exc:  # noqa: BLE001
            print(f"       {_failure_detail(exc)}")
            summary.append({"p2map_id": str(p2map_id), "error": str(exc)})
            continue
        print("       raw response:")
        print(_indent_json(raw, indent=7))

        # Parsed alongside the raw, never instead of it. The keys are
        # confirmed from the app's own response parser, so a mismatch
        # here means either the server changed or the confirmation was
        # misread -- and either is worth seeing in the same run rather
        # than a round later.
        from roombapy_prime.models import CleanScoreResponse  # noqa: PLC0415

        parsed = CleanScoreResponse.from_json(raw)
        rooms = [r for entry in parsed.clean_scores for r in entry.regions]
        print(f"       parsed: {len(rooms)} room score(s)")
        for room in rooms:
            print(f"         region {room.region_id}: {room.clean_score}"
                  f"  (updated {room.updated_ts})")
        raw_rooms = sum(
            len(entry.get("regions") or [])
            for entry in ((raw or {}).get("clean_scores") or [])
            if isinstance(entry, dict)
        ) if isinstance(raw, dict) else 0
        if raw_rooms != len(rooms):
            print(f"       DISAGREEMENT: the raw response holds {raw_rooms} region(s),\n"
                  f"       this project parsed {len(rooms)}. The parser is wrong.")
        summary.append({
            "p2map_id": str(p2map_id),
            "raw_room_count": raw_rooms,
            "parsed_room_count": len(rooms),
        })

    if not summary:
        return NoResult("no map carried a p2map_id to ask about")
    if all("error" in entry for entry in summary):
        return NoResult(
            "every map was rejected -- the request body is the likely reason, "
            "and the server's own words are above"
        )
    return summary


async def _create_and_delete_schedule(robot: Any, args: argparse.Namespace) -> Any:
    """Creates a schedule and offers to delete it again.

    Paired on purpose. Creating alone leaves a stray entry in the
    tester's app that they have to find and remove; deleting alone needs
    a schedule they already care about.
    """
    household_id = await _household_id(robot, args)

    # BUILT FROM AN EXISTING SCHEDULE, not from scratch.
    #
    # A first version sent ScheduleOptions(name=..., enabled=False),
    # which serialises to exactly two fields. No asset_id, no frequency,
    # no start time, no commands -- a schedule that says nothing about
    # which robot, on which days, at what time, to do what.
    #
    # The server answered HTTP 500 (@DaRealGuGu). That was reported as
    # "create_schedules does not work", and it is not: the request shape
    # was fine and the CONTENT was meaningless.
    #
    # Same mistake as the quiet-hours check, which resent an empty
    # settings object and got HTTP 400. Both were my checks inventing a
    # payload rather than deriving one from what the robot already has.
    #
    # So this copies an existing schedule, changes the name, and marks it
    # disabled. A robot with no schedules cannot run this check -- which
    # is the honest outcome, not a reason to invent one.
    try:
        raw = await robot.get_schedules_raw(household_id)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not read existing schedules: {exc}") from exc

    # PARSED, NOT READ OFF THE DICT.
    #
    # SECOND OCCURRENCE, found by sweeping for the first (b5). This read
    # `getattr(schedule, "options", None)` -- and SchedulesList.schedules
    # is list[dict], so that returned None for every schedule that has
    # ever existed. `template` was always None, so this check ALWAYS took
    # the branch below and told the tester he had no schedules.
    #
    # It is the second of the "two separate code paths" that told one
    # tester he had none. b5 fixed the other one and improved this
    # branch's WORDING, which made the message clearer while leaving it
    # just as wrong.
    parsed = _parse_schedules(raw)
    template = parsed[0].options if parsed else None

    if template is None:
        # SAY WHAT WAS ASKED, not just what came back.
        #
        # A tester with schedules that demonstrably fire got told he had
        # none. The message was worse than useless: it sent him to
        # create a schedule he already had.
        #
        # The endpoint is per-HOUSEHOLD, so an empty response can mean
        # "no schedules" or "wrong household" -- and on an account with
        # both a Classic and a Prime robot, picking the right one has
        # bitten this project before. Printing the id and the raw shape
        # lets a report distinguish them instead of guessing.
        containers = _raw_containers(raw)
        raw_count = _raw_schedule_count(raw)
        print(
            f"   get_schedules() returned {len(containers)} container(s) for\n"
            f"   household {household_id}, holding {raw_count} schedule(s).\n"
        )
        if raw_count:
            # The server sent schedules and none of them parsed. That is
            # this project's bug, and saying "you have no schedules"
            # would report it as the account's.
            print(
                "   None of them could be read. The server sent schedules and\n"
                "   this tool failed to parse them -- that is a bug here, not\n"
                "   something wrong with your account. Please report this\n"
                "   output.\n"
            )
            return None
        print(
            "   That is either an account with no schedules, or the wrong\n"
            "   household -- this endpoint is per-household, and an account\n"
            "   with more than one robot can have more than one.\n"
            "\n"
            "   If the iRobot app DOES show a schedule for this robot, that\n"
            "   is a finding worth reporting rather than a reason to create\n"
            "   another one. Re-run with --household-id to try a different\n"
            "   household."
        )
        return None

    from dataclasses import replace  # noqa: PLC0415

    # FIELDS DELIBERATELY DROPPED FROM THE COPY.
    #
    # `created_time` is assigned by the server. Copying a template means
    # replaying the ORIGINAL schedule's timestamp into a create -- in
    # @DaRealGuGu's b7 run, literally "2026-08-01T18:15:09.211030+00:00"
    # from the schedule being copied. That is now the last candidate
    # standing for the HTTP 500 that create has returned on every
    # attempt:
    #
    #   {"errorType": "AspenError.InternalError",
    #    "errorMessage": "Internal error"}
    #
    # A server crash rather than a validation error, which fits a value
    # the server expected to assign itself.
    #
    # The other two candidates are out. `initiator` is not needed: his
    # working, server-stored schedules do not carry it either. And
    # `is_smart_clean_fav`, which the server sends and this project does
    # not model, does not exist anywhere in the iRobot APK -- the server
    # added it without its own app knowing about it, so its absence from
    # a request cannot plausibly crash the server.
    #
    # If this run still returns 500, `created_time` is cleared too and
    # the next question is the endpoint itself rather than the payload.
    dropped = {"created_time": template.created_time}
    options = replace(
        template, name=args.schedule_name, enabled=False, created_time=None
    )
    print("   copied from an existing schedule, disabled, renamed")
    # PRINTED, so a failing run stays readable without another round
    # trip. Three field rounds on this one check have already ended with
    # a status code and no way to tell what was actually sent.
    # Only report a field as dropped if it was actually there. Found in
    # the b8 bug hunt: a template carrying no created_time still printed
    # "deliberately NOT sent: created_time = None", claiming an omission
    # that never happened. For output whose whole purpose is to stay
    # readable after a failure, that is the wrong kind of inaccuracy.
    actually_dropped = {k: v for k, v in dropped.items() if v is not None}
    if actually_dropped:
        print("   deliberately NOT sent (server-assigned):")
        for key, value in actually_dropped.items():
            print(f"     {key} = {value!r}")
    else:
        print("   (template carried no server-assigned fields to drop)")
    print(f"   creating a DISABLED schedule named {args.schedule_name!r}")

    # THE BODY THAT IS ABOUT TO GO OUT, printed before it goes.
    #
    # @chairstacker's run got HTTP 500 here, and neither he nor this
    # project could say what was in the request -- so the round produced
    # a status code and nothing else. Two candidates were arguable from
    # the code alone (a copied `created_time`, which the server assigns,
    # and region commands missing `initiator`, which the app adds at
    # send time and stored schedules do not carry) and there was no way
    # to tell them apart without guessing.
    #
    # Printing it costs nothing and ends that class of round. Same
    # reasoning as the raw response in `schedules`: show the wire, do
    # not infer it.
    # THE ACTUAL BODY, wrapper included.
    #
    # This printed options.to_json() -- the inner object -- while
    # create_schedules() wraps it. Four field rounds were spent staring
    # at a payload that never went over the wire, in the one check whose
    # purpose is to show what did. Printing the wrapper is what made the
    # missing `options` level visible at all.
    print("   request body (as sent):")
    print(_indent_json({"schedules": [{"options": options.to_json()}]}, indent=5))

    created = await robot.create_schedules(household_id, [options])
    print(f"   created: {created}")

    if confirm("Delete the schedule that was just created?"):
        schedule_id = (created or {}).get("household_schedule_id")
        if schedule_id:
            await robot.delete_schedule(household_id, str(schedule_id))
            print("   deleted again")
        else:
            print(
                "   no household_schedule_id in the response -- please remove "
                "the schedule in the iRobot app by hand"
            )
    return created


async def _time_estimates(robot: Any, args: argparse.Namespace) -> Any:
    """Reads per-room time estimates. A GET in everything but method.

    Listed here despite being read-only because this file is where a
    tester looks for "things to try", and the response shape is the one
    thing still unknown about this endpoint. The request body was traced
    from native code -- a single {"robot_id": blid} -- but nobody has
    seen what comes back.
    """
    result = await robot.get_time_estimates()
    print("   response:")
    print(f"   {result}")
    return result


async def _initiator_probe(robot: Any, args: Any) -> Any:
    """Sends one harmless command under a made-up initiator.

    THE QUESTION: does the server validate `initiator` at all?

    ANSWERED, ON ONE ROBOT. @chairstacker's G185020 chirped on a `find`
    sent as `homeassistant`. The server accepts an initiator that is not
    in the vendor's own list of 25, so the field is a free string rather
    than a registry -- and this project can stop reporting itself as the
    local iRobot app. One robot is not every robot, and the account may
    matter, so this check stays.

    `Initiator` (app 3.0.0) lists 25 values, and ten of them are named
    third parties -- alexa, siri, google, ifttt, homey, openHAB, yonomi,
    bosch, swisscom, alismart. Two of those are home-automation
    platforms exactly like this one. `homeassistant` is not among them.

    Either iRobot never registered us, or the field is a free string the
    server passes through. Nobody knows which, and the difference
    decides whether this integration can identify itself in a robot's
    own mission history instead of impersonating the local iRobot app.

    WHY `find` AND NOT A REAL COMMAND. It makes the robot chirp and
    changes nothing else -- no mission starts, no setting moves, and the
    tester can hear whether it arrived. A start command would answer the
    same question and cost a cleaning run to undo.

    WHAT SUCCESS LOOKS LIKE, and it takes two observations rather than
    one: the robot chirps (the command was accepted) AND the mission
    history afterwards carries `homeassistant` (the value survived the
    round trip). Either half alone is inconclusive -- a broker
    acknowledgement says the publish left, not that the robot honoured
    the field.
    """
    initiator = getattr(args, "initiator", None) or "homeassistant"
    accepted = await robot.send_simple_command("find", initiator=initiator)
    return {
        "initiator_sent": initiator,
        "publish_acknowledged": accepted,
        "note": (
            "acknowledgement is the broker, not the robot -- listen for "
            "the chirp, then check the mission history"
        ),
    }


async def _initiator_mission(robot: Any, args: Any) -> Any:
    """Starts a REAL whole-house clean under a made-up initiator.

    THE TIMELINE IS WHERE THE ANSWER IS. @chairstacker photographed the
    iRobot app's Timeline for three missions and it names the initiator
    each time:

        HA start button      ->  LocalApp
        favourite in the app ->  RmtApp
        scheduled mission    ->  Cloud

    So the app renders the field a mission was started with. `find`
    never could have shown this -- it is not a mission and makes no
    timeline entry, which is why the earlier version of the
    `custom_initiator` check asked for something impossible.

    THIS MOVES THE ROBOT, and that is the whole point: only a mission
    produces a timeline entry to read.

    AND NOBODY IS ASKED TO RUN IT. The question it answers changes
    nothing: this integration's own "Started by" sensor already labels
    `localApp` as "Home Assistant", so a user sees the right thing in
    Home Assistant today. The only gain would be the label inside
    iRobot's own app -- against `localApp`, which is field-confirmed
    across several robots and commands.

    Kept because it costs nothing until it is run, and somebody
    curious about their own timeline should not have to build it.
    An open question is not automatically worth closing.

    WHAT TO LOOK FOR, in the app's Timeline for the run that follows:

      - "Homeassistant" or similar  -> the field is a free string and
        this project can identify itself instead of impersonating the
        local iRobot app
      - "LocalApp" or blank         -> the server normalised or dropped
        an unregistered value; keep sending `localApp`
      - no mission at all           -> the server refused the command
        over the initiator, which is the one outcome that would settle
        it negatively
    """
    initiator = getattr(args, "initiator", None) or "homeassistant"
    accepted = await robot.send_simple_command("start", initiator=initiator)
    return {
        "initiator_sent": initiator,
        "publish_acknowledged": accepted,
        "note": (
            "a clean should now be starting -- read the app's Timeline "
            "entry for it, not the broker acknowledgement"
        ),
    }


CHECKS: tuple[WriteCheck, ...] = (
    WriteCheck(
        name="schedules",
        risk="read",
        summary="lists every household and the schedules in each (writes nothing)",
        verify_by=(
            "nothing to check in the app -- please paste the output if your "
            "app shows a schedule this does not"
        ),
        runner=lambda robot, args: _list_schedules(robot, args),
    ),
    WriteCheck(
        name="dnd_read",
        risk="read",
        summary="quiet hours, read only -- nothing is sent",
        verify_by=(
            "nothing to check in the app. This is only useful from an account "
            "that HAS quiet hours configured -- please set some first if you "
            "have not, then paste the whole output. Nobody has ever seen a "
            "populated response, which is why this feature does not exist yet"
        ),
        runner=lambda robot, args: _dnd_read(robot, args),
    ),
    WriteCheck(
        name="firmware_catalogue",
        # READ, not "safe". @utkjmitch hit the seam: the gate demanded
        # --i-understand-this-writes-to-my-robot and told him the check
        # "writes to your real robot", while its own banner said it
        # writes nothing. The gate was right about the flag and wrong
        # about the reason, which is worse than either alone.
        risk="read",
        summary="reads the available firmware releases -- writes nothing",
        verify_by=(
            "there is nothing to verify on the robot; this only asks whether "
            "the endpoint answers. `FirmwareRequest` in the app declares the "
            "path and no HTTP method, so GET was a guess. It resolves: one "
            "account got a **403** -- the endpoint exists and the consumer "
            "Cognito role has no execute-api:Invoke on it. That is a "
            "useful result and not a failure of your account. Paste "
            "whatever comes back, including the shape of the envelope, which "
            "nothing describes anywhere. Nothing changes on the robot, so "
            "there is nothing to check in the iRobot app"
        ),
        runner=lambda robot, args: _firmware_catalogue(robot, args),
    ),
    WriteCheck(
        name="custom_initiator",
        # Makes the robot chirp and changes nothing else. The `find`
        # command is the one simple verb confirmed on Prime.
        risk="safe",
        summary="sends `find` claiming to be `homeassistant` instead of `localApp`",
        verify_by=(
            "DOES THE ROBOT CHIRP? That is the whole test, and a chirp "
            "means the server accepted a command claiming to be "
            "`homeassistant` -- so the field is not validated against a "
            "registry and this project can identify itself instead of "
            "impersonating the local iRobot app. Silence means the "
            "opposite, and `--initiator openHAB` then separates 'iRobot "
            "does not know us' from 'iRobot does not check'. "
            "NOT THE CLEANING HISTORY: an earlier version of this text "
            "asked for that too, and @chairstacker duly reported a chirp "
            "with no history entry. `find` is not a mission and creates "
            "no record -- the check was impossible, and his robot had "
            "already answered the question"
        ),
        runner=lambda robot, args: _initiator_probe(robot, args),
    ),
    WriteCheck(
        name="timeline_request",
        # Publishes a request and changes nothing. Same seam.
        risk="read",
        summary="asks the robot to send its mission timeline now",
        verify_by=(
            "the request is accepted -- that much is confirmed on two "
            "accounts. What an IDLE robot does not do is answer: a 35-second "
            "watch on a single connection produced no report (@jouwdan). So "
            "silence here is expected, not a failure. The open question is "
            "whether a request DURING a mission pulls a report earlier than "
            "the robot would have sent one anyway, and that needs a watcher "
            "running while the robot drives. Nothing changes on the robot, "
            "so there is nothing to check in the iRobot app"
        ),
        runner=lambda robot, args: _timeline_request(robot, args),
    ),
    WriteCheck(
        name="settings_roundtrip",
        risk="safe",
        summary="resends each rw-settings value unchanged -- nothing changes",
        verify_by=(
            "nothing should change, and that is the point. Then please open "
            "the iRobot app and check that the settings screen still shows the "
            "same values -- accepted and read back is NOT the same as working. "
            "schedHold does both and the robot ignores it entirely. Paste the "
            "whole output including the key list at the top"
        ),
        runner=lambda robot, args: _settings_roundtrip(robot, args),
    ),
    WriteCheck(
        name="automations",
        risk="read",
        summary="third-party triggers and geofencing -- NOT schedules (writes nothing)",
        verify_by=(
            "please paste the whole output. This endpoint looks dead in the app, "
            "so a refusal is as useful as a success -- it closes the question. "
            "Most useful from an account that has automations set up in the app"
        ),
        runner=lambda robot, args: _automations(robot, args),
    ),
    WriteCheck(
        name="mission_history",
        risk="read",
        summary="reads the mission history and reports its shape -- nothing is sent",
        verify_by=(
            "nothing to check on the robot or in the app; the output IS the "
            "result. Please paste all of it, including the field list -- four "
            "Home Assistant sensors are waiting on this shape"
        ),
        runner=lambda robot, args: _mission_history(robot, args),
    ),
    WriteCheck(
        name="clean_score",
        risk="read",
        summary="per-room cleanliness values from an endpoint we have never called (writes nothing)",
        verify_by=(
            "nothing to check in the app -- please paste the whole output, "
            "including a failure: the request body is a guess and a rejection "
            "naming a field would tell us more than a success"
        ),
        runner=lambda robot, args: _clean_score(robot, args),
    ),
    WriteCheck(
        name="time_estimates",
        risk="read",
        summary="reads per-room time estimates (writes nothing)",
        verify_by=(
            "nothing to check in the app -- please paste the response, it is "
            "the last unknown about this endpoint"
        ),
        runner=_time_estimates,
    ),
    WriteCheck(
        name="set_map_name",
        risk="safe",
        summary="renames a map",
        verify_by="open the iRobot app and look at the map's name",
        runner=_set_map_name,
        extra_args=("--new-name", "--p2map-id"),
    ),
    WriteCheck(
        name="set_map_orientation",
        risk="safe",
        summary=(
            "resends a map's current orientation, changing nothing. "
            "--orientation takes RADIANS to rotate it instead: "
            "0 leaves it as is, 1.5708 is a quarter turn, 3.1416 is upside down"
        ),
        verify_by="open the iRobot app; the map should appear rotated",
        runner=_set_map_orientation,
        extra_args=("--orientation", "--p2map-id"),
    ),
    WriteCheck(
        name="set_dnd_settings",
        risk="safe",
        summary="resends your existing quiet hours unchanged",
        verify_by="check quiet hours in the app -- they should be unchanged",
        runner=_set_dnd,
    ),
    WriteCheck(
        name="order_favorite",
        risk="safe",
        summary="resends your favourites in their current order",
        verify_by="check the favourites list in the app -- same order",
        runner=_order_favorite,
    ),
    WriteCheck(
        name="initiator_mission",
        # Starts a real clean. Nothing else produces a timeline entry.
        risk="risky",
        summary=(
            "starts a REAL whole-house clean claiming to be "
            "`homeassistant` -- NOBODY IS BEING ASKED TO RUN THIS"
        ),
        verify_by=(
            "OPEN THE APP'S TIMELINE for the mission that just started. It "
            "names the initiator: @chairstacker's shows LocalApp for a Home "
            "Assistant start, RmtApp for a favourite pressed in the app, and "
            "Cloud for a schedule. If yours names Home Assistant, the field "
            "is a free string and this project can stop impersonating the "
            "local iRobot app. If it says LocalApp or nothing, the server "
            "normalised an unregistered value. If no mission appears at "
            "all, it refused the command -- and that is the answer too"
        ),
        runner=lambda robot, args: _initiator_mission(robot, args),
        extra_args=("--initiator",),
    ),
    WriteCheck(
        name="schedule_create_delete",
        risk="risky",
        summary="creates a schedule, then offers to delete it again",
        verify_by=(
            "watch the app: a new schedule appears, then disappears. If the "
            "delete step fails, remove it in the app by hand"
        ),
        runner=_create_and_delete_schedule,
        extra_args=("--schedule-name", "--household-id"),
    ),
)

_RISK_NOTE = {
    "read": "reads only, changes nothing",
    "safe": "reversible, and resends what is already there",
    "risky": "creates something you may have to remove by hand",
}


def _print_list() -> None:
    print("\nWrite operations that have never been tested on real hardware.\n")
    for check in CHECKS:
        args = " ".join(check.extra_args)
        print(f"  [{check.risk:5}] {check.name}")
        print(f"           {check.summary}")
        print(f"           check afterwards: {check.verify_by}")
        if args:
            print(f"           options: {args}")
        print()
    print("Run one with:")
    print("  roombapy-prime-verify-writes <name> [options]\n")
    print(
        "NOT listed, deliberately: delete_map, reset_robot and\n"
        "reset_robot_parts. Deleting a map costs weeks of mapping and every\n"
        "zone on it; the resets are irreversible. Confirming those commands\n"
        "work is not worth what it costs to find out.\n"
    )


def _survive_a_narrow_console() -> None:
    """Stops a check tickmark from killing the run on Windows.

    The status lines print U+2713. A cp1252 console cannot encode it, so
    `print()` raises UnicodeEncodeError **before the check does any
    work** -- the tool dies on its own decoration, and the error names an
    encoding rather than anything the tester did (@utkjmitch).

    `PYTHONUTF8=1` fixes it from outside; nobody should have to know
    that. Falling back to `errors="replace"` costs a mangled tickmark on
    a console that could not have shown it anyway.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - platform specific
            pass


#: This package's own version, for the skew check below.
#:
#: Read from installed metadata rather than hardcoded: a literal here
#: would be a third place to forget on release, and the two that already
#: exist are enforced by `scripts/check_version_pin.py`.
try:
    from importlib.metadata import version as _dist_version

    TOOLS_VERSION = _dist_version("roombapy-prime-tools")
except Exception:  # noqa: BLE001
    TOOLS_VERSION = "unknown"


def _warn_on_version_skew() -> None:
    """Says so when the tools and the core they drive disagree.

    THESE ARE TWO DISTRIBUTIONS. `pip install --upgrade roombapy-prime`
    updates the library and leaves `roombapy-prime-tools` where it was,
    so a tester can upgrade, run a check, and get output from the
    previous release without a hint that anything is stale.

    @chairstacker hit exactly that: he installed 0.3.0b6, ran
    `custom_initiator`, and the prompt still asked him for a cleaning
    history entry -- an instruction b6 had removed precisely because
    `find` creates no history. He then reported the missing entry as a
    result, which cost him a step and me a paragraph explaining it.

    A warning rather than a refusal: an older tool against a newer core
    usually works, and stopping someone mid-field-test over a version
    string would be worse than the confusion it prevents.
    """
    try:
        from roombapy_prime import __version__ as core_version  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    if core_version == TOOLS_VERSION:
        return
    print(
        f"  NOTE: roombapy-prime-tools {TOOLS_VERSION} against "
        f"roombapy-prime {core_version}.\n"
        f"  These are separate packages -- upgrading the library does not "
        f"upgrade this tool.\n"
        f"  If a prompt below asks for something that makes no sense, that "
        f"is why:\n"
        f'    pip install --upgrade "roombapy-prime-tools@'
        f'git+https://github.com/johnnyh1975/roombapy-prime.git'
        f'@v{core_version}#subdirectory=tools"\n'
    )


def main() -> None:
    _survive_a_narrow_console()
    # AFTER the console fix, never before: this prints, and the crash
    # that fix exists for was in the very first status line.
    _warn_on_version_skew()
    # DEBUG WITHOUT A DETOUR.
    #
    # Asking a tester for a debug log meant telling them to set an
    # environment variable this tool does not read, or to wrap the call
    # in a Python one-liner. Both were guessed rather than checked, and
    # the second time that happened in one day.
    #
    # A check whose failures need a log should be able to produce one.
    if "--debug" in sys.argv:
        sys.argv.remove("--debug")
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
        logging.getLogger("paho").setLevel(logging.DEBUG)
    parser = argparse.ArgumentParser(
        description="Try a write operation that has no verifier yet."
    )
    add_account_arguments(parser)
    parser.add_argument("check", nargs="?", help="which operation to run")
    parser.add_argument("--list", action="store_true", help="show what can be run")
    parser.add_argument("--new-name", default="Roomba+ test")
    parser.add_argument("--p2map-id", default=None)
    # NO DEFAULT: omitting it means "resend what the map already has",
    # which is what the abort notice promises. A default of 0.0 made the
    # check quietly straighten every map it was run against.
    parser.add_argument("--orientation", type=float, default=None,
                        help="in RADIANS (0 = unchanged for most maps)")
    parser.add_argument("--schedule-name", default="Roomba+ test schedule")
    parser.add_argument("--household-id", default=None)
    # OVERRIDABLE so a second value can be tried without a code change.
    # If `homeassistant` is rejected, the next question is whether the
    # server rejects ANY unregistered value or only that one -- and the
    # cheapest way to ask is to run the same check with `openHAB`, which
    # the vendor's own enum does list.
    parser.add_argument(
        "--initiator", default="homeassistant",
        help="the initiator string to claim (custom_initiator check)",
    )
    parser.add_argument("--i-understand-this-writes-to-my-robot", action="store_true")
    args = parser.parse_args()

    if args.list or not args.check:
        _print_list()
        return

    check = next((c for c in CHECKS if c.name == args.check), None)
    if check is None:
        print(f"Unknown check {args.check!r}. Run with --list to see the options.")
        sys.exit(1)

    # A read-only check needs no write consent. Demanding it would
    # teach the habit of passing the flag without reading why -- which
    # is precisely what makes the flag worthless on the checks that do
    # need it.
    if check.risk != "read" and not args.i_understand_this_writes_to_my_robot:
        print(
            f"Aborted: {check.name} writes to your real robot "
            f"({_RISK_NOTE[check.risk]}).\n"
            "Re-run with --i-understand-this-writes-to-my-robot once you have "
            "read what it does."
        )
        sys.exit(1)

    require_blid(args)
    username, password = resolve_credentials(args)

    async def _run() -> None:
        # connect_mqtt=True because some checks read or write the named
        # shadows, which only exist over MQTT. A guard test enforces this
        # at every connection site: fixing one and missing another fails
        # only against a real robot, which is the expensive place.
        async with connected_robot(
            username, password, args.country_code, args.blid, connect_mqtt=True
        ) as (robot, report):
            print(f"\n== {check.name} ==")
            print(f"   {check.summary}")
            try:
                result = await check.runner(robot, args)
            except Exception as exc:  # noqa: BLE001
                # "FAILED", not "FAIL" -- Report.add looks the status up
                # in a dict and raises KeyError on anything else.
                #
                # The effect was worse than a typo: a check that failed
                # cleanly with an HTTP error turned into a KeyError
                # traceback, and the final summary crashed on the way out
                # too. Two of @DaRealGuGu's runs ended that way, so the
                # actual finding -- HTTP 400 and HTTP 500 from two
                # endpoints -- arrived buried under our own crash.
                report.add(check.name, "FAILED", _failure_detail(exc))
                return
            if result is None:
                report.add(check.name, "SKIPPED", "nothing was sent")
                return
            # A check that ran but answered nothing is not a pass. See
            # NoResult's own docstring for the two field rounds this
            # distinction cost.
            if isinstance(result, NoResult):
                report.add(check.name, "SKIPPED", result.detail)
                print(
                    "\nThis check did not answer its own question. That is a "
                    "result worth having,\nbut only together with the raw output "
                    f"above -- please {check.verify_by}."
                )
                return
            report.add(check.name, "OK", f"response: {result}")
            # ACCEPTED IS NOT THE SAME AS CORRECT. The virtual-wall
            # investigation turned on exactly this: a server can accept a
            # request and store something other than what was meant.
            print(f"\nAccepted. Now please {check.verify_by}.")

    run_script(_run())


if __name__ == "__main__":
    main()
