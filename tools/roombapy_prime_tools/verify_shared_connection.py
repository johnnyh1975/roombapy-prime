"""Can ONE MQTT connection serve every Prime robot on an account?

WHY THIS EXISTS (@mattdrmc, issue #91). Two Prime robots on one
account, one login between them: both connected with the same
server-issued client id, and AWS IoT drops the older connection when a
second one arrives with the same id. Each robot knocked the other off,
about every two seconds, for days -- roughly 3,500 drops an hour.

One fix is a login per robot, which buys each robot a client id of its
own at the price of a login per robot. The other is one connection per
account that carries every robot's topics. Whether the second can work
is decided by the iRobot cloud, not by this library:

  1. Does the login's connection token name every robot? The login
     asks for up to five devices per token (`devices_per_token`), and
     the token's claims list the robots it may reach. If one token names
     them all, one connection is at least allowed to reach them all.
  2. Does the broker actually serve them all on that one connection --
     subscriptions accepted for each robot, answers from each, no drops,
     and still after the token refresh, which comes about every hour?

This script checks both. It logs in ONCE, opens ONE connection, and
for every Prime robot on the account subscribes to its shadow updates
and asks for its shadow every few minutes. It runs across the token
refresh (75 minutes by default) and counts per robot what came back,
and every drop the broker or the network caused.

WHAT IT NEVER DOES. It reads shadows only. With --find, and only after a
yes at the prompt, it asks each robot to play its sound twice: at the
start and after the token refresh. Nothing else is sent.

WHAT IT NEVER PRINTS. No password, no token, no client id. Robot ids are
shortened to their last four characters; client ids appear only as a
fingerprint, which says whether two are the same and nothing else.

Home Assistant can keep running meanwhile: its connections log in on
their own and carry other client ids. Close the iRobot phone app for the
run, so its connection does not muddy the count.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field as dc_field

import aiohttp

from roombapy_prime.account import CloudAccount
from roombapy_prime.auth import ConnectionToken, LoginResult, is_prime_sku
from roombapy_prime.diagnostics import Report
from roombapy_prime.mqtt_client import PrimeMqttClient, ShadowError, ShadowResponse

# Absolute, not relative: the file also runs on its own, downloaded next
# to an installed roombapy-prime-tools, before a release ships it.
from roombapy_prime_tools._cli import (
    add_account_arguments,
    confirm,
    field,
    resolve_credentials,
    run_script,
)

# FULL STATUS NAMES ONLY: Report.summary() counts "FAILED" and "SKIPPED",
# not the short forms its markers accept -- a "FAIL" would print as
# "0 failed" under a failed check.

#: Long enough to cross the token refresh, which falls about 55 minutes
#: after a login (a one-hour token, refreshed five minutes early).
DEFAULT_MINUTES = 75
#: How often each robot is asked for its shadow.
DEFAULT_ASK_EVERY_MINUTES = 5
#: Minutes the run keeps going after the refresh, so the robots have to
#: answer on the refreshed connection as well.
AFTER_REFRESH_MINUTES = 10
PROGRESS_EVERY_SECONDS = 300
#: After the last round of requests, before the verdict.
LAST_ANSWER_WAIT_SECONDS = 5.0
FIND_COMMAND = "find"


# --- What is printed --------------------------------------------------


def short_id(blid: str) -> str:
    """A robot id cut to its last four characters: enough to tell two
    robots apart in a pasted report, too little to identify one."""
    return f"...{blid[-4:]}" if len(blid) > 4 else blid


def fingerprint(value: str) -> str:
    """Eight hex characters of a SHA-256. Says whether two client ids are
    the same -- the whole question here -- without printing either."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def mask(text: str, blids: Iterable[str]) -> str:
    """Shortens every full robot id in `text`. Error messages from the
    library name topics, and topics carry the full id."""
    for blid in blids:
        if blid:
            text = text.replace(blid, short_id(blid))
    return text


class _MaskRobotIds(logging.Filter):
    """Shortens robot ids in the library's own warnings too: they name
    topics, and a pasted run should not carry a full id anywhere."""

    def __init__(self, blids: Iterable[str]) -> None:
        super().__init__()
        self._blids = list(blids)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask(record.getMessage(), self._blids)
        record.args = None
        return True


def mask_library_logs(blids: Iterable[str]) -> logging.Handler:
    """Routes roombapy-prime's warnings through the id mask."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_MaskRobotIds(blids))
    logger = logging.getLogger("roombapy_prime")
    logger.addHandler(handler)
    logger.propagate = False
    return handler


def clock(seconds: float) -> str:
    return f"{int(seconds // 60):3d}:{int(seconds % 60):02d}"


# --- Check 1: what the token covers -------------------------------------


def token_claims_devices(token: ConnectionToken) -> list[str] | None:
    """The robots the token's own claims name, or None if the claims
    cannot be read.

    The iot_token is base64-encoded JSON whose "devices" maps each robot
    id it may reach. The authorizer decides from those claims, so they
    are the authority; the response's own `devices` list is shown beside
    them. Reads nothing else from the claims."""
    raw = token.iot_token or ""
    candidates = [raw]
    if raw.count(".") == 2:  # a JWT-shaped token: the claims are the middle part
        candidates.append(raw.split(".")[1])
    for candidate in candidates:
        padded = candidate + "=" * (-len(candidate) % 4)
        try:
            decoded = base64.b64decode(padded.replace("-", "+").replace("_", "/"))
            claims = json.loads(decoded)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(claims, dict):
            continue
        devices = claims.get("devices")
        if isinstance(devices, dict):
            return [str(blid) for blid in devices]
        if isinstance(devices, list):
            return [str(blid) for blid in devices]
        return []
    return None


def token_reach(token: ConnectionToken) -> set[str]:
    """The robots a token may reach: its claims if readable, else the
    response's list."""
    claims = token_claims_devices(token)
    return set(claims) if claims is not None else set(token.devices)


def token_covering(login_result: LoginResult, blids: Iterable[str]) -> ConnectionToken | None:
    """The first token that reaches every robot in `blids`, or None."""
    wanted = set(blids)
    for token in login_result.connection_tokens:
        if wanted <= token_reach(token):
            return token
    return None


@dataclass(frozen=True)
class Target:
    blid: str
    name: str


def choose_targets(login_result: LoginResult, wanted: list[str] | None) -> list[Target]:
    """The robots to test: those named with --robots, else every Prime
    robot on the account. A named id the account does not have is an
    error rather than something to skip quietly."""
    robots = login_result.robots or {}
    if wanted:
        unknown = [blid for blid in wanted if blid not in robots]
        if unknown:
            raise SystemExit(
                "Aborted: not on this account: " + ", ".join(short_id(b) for b in unknown)
            )
        chosen = wanted
    else:
        chosen = [
            blid for blid, entry in robots.items() if is_prime_sku(field(entry, "sku", None) or "")
        ]
    return [
        Target(blid, field(robots.get(blid), "name", None) or "(unnamed)") for blid in chosen
    ]


def report_tokens(
    login_result: LoginResult, targets: list[Target], report: Report
) -> ConnectionToken | None:
    """Prints what every token covers and returns the one covering all
    targets, if any."""
    names = {target.blid: target.name for target in targets}
    every = [*names, *(login_result.robots or {})]
    tokens = login_result.connection_tokens
    print(f"\n== The login returned {len(tokens)} connection token(s) ==")
    for index, token in enumerate(tokens, start=1):
        claims = token_claims_devices(token)
        remaining = token.seconds_until_expiry()
        expiry = f"expires in {remaining / 60:.0f} min" if remaining is not None else "no expiry"
        print(f"  token {index}: client id #{fingerprint(token.client_id)}, {expiry}")
        listed = ", ".join(short_id(b) for b in token.devices) or "(none)"
        print(f"    robots in the response's list: {listed}")
        if claims is None:
            print("    robots in the token's claims:  (claims not readable)")
        else:
            print(f"    robots in the token's claims:  {', '.join(short_id(b) for b in claims) or '(none)'}")
        for target in targets:
            mark = "yes" if target.blid in token_reach(token) else "no"
            print(f"      {target.name!r} {short_id(target.blid)}: {mark}")
    ids = {fingerprint(token.client_id) for token in tokens}
    if len(tokens) > 1:
        print(f"  distinct client ids among them: {len(ids)}")

    covering = token_covering(login_result, names)
    if covering is not None:
        report.add(
            "1. One token covers every Prime robot", "OK",
            f"{len(targets)} robot(s), one client id",
        )
    else:
        missing = [
            short_id(t.blid) for t in targets
            if not any(t.blid in token_reach(token) for token in tokens)
        ]
        detail = (
            "no token names " + ", ".join(missing) if missing
            else "the robots are spread over several tokens"
        )
        report.add("1. One token covers every Prime robot", "FAILED", mask(detail, every))
    return covering


# --- Check 2: one connection, every robot -------------------------------


@dataclass
class RobotTally:
    target: Target
    subscribed: bool = False
    subscribe_error: str | None = None
    updates: int = 0
    asked: int = 0
    answered: int = 0
    rejected: int = 0
    unsent: int = 0
    answered_after_refresh: int = 0
    finds: list[bool] = dc_field(default_factory=list)


def shadow_topic(blid: str, suffix: str) -> str:
    return f"$aws/things/{blid}/shadow/{suffix}"


def update_topics(blid: str) -> list[str]:
    """The update topics the library itself watches, named and unnamed."""
    return [shadow_topic(blid, "name/+/update/accepted"), shadow_topic(blid, "update/accepted")]


class SharedConnectionProbe:
    """One connection, several robots, and a tally of what each got.

    The client is a PrimeMqttClient opened for the first robot; the
    other robots' topics ride on the same connection. `relogin` returns a
    fresh LoginResult; `now` is a monotonic clock, injectable for tests.
    """

    def __init__(
        self,
        client: PrimeMqttClient,
        targets: list[Target],
        relogin: Callable[[], Awaitable[LoginResult]],
        irbt_topic_prefix: str | None,
        *,
        all_blids: Iterable[str] = (),
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.tallies = [RobotTally(target) for target in targets]
        self._relogin = relogin
        self._prefix = irbt_topic_prefix
        self._blids = {*(t.blid for t in targets), *all_blids}
        self._now = now
        self.started = now()
        self.drops: list[tuple[float, str]] = []
        self.reconnect_failures = 0
        self.refreshes: list[tuple[float, bool]] = []  # (when, same client id)
        self.refresh_failures: list[str] = []
        self.refreshed_at: float | None = None
        self.task_errors: list[str] = []

    def elapsed(self) -> float:
        return self._now() - self.started

    def say(self, text: str) -> None:
        print(f"[{clock(self.elapsed())}] {mask(text, self._blids)}")

    # Subscriptions -----------------------------------------------------

    def _on_update(self, tally: RobotTally) -> Callable[[ShadowResponse], None]:
        def count(_response: ShadowResponse) -> None:
            tally.updates += 1
        return count

    def _on_answer(self, tally: RobotTally) -> Callable[[ShadowResponse], None]:
        def count(response: ShadowResponse) -> None:
            tally.answered += 1
            if response.topic.endswith("/rejected"):
                tally.rejected += 1
            if self.refreshed_at is not None:
                tally.answered_after_refresh += 1
        return count

    async def subscribe_all(self) -> None:
        for tally in self.tallies:
            blid = tally.target.blid
            topics = [(topic, self._on_update(tally)) for topic in update_topics(blid)]
            answer = self._on_answer(tally)
            topics += [(shadow_topic(blid, "get/accepted"), answer),
                       (shadow_topic(blid, "get/rejected"), answer)]
            try:
                for topic, callback in topics:
                    await self.client.subscribe(topic, callback)
            except Exception as exc:  # noqa: BLE001 -- the finding itself
                tally.subscribe_error = mask(f"{type(exc).__name__}: {exc}", self._blids)
                self.say(f"{tally.target.name!r}: subscription refused -- {tally.subscribe_error}")
            else:
                tally.subscribed = True
                self.say(f"{tally.target.name!r} {short_id(blid)}: subscribed")

    # Requests ----------------------------------------------------------

    async def ask_every_robot(self) -> None:
        """One shadow get per robot. An answer, accepted or rejected,
        proves the shared connection reaches that robot."""
        for tally in self.tallies:
            if not tally.subscribed:
                continue
            tally.asked += 1
            try:
                # _publish, not get_shadow(): get_shadow() asks for the
                # robot the client was opened for, and the point here is
                # every other robot on the same connection.
                await self.client._publish(shadow_topic(tally.target.blid, "get"), b"")
            except ShadowError as exc:
                tally.unsent += 1
                self.say(f"{tally.target.name!r}: shadow request not sent -- {exc}")

    async def find_every_robot(self) -> None:
        if self._prefix is None:
            self.say("find skipped: the login named no command topic prefix")
            return
        for tally in self.tallies:
            if not tally.subscribed:
                continue
            payload = json.dumps(
                {"command": FIND_COMMAND, "time": int(time.time()), "initiator": "localApp"}
            )
            topic = f"{self._prefix}/things/{tally.target.blid}/cmd"
            try:
                await self.client._publish(topic, payload)
            except ShadowError:
                tally.finds.append(False)
                self.say(f"{tally.target.name!r}: find NOT acknowledged by the broker")
            else:
                tally.finds.append(True)
                self.say(f"{tally.target.name!r}: find sent -- did it play its sound?")

    # Background work ---------------------------------------------------

    async def watch_connection(self) -> None:
        """Counts every drop the broker or the network caused, and
        reconnects after it. Our own reconnects for the token refresh are
        not drops."""
        while True:
            generation = self.client.generation
            await self.client.wait_for_disconnect(generation)
            end = self.client.ended(generation)
            reason, deliberate = end if end is not None else (
                self.client.disconnect_reason or "unknown",
                self.client.last_disconnect_was_deliberate,
            )
            if self.client.closed:
                return  # our own disconnect at the end of the run
            if deliberate:
                # A token refresh is reconnecting; let it finish.
                await self.client.wait_until_settled()
                if self.client.closed:
                    return
                if self.client.connected:
                    continue
            else:
                self.drops.append((self.elapsed(), mask(reason, self._blids)))
                self.say(f"connection DROPPED: {reason}")
            await self._reconnect()

    async def _reconnect(self) -> None:
        delay = 2.0
        while not self.client.connected and not self.client.closed:
            try:
                await self.client.reconnect(timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                self.reconnect_failures += 1
                self.say(f"reconnect failed ({type(exc).__name__}: {exc}); retrying in {delay:.0f}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            else:
                self.say("reconnected")

    async def refresh_token_when_due(self, send_find: bool) -> None:
        """Logs in again shortly before the token expires and swaps the
        token on the open connection, as the library does -- then asks
        every robot again, which is the point of the exercise."""
        while True:
            due = self.client.seconds_until_token_refresh_due()
            if due is None:
                self.say("the token carries no expiry -- the refresh cannot be tested")
                return
            await asyncio.sleep(due)
            old = self.client._token
            try:
                login_result = await self._relogin()
                blids = [t.target.blid for t in self.tallies]
                token = token_covering(login_result, blids) or login_result.token_for_blid(blids[0])
                await self.client.replace_token(token)
            except Exception as exc:  # noqa: BLE001
                self.refresh_failures.append(mask(f"{type(exc).__name__}: {exc}", self._blids))
                self.say(f"token refresh FAILED ({type(exc).__name__}: {exc}); retrying in 60s")
                await asyncio.sleep(60)
                continue
            same = fingerprint(token.client_id) == fingerprint(old.client_id)
            self.refreshes.append((self.elapsed(), same))
            self.refreshed_at = self.elapsed()
            self.say(
                "token refreshed, connection reopened"
                + (" (same client id)" if same else " (new client id)")
            )
            await self.ask_every_robot()
            if send_find:
                await self.find_every_robot()

    async def keep_asking(self, every_seconds: float) -> None:
        while True:
            await asyncio.sleep(every_seconds)
            await self.ask_every_robot()

    async def print_progress(self, every_seconds: float = PROGRESS_EVERY_SECONDS) -> None:
        while True:
            await asyncio.sleep(every_seconds)
            parts = [
                f"{t.target.name!r}: {t.updates} updates, {t.answered}/{t.asked} answers"
                for t in self.tallies
            ]
            state = "up" if self.client.connected else "DOWN"
            self.say(f"connection {state}, {len(self.drops)} drop(s) | " + " | ".join(parts))

    async def guarded(self, name: str, work: Awaitable[None]) -> None:
        """A background task that fails is recorded, not lost: the run
        goes on, and the report says what stopped."""
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.task_errors.append(mask(f"{name}: {type(exc).__name__}: {exc}", self._blids))
            self.say(f"{name} stopped: {type(exc).__name__}: {exc}")

    # Verdicts ------------------------------------------------------------

    def verdicts(self, minutes: float, send_find: bool) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        refused = [t for t in self.tallies if not t.subscribed]
        out.append((
            "2a. Subscriptions accepted for every robot",
            "FAILED" if refused else "OK",
            "; ".join(f"{t.target.name!r}: {t.subscribe_error}" for t in refused)
            or f"{len(self.tallies)} robot(s) on one connection",
        ))
        silent = [t for t in self.tallies if t.subscribed and t.answered == 0]
        out.append((
            "2b. Every robot answered on the shared connection",
            "FAILED" if silent or refused else "OK",
            ", ".join(
                f"{t.target.name!r} {t.answered}/{t.asked} answers"
                + (f" ({t.rejected} rejected)" if t.rejected else "")
                for t in self.tallies
            ),
        ))
        if self.drops:
            shown = "; ".join(f"at {clock(when).strip()} {why}" for when, why in self.drops[:3])
            more = f" (+{len(self.drops) - 3} more)" if len(self.drops) > 3 else ""
            out.append(("2c. No connection drops", "FAILED",
                        f"{len(self.drops)} in {minutes:.0f} min: {shown}{more}"))
        else:
            out.append(("2c. No connection drops", "OK", f"0 in {minutes:.0f} min"))
        if self.refreshes:
            behind = [t for t in self.tallies if t.subscribed and t.answered_after_refresh == 0]
            ids = "same client id" if all(same for _, same in self.refreshes) else "new client id"
            out.append((
                "2d. Every robot answered after the token refresh",
                "FAILED" if behind else "OK",
                ", ".join(f"{t.target.name!r} did not" for t in behind)
                or f"{len(self.refreshes)} refresh(es), {ids}",
            ))
        elif self.refresh_failures:
            out.append(("2d. Every robot answered after the token refresh", "FAILED",
                        self.refresh_failures[-1]))
        else:
            out.append(("2d. Every robot answered after the token refresh", "SKIPPED",
                        "the run ended before the refresh"))
        if send_find:
            unacked = [t for t in self.tallies if t.subscribed and not all(t.finds)]
            out.append((
                "2e. Find acknowledged by the broker for every robot",
                "FAILED" if unacked else "OK",
                ", ".join(f"{t.target.name!r}: {t.finds}" for t in unacked)
                or "the broker acknowledged each; whether each robot beeped, only you can say",
            ))
        for t in self.tallies:
            out.append((
                f"   {t.target.name!r} {short_id(t.target.blid)}",
                "OK" if t.subscribed else "FAILED",
                f"{t.updates} shadow updates, {t.answered}/{t.asked} answers",
            ))
        for error in self.task_errors:
            out.append(("   A background task stopped", "FAILED", error))
        return out


# --- The run --------------------------------------------------------------


async def probe_shared_connection(
    account: CloudAccount,
    targets: list[Target],
    token: ConnectionToken,
    minutes: float,
    ask_every_minutes: float,
    send_find: bool,
    report: Report,
) -> None:
    login_result = account.login_result
    every = [*(t.blid for t in targets), *(login_result.robots or {})]
    client = PrimeMqttClient(token, login_result.mqtt_endpoint, targets[0].blid)
    probe = SharedConnectionProbe(
        client, targets, account.relogin, login_result.irbt_topic_prefix, all_blids=every,
    )

    due = client.seconds_until_token_refresh_due()
    if due is not None and minutes * 60 < due + AFTER_REFRESH_MINUTES * 60:
        minutes = due / 60 + AFTER_REFRESH_MINUTES
        print(f"\nThe token refresh is due in {due / 60:.0f} min; the run is extended to "
              f"{minutes:.0f} min to cross it.")
    print(f"\n== One connection for {len(targets)} robots, {minutes:.0f} minutes ==")
    print("Leave this running. A line appears every five minutes and on every drop.")

    await client.connect()
    probe.say("connected")
    tasks: list[asyncio.Task[None]] = []
    try:
        await probe.subscribe_all()
        await probe.ask_every_robot()
        if send_find:
            await probe.find_every_robot()
        tasks = [
            asyncio.create_task(probe.guarded("drop watch", probe.watch_connection())),
            asyncio.create_task(probe.guarded("token refresh",
                                              probe.refresh_token_when_due(send_find))),
            asyncio.create_task(probe.guarded("shadow requests",
                                              probe.keep_asking(ask_every_minutes * 60))),
            asyncio.create_task(probe.guarded("progress", probe.print_progress())),
        ]
        remaining = minutes * 60 - probe.elapsed()
        if remaining > 0:
            await asyncio.sleep(remaining)
        # A last round, so a robot asked late still gets a chance to answer.
        await probe.ask_every_robot()
        await asyncio.sleep(LAST_ANSWER_WAIT_SECONDS)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.disconnect()
        for name, status, detail in probe.verdicts(minutes, send_find):
            report.add(name, status, detail)


async def run(
    username: str,
    password: str,
    country_code: str,
    wanted: list[str] | None,
    minutes: float,
    ask_every_minutes: float,
    send_find: bool,
    token_only: bool,
) -> Report:
    report = Report()
    async with aiohttp.ClientSession() as session:
        try:
            account = await CloudAccount.login(session, username, password, country_code)
            login_result = account.login_result
            mask_library_logs(login_result.robots or {})
            report.add("Login", "OK", "one login for the whole run")
            targets = choose_targets(login_result, wanted)
            robots = login_result.robots or {}
            print(f"\n== {len(robots)} robot(s) on this account ==")
            for blid, entry in robots.items():
                sku = field(entry, "sku", None) or "?"
                mark = "->" if any(t.blid == blid for t in targets) else "  "
                kind = "Prime" if is_prime_sku(sku) else "not Prime (left out)"
                print(f"  {mark} {field(entry, 'name', None) or '(unnamed)'!r}  sku={sku}  "
                      f"{short_id(blid)}  {kind}")
            token = report_tokens(login_result, targets, report)
            if token_only:
                return report
            if len(targets) < 2:
                report.add("2. One connection for several robots", "SKIPPED",
                           f"{len(targets)} Prime robot(s) on this account; needs two")
                return report
            await probe_shared_connection(
                account, targets, token or login_result.token_for_blid(targets[0].blid),
                minutes, ask_every_minutes, send_find, report,
            )
        finally:
            report.redact(username, password)
            report.print_final_summary()
            print_conclusion(report)
    return report


def print_conclusion(report: Report) -> None:
    """One plain sentence on top of the table: the answer #91 waits for."""
    results = {r.name: r.status for r in report.results}
    one = [name for name in results if name.startswith(("2", "   A background"))]
    if not one:
        return
    failed = [name for name in one if results[name] == "FAILED"]
    print("\n== Conclusion ==")
    if failed:
        print("One connection did NOT serve every robot cleanly. Failed: " + "; ".join(failed))
    elif any(results[name] == "SKIPPED" for name in one):
        print("No failure so far, but not every check ran -- see the skipped line above.")
    else:
        print("One connection served every robot, across the token refresh, without a drop.")
    print("Please paste the WHOLE output into the issue.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Checks whether one MQTT connection can serve every Prime robot on an account "
            "(issue #91). Logs in once, reads shadows only; --find makes each robot play its "
            "sound. Prints no password, token or client id."
        )
    )
    add_account_arguments(parser)
    # Every robot is tested here, so the shared --blid means nothing: kept
    # for a ROOMBAPY_PRIME_BLID already set, hidden from --help.
    for action in parser._actions:
        if action.dest == "blid":
            action.help = argparse.SUPPRESS
    parser.add_argument(
        "--robots", default=None, metavar="BLID,BLID",
        help="Comma-separated robot ids to test (default: every Prime robot on the account).",
    )
    parser.add_argument(
        "--minutes", type=float, default=DEFAULT_MINUTES,
        help=f"How long to run (default {DEFAULT_MINUTES}); extended by itself to cross "
        "the token refresh.",
    )
    parser.add_argument(
        "--ask-every", type=float, default=DEFAULT_ASK_EVERY_MINUTES, metavar="MINUTES",
        help=f"Ask each robot for its shadow this often (default {DEFAULT_ASK_EVERY_MINUTES}).",
    )
    parser.add_argument(
        "--find", action="store_true",
        help="Also make each robot play its sound, at the start and after the token refresh.",
    )
    parser.add_argument(
        "--token-only", action="store_true",
        help="Only show what the login's tokens cover (check 1); takes seconds.",
    )
    args = parser.parse_args(argv)
    if args.minutes <= 0 or args.ask_every <= 0:
        print("Aborted: --minutes and --ask-every must be positive.")
        sys.exit(1)
    wanted = [b.strip() for b in args.robots.split(",") if b.strip()] if args.robots else None
    if args.find and not args.token_only and not confirm(
        "--find makes every tested robot play its sound, twice. Continue?"
    ):
        print("Aborted: nothing was sent, and no login was made.")
        sys.exit(1)

    username, password = resolve_credentials(args)
    sys.exit(run_script(run(
        username, password, args.country_code, wanted,
        args.minutes, args.ask_every, args.find and not args.token_only, args.token_only,
    )))


if __name__ == "__main__":
    main()
