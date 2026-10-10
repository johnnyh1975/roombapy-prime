"""Public robot class (analogous to roombapy.roomba.Roomba).

STATUS: In field use. Connects auth.LoginResult,
mqtt_client.PrimeMqttClient and rest_client.PrimeRestClient.

It read "NOT tested against a real V4 account" for months after that
stopped being true. A dozen testers run this class daily; region
cleaning, splitting and merging rooms, schedule writes and map editing
are all confirmed on hardware, several of them with the robot's own
answer recorded. A "Draft" banner on the most-read docstring in the
package told every reader the opposite of the truth.

WHAT REMAINS UNCONFIRMED IS NAMED PER METHOD, not here. The pattern is
deliberate: a blanket disclaimer at the top invites either ignoring the
whole file or distrusting all of it, and neither helps someone deciding
whether one specific call is safe. `send_routine_command_via_cmd_topic`
carries the sharpest of them -- `clean_all` is still untested, and a
wrong guess there cleans the whole house.

Also part of this draft (see watch_state()/watch_live_map() below):
continuous dispatch loops for shadow deltas and live-map/-position
messages -- previously deliberately left out (see
docs/internal/ROOMBAPY_COMPARISON.md section 3). One asyncio.Queue PER
watch_*() call, filled from mqtt_client.py's subscribe() callbacks via
loop.call_soon_threadsafe(). Since 0.5.0 those callbacks run on the
event loop itself (aiomqtt, no paho thread); call_soon_threadsafe
still works there and keeps each callback short. No lock needed --
each watcher gets its own queue, and subscribe()/unsubscribe() are
reference-counted for the case where two watchers observe the same
topic (see its docstring).

Also: proactive token refresh (see _refresh_loop() below).
PrimeFactory wires up a relogin callback for this by default --
without it (relogin=None) this class behaves as before: tokens expire
after ~1h, running watch_*() generators then simply stop delivering
messages, no error.

IMPORTANT TRADEOFF, not hidden: automatic refresh means credentials
(via the relogin callback) must stay in memory for the entire lifetime
of the PrimeRobot instance, not just for the one-time login moment as
before. Anyone who doesn't want this can omit relogin and accept the
~1h expiry limit.

Connection drops (since 0.5.0b1): every watcher remembers the connection
generation it watches, so a drop it was not waiting for is still seen.
One watcher at a time rebuilds the connection, under this robot's lock;
the others adopt the new connection. disconnect() closes the client for
good: watchers then wait, and nothing reconnects until connect(). See
_resume_after_end().

Notes:
  - Each watcher's queue is bounded (queue_maxsize, default 100); a
    consumer that falls behind loses the oldest messages, each loss
    logged -- see _put_with_backpressure().
  - replace_token() and get_shadow()/update_shadow() share one lock in
    mqtt_client.py, so a token swap never runs in the middle of a
    shadow read or write. A watcher's plain reconnect does not take
    that lock; a read it interrupts times out.
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import UTC, datetime
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar

from .auth import LoginResult
from .mqtt_client import PrimeMqttClient, ShadowResponse
from .rest_client import PrimeRestClient
from .models import (
    DNDStatusResponse,
    FavoriteV1,
    FirmwareItem,
    HouseholdSchedule,
    LiveMapStreamInit,
    MapEditCommand,
    MapEditCommandV1,
    MapEditResult,
    MapUpdateMessage,
    P2MapData,
    PositionUpdateMessage,
    RobotPartsInfo,
    RobotSerialInfo,
    RoutineCommand,
    RoutinesDefaultsResponse,
    ScheduleOptions,
    SchedulesResponse,
    parse_livemap_message_data,
)

_LOGGER = logging.getLogger(__name__)


#: Ten seconds, matching LiveMapKeepAliveConfig's own default. A margin
#: before the stream lapses, not an interval.
_LIVEMAP_REFRESH_WINDOW_S: float = 10.0


class _StreamExpiry:
    """Holds the live-map stream's expiry, as the robot reports it.

    Every position message carries `update_expire_ts`. The app schedules
    its next keep-alive at `expiration - now - refreshWindowMillis`, so
    the ping lands shortly before the stream would lapse rather than at
    a fixed cadence.

    WRITTEN FROM THE MQTT CALLBACK, READ FROM THE KEEP-ALIVE TASK. Both
    run on the same event loop -- the callback hands over via
    call_soon_threadsafe -- so a plain attribute is enough and a lock
    would only add a way to deadlock a background task.
    """

    def __init__(self) -> None:
        self._expires_at: datetime | None = None

    def set_result_if_pending(self, expires_at: datetime) -> None:
        self._expires_at = expires_at

    def next_delay(self, fallback: float) -> float:
        """Seconds until the next ping should go out.

        Falls back whenever the robot has not told us anything, and that
        covers more than the first message: a robot that never sends the
        field keeps the old fixed cadence rather than losing its stream.

        Clamped at both ends. Zero would spin, and an expiry far in the
        future would leave the stream unattended for hours if the robot
        ever reported a bad one -- an hour is generous for something the
        app treats in seconds.
        """
        if self._expires_at is None:
            return fallback
        remaining = (
            self._expires_at - datetime.now(tz=UTC)
        ).total_seconds() - _LIVEMAP_REFRESH_WINDOW_S
        return min(max(remaining, 1.0), 3600.0)


async def _first_of(*tasks: asyncio.Future[Any]) -> set[asyncio.Future[Any]]:
    """Waits for the first task to finish; cancels the rest and waits for
    them to end.

    A CANCEL OF OUR OWN IS NEVER SWALLOWED. The losers used to be
    awaited under `suppress(BaseException)` -- a cancel landing on this
    task during that await was taken for the loser's, and the watcher
    kept running (review finding; 0.4.x too). asyncio.wait() does not
    raise the tasks' outcomes, only our own cancellation."""
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)
    return done

Relogin = Callable[[], Awaitable[LoginResult]]

DEFAULT_WATCH_QUEUE_MAXSIZE = 100
DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS = 60.0
# Chosen arbitrarily (not an empirical value) -- large enough to
# absorb brief processing delays on the caller's side, small enough to
# not tie up unbounded memory if the consumer permanently falls behind.


_QueueItemT = TypeVar("_QueueItemT")


def _put_with_backpressure(
    queue: asyncio.Queue[_QueueItemT], item: _QueueItemT, topic: str
) -> None:
    """Runs on the event loop thread (called via
    loop.call_soon_threadsafe from watch_state()/watch_live_map()). If
    the queue is full, the OLDEST entry is dropped to make room for the
    new one -- freshness over completeness, appropriate for status/
    position streams, where a stale value is less useful than a
    current one. Every drop is logged, so a lagging consumer doesn't
    lose messages unnoticed.

    NEW: if the entry being dropped happens to be an exception
    (watch_live_map() puts errors into the same queue, see its
    docstring), this is logged as ERROR instead of WARNING -- a lost
    error is more serious than a lost routine message. This does NOT
    prevent the loss (that would need a priority queue instead of a
    simple FIFO), but makes it more visible instead of disappearing
    among ordinary drops."""
    if queue.full():
        try:
            dropped = queue.get_nowait()
            if isinstance(dropped, Exception):
                _LOGGER.error(
                    "watch_*() queue for topic %s full -- an ERROR was dropped "
                    "while discarding the oldest entry (not just a routine "
                    "message): %r. The caller missed this error signal.",
                    topic,
                    dropped,
                )
            else:
                _LOGGER.warning(
                    "watch_*() queue for topic %s full -- oldest entry "
                    "dropped to make room (consumer is falling behind)",
                    topic,
                )
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(item)


class PrimeRobot:
    """A robot, identified by blid. Doesn't hold its own login session
    -- that comes already wired up from prime_factory.py.

    relogin: optional async callback with no arguments that provides a
    new LoginResult (see prime_factory.py). Only needed for proactive
    token refresh -- without it, everything works as before, just
    without automatic refresh (see module docstring, tradeoff).

    irbt_topic_prefix: NEW, UNCERTAIN (see auth.py's LoginResult
    docstring and mqtt_client.py's livemap_topic()). Needed for
    watch_live_map()/send_simple_command() -- without it, both
    immediately raise a clear error, instead of silently waiting on/
    publishing to the wrong topic.

    deployment: NEW (session 41). The raw discovery-response deployment
    object, kept around so diagnostics.py can report its actual keys
    when irbt_topic_prefix/iot_topic_prefix guessing turns out wrong (as
    a live test first showed) -- not used by PrimeRobot itself for
    anything beyond exposing it for diagnostics.

    COMMAND SURFACE, cross-checked against the firmware 3.8.126 image's
    own handler router in `connectivity_broker`. The firmware names six
    write handlers; four have methods here, two do not, and the two
    gaps are deliberate:

        rw-settings   process_update_robot_settings_event  -> set_setting etc.
        rw-schedule   handle_clean_schedules               -> create/update/delete_schedule
        map/zone edit handle_map_edit_request              -> edit_map, edit_map_v2, set_map_name
        dock status   process_dock_status                  -> DockStatus model, watch_dock_reports

        p2map upload  handle_smart_map_upload              -> NOT BUILT
        services      process_update_robot_services_event  -> NOT BUILT (svcConf is read, not written)

    The two NOT-BUILT ones are not built because no request payload has
    been captured for either. The upload we DO model is the robot's own
    outbound side (`uploadP2MapLive`/`uploadP2MapMission`, see
    map_bundle.py); sending a map TO a robot is a different, unseen
    payload, and restoring a map onto a robot is rare and destructive
    enough that guessing its shape is the wrong trade. `svcConf` is
    read from the shadow; writing services has no observed request.
    Both go in when a real capture shows the shape -- the firmware
    confirms the handlers exist, which is the useful half.

    HYPOTHESIS WORTH TESTING BEFORE ASSUMING THE UPLOAD IS UNKNOWABLE:
    on CLASSIC firmware you do not send a map to a robot at all. You
    tell it which one to fetch. ruby-0.7.12 (j9) carries a schema for
    the command channel:

        cloudFileXfer.downloadPMap = {pmap_id, pmapv_id, format}

    with `cloudFileXferQuery{xferId}` and
    `cloudFileXferCancel{xferId, xferType}` for progress, and a
    `cloudFileXferResult` carrying `status`, `details` and a `cloudData`
    block with `uploadId`, `url` and `headers` -- a presigned URL the
    robot uses itself.

    If Prime works the same way, "the unseen payload" is not a map at
    all: it is three fields naming a version already in the cloud, and
    both halves are partly here already -- `get_map_geojson_link()` and
    `download_map_bundle()` are the same URL mechanism in the other
    direction.

    NOT EVIDENCE FOR PRIME. That firmware is Classic and its local
    channel is roombapy's territory, not this library's. What makes it
    worth writing down is that it is the same cloud behind both
    generations (`disc-prod.iot.irobotapi.com`), so the architecture is
    a reasonable thing to look for rather than a shape to guess at. See
    docs/internal/vendor_schemas_ruby_0_7_12.json."""

    def __init__(
        self,
        blid: str,
        mqtt_client: PrimeMqttClient,
        rest_client: PrimeRestClient,
        relogin: Relogin | None = None,
        robot_id: str | None = None,
        irbt_topic_prefix: str | None = None,
        deployment: dict[str, Any] | None = None,
    ) -> None:
        self.blid = blid
        # The account-level identifier the LOGIN response gives for this
        # BLID. NOT always the same string as the BLID itself -- see
        # get_household_id()'s own docstring for the real account where
        # they differ. Optional because older callers don't pass it;
        # falls back to the BLID, which is correct wherever they match.
        self.robot_id = robot_id or blid
        self._mqtt = mqtt_client
        # Serialises reconnects across concurrent watch tasks.
        #
        # REAL BUG FOUND IN THE FIELD (DaRealGuGu): every watcher had its
        # own reconnect loop, but they all share ONE mqtt client. When
        # two topics are watched at once -- which the region-command
        # session always does, mission/timeline plus rejected/report --
        # a reconnect by task A tears down and rebuilds the shared
        # connection, which task B observes as a drop. B then reconnects,
        # which A observes as a drop. The log shows exactly that: dozens
        # of immediate drops with no failed attempts in between, because
        # every reconnect SUCCEEDED and then got torn down by the other
        # task.
        #
        # It cost two of three test stages their result: the publish
        # went out during a torn-down connection and never got a PUBACK,
        # which the script then reported as a possible policy block.
        self._reconnect_lock: asyncio.Lock | None = None
        #: Timestamps of recent disconnections, for the eviction check.
        #: A blip does not recur every few seconds; an eviction loop does.
        self._recent_drops: list[float] = []
        #: The last connection whose drop was logged -- see _report_drop().
        self._drop_reported_generation = 0
        self._rest = rest_client
        self._relogin = relogin
        self._irbt_topic_prefix = irbt_topic_prefix
        #: Timeline requests are matched to their reports by this id.
        #: The app starts at 1 and increments; reusing one would make
        #: two requests indistinguishable, which is the single thing the
        #: field exists to prevent.
        self._timeline_request_id = 0
        self.deployment = deployment or {}
        self._refresh_task: asyncio.Task[None] | None = None

    _REFRESH_RETRY_SECONDS = 60.0
    """NEW (this session, _refresh_loop() hardening). How long to wait
    before retrying a FAILED proactive token refresh -- deliberately
    short and fixed (not exponential backoff, unlike _watch_topic()'s
    reconnect loop) since this task runs for the whole lifetime of the
    connection and a transient failure shouldn't meaningfully delay
    the next legitimate attempt to get ahead of the ~1h token
    lifetime."""

    async def connect(self, timeout: float = 10.0) -> None:
        """Opens the MQTT connection (aiomqtt since 0.5.0; a paho worker
        thread before). Also starts the refresh loop in the background,
        if relogin was provided (see class docstring)."""
        await self._mqtt.connect(timeout)
        # ONE REFRESH LOOP, however often connect() is called: a second
        # would log in and swap the token in parallel with the first.
        # And none for a client a disconnect() closed while this connect
        # was running: it would log in every token lifetime, forever,
        # for a connection nobody opens again (review finding).
        if (
            self._relogin is not None
            and not self._mqtt.closed
            and (self._refresh_task is None or self._refresh_task.done())
        ):
            self._refresh_task = asyncio.ensure_future(self._refresh_loop())

    async def disconnect(self) -> None:
        """Stops the token refresh and closes the connection. Running
        watchers stay, and wait: nothing reconnects until connect() is
        called again."""
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.wait({self._refresh_task})
            self._refresh_task = None
        await self._mqtt.disconnect()

    async def _refresh_loop(self) -> None:
        """Proactively logs in again and swaps the MQTT token shortly
        before it expires (see mqtt_client.py's
        seconds_until_token_refresh_due()/replace_token()) -- so
        running watch_*() generators and future request/response calls
        survive the ~1h token lifetime. Returns for good (no further
        refresh) once no expiry time is known anymore -- see
        seconds_until_token_refresh_due()'s docstring for why that's a
        known limitation, not a silent bug.

        HARDENED (this session, prompted by a real field report: an
        integration stuck permanently reconnecting-but-never-
        succeeding, surviving even multiple full application restarts).
        Previously, a single failed relogin()/replace_token() call
        here (a transient network blip at exactly the wrong moment,
        for instance) would propagate out of this method entirely --
        and since this runs as a fire-and-forget background task
        (asyncio.ensure_future() in connect(), never awaited except on
        disconnect()), an unhandled exception here means the task
        simply dies silently. No further proactive refresh EVER
        happens again for this PrimeRobot's lifetime, with no log line
        anywhere pointing at it -- the token then runs out at its
        normal ~1h lifetime with nothing left to renew it, and any
        later reconnect (see _watch_topic()'s own hardening) would
        depend entirely on ITS OWN relogin fallback instead, having
        lost this proactive path for good, silently, possibly hours
        earlier. Now: a failed refresh attempt is logged and retried
        with a short, fixed backoff, rather than ending the loop --
        this task is designed to run for as long as the connection
        does, so a transient failure should delay the next attempt,
        not terminate proactive refreshing permanently."""
        while True:
            wait_seconds = self._mqtt.seconds_until_token_refresh_due()
            if wait_seconds is None:
                return
            await asyncio.sleep(wait_seconds)
            assert self._relogin is not None  # invariant: only started if set
            if self._mqtt.closed:
                # A disconnect() that ran while connect() was starting
                # this loop: no login for a connection nobody reopens.
                return
            if self._reconnect_lock is None:
                self._reconnect_lock = asyncio.Lock()
            try:
                # UNDER THE WATCHERS' LOCK, AND ONLY IF STILL DUE. A
                # watcher that reconnected with a due token has logged in
                # already; refreshing again on the old schedule would be
                # a second login and a second reconnect for nothing
                # (review finding).
                async with self._reconnect_lock:
                    still_due = self._mqtt.seconds_until_token_refresh_due()
                    if still_due is None:
                        return
                    if still_due > 1.0:
                        continue
                    login_result = await self._relogin()
                    new_token = login_result.token_for_blid(self.blid)
                    await self._mqtt.replace_token(new_token)
                # A TOKEN THAT IS DUE AT ONCE would make the next wait
                # zero, and this loop would log in as fast as the server
                # answers (review finding: thousands of logins in two
                # seconds, for a server that hands back a cached token or
                # a host clock that is off). One refresh a minute at most.
                if self._mqtt.seconds_until_token_refresh_due() == 0.0:
                    _LOGGER.warning(
                        "roombapy-prime: the new token for %s is due for refresh at "
                        "once -- is the system clock right? Next attempt in %.0fs",
                        self.blid, self._REFRESH_RETRY_SECONDS,
                    )
                    await asyncio.sleep(self._REFRESH_RETRY_SECONDS)
            except Exception as exc:  # noqa: BLE001
                # A CONNECT TIMEOUT HERE IS NOT AN ERROR, and logging it
                # as one filled @ratpic83's log with a dozen tracebacks
                # a day for something that healed itself in two seconds:
                #
                #   WARN   MQTT reconnect attempt failed (Connect timed out)
                #   ERROR  proactive token refresh failed for <THING>
                #   INFO   MQTT: reconnecting (4 subscriptions to restore)
                #   WARN   MQTT reconnected, watch resumed
                #
                # The retry below is the design, not a fallback -- the
                # refresh is scheduled five minutes before expiry
                # precisely so a failed attempt has room to try again.
                #
                # So a timeout gets a warning with no traceback, and
                # anything else keeps the full one: an auth rejection or
                # a malformed token is a real failure and the stack is
                # what identifies it.
                if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
                    _LOGGER.warning(
                        "roombapy-prime: token refresh for %s timed out -- "
                        "retrying in %.0fs (the refresh runs %.0fs before "
                        "expiry, so there is room for this)",
                        self.blid, self._REFRESH_RETRY_SECONDS,
                        self._mqtt.REFRESH_MARGIN_SECONDS,
                    )
                else:
                    _LOGGER.exception(
                        "roombapy-prime: proactive token refresh failed for %s -- "
                        "will retry in %.0fs rather than giving up on future refreshes",
                        self.blid, self._REFRESH_RETRY_SECONDS,
                    )
                await asyncio.sleep(self._REFRESH_RETRY_SECONDS)

    # --- Shadow-based operations (via mqtt_client.py) -----------------

    async def get_state(self, timeout: float = 8.0) -> ShadowResponse:
        """Classic/unnamed shadow -- identity, capabilities, current
        mission status. Responds reliably on both tiers tested so
        far (EPHEMERAL + SMART).

        Response shape CONFIRMED (this session, real live response,
        chairstacker): for a typed result, apply
        models/robot_info.py::ClassicShadowState.from_json() to
        response.payload["state"]["reported"] (same nesting as
        get_settings()). Was untyped for a long time simply because no
        capture had ever reached this specific (unnamed) shadow before
        -- not because it's less confirmed than the named ones. See
        ClassicShadowState's own docstring, especially the CapabilityFlags
        sub-model (the only per-device capability data found anywhere
        in this project so far) and the schedHold duplication note."""
        return await self._mqtt.get_shadow(None, timeout)

    async def get_settings(self, timeout: float = 8.0) -> ShadowResponse:
        """Named "rw-settings" shadow. IMPORTANT CORRECTION (session
        25): the earlier "SMART tier live-confirmed" claim was
        PREMATURE. The same user (chairstacker), the same device (SKU
        G185020, same BLID), two consecutive runs -- once SUCCESSFUL,
        once TIMEOUT. That's not a stable tier signal, but shows
        either:
        (a) a genuine inconsistency/race condition in this library when
            requesting the named shadow, or
        (b) a genuine, device-side state (e.g. the robot itself might
            need to be actively connected to AWS IoT for a GET on a
            named shadow to be answered -- unlike the classic shadow,
            which might be served from a cache regardless of the
            robot's online status).
        The original "EPHEMERAL vs. SMART" distinction still stands,
        but is NOT the sole explanation for every individual timeout --
        see mqtt_client.py's get_shadow docstring.

        Response shape NOW fully confirmed (session 32, real live
        response): for a typed result, apply
        models/robot_info.py::RobotSettings.from_json() to
        response.payload["state"]["reported"] (same nesting as
        get_state()). Covers things like child lock, volume, timezone,
        pad wash settings, language list, auto-evac frequency --
        resolves a large part of the settings vocabulary previously
        listed as unmodeled in docs/API_REFERENCE.md."""
        return await self._mqtt.get_shadow("rw-settings", timeout)

    async def get_named_shadow(self, name: str, timeout: float = 8.0) -> ShadowResponse:
        """NEW (this session, prompted by a person's own native-binary
        symbol analysis, not this library's own investigation): fetches
        an arbitrary named shadow. get_state() (unnamed/classic) and
        get_settings() ("rw-settings") are thin, specifically-named
        convenience wrappers around this exact same underlying
        capability (mqtt_client.py's get_shadow(named=...), which
        already accepted any string) -- this is that general form,
        exposed publicly so a currently-unconfirmed named shadow can be
        investigated without reaching into a private attribute.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotget_named_shadow
    """
        return await self._mqtt.get_shadow(name, timeout)

    async def set_setting(self, key: str, value: object, timeout: float = 8.0) -> ShadowResponse:
        """Writes to the "rw-settings" shadow. Only meaningful on
        SMART tier -- on EPHEMERAL, presumably the same timeout as
        get_settings(), never tested.

        CONFIRMED WORKING END TO END for childLock (DaRealGuGu, real
        device): write accepted, read-back confirmed, the change showed
        up in the iRobot app, and the robot made an audible
        announcement. That is the first setting whose PHYSICAL effect
        is confirmed rather than only its acceptance. ecoCharge,
        noAutoPasses and vacHigh were also written and read back
        successfully; their real-world effect is untested because none
        is readily observable.

        KNOWN EXCEPTION -- schedHold: the write is accepted and the
        read-back confirms it, but the schedule STAYS ACTIVE in the
        app. Writing schedHold here is not the mechanism the app itself
        uses to pause a schedule.

        AND THE FIRMWARE SAYS WHY. On Prime, `schedHold` appears
        exactly ONCE in the whole root filesystem: as an entry in the
        connectivity broker's key table. Not in `systemApp`, not in
        `everest-server`, not persisted anywhere. The broker's own
        scheduler path uses different identifiers entirely
        (`create_schedule_command`, `rw-schedule`, `cleanSchedule2`)
        and `schedHold` appears in none of them.

        So the value is stored and echoed and nothing reads it. That is
        the whole explanation, and it took a field observation plus a
        firmware read to get there.

        CLASSIC IS THE OPPOSITE, which is worth knowing before anyone
        generalises this. There `schedHold` has a full handler in the
        `scheduler` binary -- type check, value check, its own log line
        on acceptance ("schedHold set to %d"), persisted to the
        schedule keystore and reloaded at boot -- and the consumer sits
        in the trigger path, between the throttle checks and the point
        where a mission would start ("Scheduler is on hold"). It works
        there.

        Worth knowing how that was caught: this project's own
        cross-check against the classic/unnamed shadow's schedHold
        FLAGGED the mismatch (rw-settings said True while classic still
        said False) BEFORE the tester looked in the app -- and the app
        then confirmed it. Two sources disagreeing turned out to mean
        "the write did not really take", which makes that cross-check a
        genuine signal rather than a curiosity. Disabling moved both
        sources in step, so the divergence is specific to enabling.

        Uses the same generic shadow-write mechanism
        trigger_echo_via_shadow() already confirmed works at the
        transport level (a real, accepted update/delta response, not
        just "no error") -- the "rw-" prefix on this shadow's own name
        (as opposed to the four "ro-" shadows) is itself a real signal
        it's meant to be writable, consistent with that result.

        Example: set_setting("carpetBoost", True) to enable the real,
        sensor-driven "boost suction when carpet detected" feature
        (confirmed via iRobot's own public product documentation --
        NOT the three-way Auto/Performance/Eco selector some app code
        suggests, which is confirmed dead code, see
        CarpetBoostSettings's own docstring in models/mission_control.py).

        WHAT IS NOT YET CONFIRMED for any individual key: whether
        writing it actually changes the robot's real behavior, the way
        writing rw-constatus's "echo" field was confirmed to accept
        the write but NOT trigger the expected chime (see
        trigger_echo_via_shadow()'s own entry in docs/internal/EVIDENCE_TRAIL.md). A successful
        ShadowResponse here confirms the WRITE itself worked, not that
        the underlying feature actually changed -- checking the real
        app's own settings screen (or observing the actual behavior)
        after calling this is the only way to confirm a real effect."""
        # ONE KEY, ONE VALUE -- CONFIRMED AS THE VENDOR'S OWN SHAPE.
        #
        # `RobotServiceHandler.settingFromKey(keyPath)` in app 3.0.0 is a
        # switch over 24 individual keys, each returning its own Setting,
        # and `updateSetting(keyPath, value, assetId)` writes exactly
        # that pair. This method already did it that way; the analysis
        # confirms it rather than changing it.
        #
        # DOTTED KEYS ARE ADDRESSED DIRECTLY. `padWetness.padPlate` and
        # the five `langs2.*` keys appear in that switch with their dots
        # intact -- the app targets the sub-key, it does not read the
        # whole map, change one entry and write it back.
        #
        # That retires the read-modify-write advice this project carried
        # for `padWetness`: it described the OLD app's behaviour.
        #
        # WHAT IT DOES NOT RETIRE, BUT DOES NARROW: this project carried
        # the caveat that a region's params override the global value
        # during a mission, and named `CommandParams.copyWith` as the
        # mechanism.
        #
        # THAT MECHANISM IS GONE IN 3.0. `CommandParams.copyWith` has no
        # counterpart there, and `fillBlanksWith` and
        # `onlyUserModifiableParams` -- the other two halves of the
        # merge -- appear nowhere in the extracted surface at all
        # (1636 Kotlin files, 1193 Dart files, count-checked; the six
        # surviving `copyWith` methods sit on unrelated models). Two
        # separate command builders now, neither of which merges.
        #
        # WHAT REMAINS is narrower and still real: `params` is OPTIONAL
        # at region level (`addElement("params", true)`), so a command
        # MAY carry per-region parameters, and those describe that
        # region. A global setting is outranked only by a command that
        # actually sends them -- not by the mere existence of regions.
        #
        # So a global wetness control is tenable, provided the command
        # sending it does not carry region-specific params of its own.
        return await self._mqtt.update_shadow({key: value}, "rw-settings", timeout)

    async def trigger_echo_via_shadow(self, value: object = True, timeout: float = 8.0) -> ShadowResponse:
        """DISPROVEN (this session, chairstacker, real device test) --
        writing to "rw-constatus"'s "echo" field does NOT trigger the
        "find my robot" chime. Kept for what it does confirm (see
        below), not as a working locate mechanism.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robottrigger_echo_via_shadow
    """
        return await self._mqtt.update_shadow({"echo": value}, "rw-constatus", timeout)

    async def send_mission_command(self, command: RoutineCommand, timeout: float = 8.0) -> ShadowResponse:
        """STRONGLY SUSPECTED WRONG (session 39) -- kept for the
        region-based/richer use case (RoutineCommand.regions/params),
        which remains unconfirmed by any source. For basic mission
        control (start/pause/stop/resume/dock/etc.), use
        send_simple_command() instead -- see its docstring for the full
        story of why this method is now believed incorrect.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotsend_mission_command
    """
        return await self._mqtt.update_shadow(command.to_shadow_desired(), None, timeout)

    async def send_simple_command(self, command: str, initiator: str = "localApp") -> bool:
        """A CORRECT COMMAND CAN STILL DO NOTHING, and nothing on the
        wire says so.

        @utkjmitch's Y351020 ignored `start`, `stop`, `dock` and `find`
        for **61 hours** -- each broker-confirmed, each without effect,
        robot idle and mid-mission alike, on fresh sessions and old
        ones. A full "simple verbs are dead on this SKU" report was
        drafted before the pattern showed itself: the robot's cloud
        document had frozen at `{phase: "run", error: 48}` after an
        errored mission, and **every failure predated the power cycle
        that cleared it, every success came after**.

        So this is a fourth way a command fails, beside the three the
        evidence trail records: the payload is right, the transport is
        right, and the robot's own state makes it inert.

        WHAT TO CHECK BEFORE CONCLUDING A COMMAND IS BROKEN: whether
        `cleanMissionStatus.phase` has been `run` for longer than a
        mission takes while `batPct` climbs. Charging and running are
        mutually exclusive; a document claiming both has stopped
        tracking the robot. Only a power cycle clears it -- iRobot's own
        app cannot end its own phantom mission either.

        VERBS CONFIRMED on this SKU once it was cleared: `start` (twice,
        user-observed), `pause`, and `dock` from Home Assistant. Plain
        shape, `initiator: "localApp"`, no map id, no regions.

        THE PATH ITSELF: the corrected mission-control route, replacing
        send_mission_command() for basic commands. See mqtt_client.py's
        cmd_topic()/publish_cmd() docstrings for the full evidence trail
        -- this library's own native disassembly of libcorebase.so,
        independently corroborated by a third-party, unaffiliated GitHub
        project that reports this exact path working against a real
        device.

        Full evidence trail, correction history and open questions:
        docs/internal/EVIDENCE_TRAIL.md#prime_robotsend_simple_command
        """
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_simple_command() needs irbt_topic_prefix (from LoginResult) -- "
                "missing here, so the correct topic can't be built."
            )
        return await self._mqtt.publish_cmd(self._irbt_topic_prefix, command, initiator)

    async def send_routine_command_via_cmd_topic(self, command: RoutineCommand) -> bool:
        """FOR REGIONS. A whole-house clean is `send_simple_command("start")`.

        `clean_all=True` does nothing here, confirmed on hardware
        (@Echovictor37, both with `regions` omitted and with an empty
        list: PUBACK, no effect). `CommandDTO` has thirteen fields and
        `select_all` is not among them -- iRobot's own code strips it
        before sending, so the robot never sees the key.

        There is no clean_all payload shape to find.

        CONFIRMED WORKING on real hardware (@Echovictor37, Combo 105,
        sku Y311240, on b14). The robot cleaned ONLY the targeted room,
        and `operating_mode` correctly selected vacuum-only versus
        vacuum-and-mop, both visually verified.

        THE SHAPE THAT WORKS:

            RoutineCommand(
                command_type=MissionCommandType.START,   # not CLEAN
                asset_id=robot.blid,
                map_id=<active p2map_id>,                # not None
                regions=[Region(region_id=<room_id>,
                                region_type=RegionType.RID,
                                params=CommandParams(operating_mode=...))],
                initiator="rmtApp",
            )

        A THIRD FAILURE MODE, and the one worth remembering. With
        `command_type=CLEAN` and `map_id=None`, the broker returned a
        PUBACK **and the robot cleaned the whole house** -- not the
        requested room, and not nothing either.

        This project already knew two ways a command can fail: no effect
        at all, and a PUBACK followed by silence. This is a third: the
        command is accepted, has an effect, and the effect is not the one
        asked for. **A confirmed send proves delivery, never intent** --
        and a robot cleaning every room when one was requested is the
        most expensive way to learn that.

        NOT UNTESTED, AND THIS PARAGRAPH SAID SO FOR MONTHS. It read
        "STILL UNTESTED: clean_all / select_all=True through this path"
        while the paragraph above recorded @Echovictor37 firing exactly
        that on hardware -- regions omitted and regions empty, PUBACK
        both times, no effect either time.

        Two claims about the same thing in one docstring, and the wrong
        one was the one people read: it sent @BryznNguyen offering to
        spend a hardware run on a question already answered.

        What remains genuinely open is narrower: whether a whole-house
        clean through this path would need the same START-not-CLEAN
        treatment a region clean does. It cannot be answered by sending
        `clean_all`, because that key never reaches the robot.

        FIRMWARE 3.8.126 WAS READ FOR THIS AND DID NOT SETTLE IT. The
        result is recorded here so nobody repeats the search or, worse,
        mistakes what was found for permission:

        - `clean_all` appears in the image ONLY inside the Realtek WiFi
          driver (`phydm_clean_all_csi_mask`) -- an unrelated false
          positive. No `select_all`, `cleanAll` or `selectAll` token
          exists anywhere in it.
        - `robot-command-processor.cpp::process_robot_command` does
          confirm the command field vocabulary (`rid`, `zid`, `ordered`,
          `pmap_id`, `p2map_id`, `poly`, `regions`, `region_id`,
          `user_pmapv_id`, `operatingMode`, ~50 more) -- the payload
          SHAPE, not the command-value literals. No `"start"`/`"clean"`
          string constants: the command type is very likely numeric.
        - The schedule path nearby has
          `create_global_cleaning_task_if_required` /
          `create_room_cleaning_task_if_required`, i.e. "valid region
          selected -> region task, else global" -- the same shape as
          the observed START/CLEAN split. But that is INFERENCE FROM
          CODE LAYOUT, not a confirmed shared call site.

        So the region-vs-global branch exists as shared architecture,
        gated on whether a valid region is present. That is consistent
        with the field-observed behaviour and is not proof of it.

        DISASSEMBLY LATER CORRECTED THE MENTAL MODEL, and the
        correction matters more than the original question.
        `process_robot_command` was disassembled: it hashes the
        command, looks the handler up in a map, validates the
        OPERATING MODE, and calls the handler virtually. It is a
        dispatcher plus a mode gate -- it does NOT contain the
        region-vs-global branch at all. That decision lives downstream
        in the per-command handler.

        WHICH MEANS `command_type=START` IS NOT A SCOPE LIMITER. START
        is the operating mode the dispatcher validates; it says what
        kind of run this is, not how much of the house it covers.
        Scope comes from the presence of region data -- `regions`,
        `region_id`, `rid` inside `params`. There is no `clean_all`
        field in the firmware at all; whole-house is the ABSENCE of a
        region selection, not a flag.

        A caller must therefore not read the field-confirmed
        `START` + `map_id` + `Region(RID)` result as "START keeps it
        to one room". It worked because a region was named. Send a
        command with no regions only when whole-house is positively
        intended.

        THE PREDICATE IS NOW KNOWN, from the app rather than the
        firmware. The handler's own branch resisted static
        disassembly, so the question was answered at the source that
        BUILDS the command: `MissionCommand::toPayload` in Prime
        3.0.0. It null-checks the `regions` list, then checks its
        length, and skips emitting the key on either.

        So the rule is checkable rather than cautionary:

            region clean  <=>  `regions` is a NON-EMPTY array of
                               {(rid|region_id), type} elements
            whole house   <=>  `regions` null or empty, and the key is
                               OMITTED from the payload

        `region_id` is never a top-level field -- it is an element key
        inside `regions`, chosen per element by a type discriminator
        alongside `rid`. So scope is never set by a top-level
        `region_id`, and never by `command_type`/`operatingMode`.

        `to_json()` implements exactly this: an empty list omits the
        key. It previously emitted `regions: []`, a shape the vendor
        client never sends.

        THE SHAPE ABOVE IS NOW FIELD-CONFIRMED, WHICH IS THE OTHER HALF
        OF THAT STORY. @bryznnguyen fired it on a Combo 105 (SKU
        G284020, x05) on b9: `command_type=START`, non-null `map_id`,
        one `Region(RID)`, `initiator="rmtApp"`.

        The proof is the area, not the acknowledgement. The mission ran
        27 minutes and reported 234 sq ft; two prior whole-house runs on
        the same robot both reported exactly 644. One room was cleaned
        and nothing else, so the third failure mode above did not fire.

        WHAT THAT DOES NOT ESTABLISH, in his own framing: the OUTBOUND
        `initiator` is confirmed, the ECHOED one is not -- the
        `cleanMissionStatus` snapshot he persisted came back with blank
        `initiator` and `missionId`, so nothing here says the robot
        reflects the value it was sent. Geometry was not independently
        audited either; the command behaved (right room, right size),
        which is a different claim from boundary-accurate.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotsend_routine_command_via_cmd_topic
    """
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_routine_command_via_cmd_topic() needs irbt_topic_prefix (from LoginResult) "
                "-- missing here, so the correct topic can't be built."
            )
        # WE ARE THE INITIATOR, not whoever created the favourite.
        #
        # @chairstacker (#67): pressing a favourite button in Home
        # Assistant showed up as "iRobot app" in the Activity log, while
        # the vacuum card's Start button showed "Home Assistant"
        # correctly. Two entries wrong, two right, on the same day.
        #
        # The cause is b7's own fix. Favourites are created in the
        # iRobot app, so the stored command carries `initiator:
        # "rmtApp"` -- and restoring that field (which is what made the
        # button work at all) replayed the app's identity along with it.
        #
        # `send_simple_command` has always defaulted to "localApp",
        # which is why the vacuum card reads correctly. The server does
        # not validate this field -- confirmed when a `find` sent as
        # `homeassistant` was accepted and the robot chirped -- so
        # sending ours is safe and truthful.
        #
        # The stored value stays on the parsed favourite: writing it
        # back through create/update must not rewrite what the app
        # recorded.
        payload = command.to_json()
        payload["initiator"] = "localApp"
        return await self._mqtt.publish_cmd_payload(self._irbt_topic_prefix, payload)

    async def send_umi_get_request(self, args: list[str], request_id: int = 1) -> None:
        """EXPERIMENTAL, UNCONFIRMED (this session) -- a well-reasoned
        hypothesis found via native decompilation, NOT a confirmed
        working path. Read the linked evidence trail before using it
        against a real device.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotsend_umi_get_request
    """
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_umi_get_request() needs irbt_topic_prefix (from LoginResult) -- "
                "missing here, so the correct topic can't be built."
            )
        payload = {"do": "get", "args": args, "id": request_id}
        await self._mqtt.publish_cmd_payload(self._irbt_topic_prefix, payload)

    # --- REST-based p2maps operations (already natively async) -------

    async def get_active_map_versions(self) -> list[dict[str, Any]]:
        """NEW (July 11, eleventh session) -- was missing as a wrapper
        until now, even though rest_client.py's version had already
        existed for a while."""
        return await self._rest.get_active_map_versions(self.blid)

    async def get_map_metadata(self, p2map_id: str) -> P2MapData:
        """UPDATED (session 51) -- now returns a parsed P2MapData, see
        rest_client.py::get_map_metadata()'s docstring."""
        return await self._rest.get_map_metadata(p2map_id)

    async def set_map_name(self, p2map_id: str, name: str) -> dict[str, Any]:
        return await self._rest.set_map_name(p2map_id, name)

    async def set_map_orientation(self, p2map_id: str, orientation_rad: float) -> dict[str, Any]:
        return await self._rest.set_map_orientation(p2map_id, orientation_rad)

    async def delete_map(self, p2map_id: str) -> dict[str, Any]:
        """NEW (thirteenth session) -- was missing as a wrapper despite
        a rest_client.py version having existed for a while (found
        during a systematic review)."""
        return await self._rest.delete_map(p2map_id)

    async def get_map_region_names(
        self, map_id: str, map_version: str
    ) -> dict[str, str]:
        """{region_id: name} for every named room AND zone on a map.

        The third source of region names, and apparently the one the
        other two are derived from. `rooms_metadata` carries rooms
        only; the bundle's `cleanZones` layer was empty on the one
        robot anyone has checked.

        Reads `GET /v1/p2maps/{id}/versions/{vid}` and its
        `geojson_details.regions` -- see `get_map_version()` in
        rest_client for where this shape came from and what is not yet
        verified about it.

        Returns {} rather than raising when the key is absent, because
        this project has not seen the response on a robot.
        """
        from .models.robot_info import parse_map_version_regions

        return parse_map_version_regions(
            await self._rest.get_map_version(map_id, map_version)
        )

    async def get_map_region_ids(
        self, map_id: str, map_version: str
    ) -> list[str]:
        """Every region id the CURRENT map version carries.

        The p2map's own `rooms_metadata` is a snapshot and can lag
        behind zone edits -- @chairstacker had twelve zones and a
        listing built from it showed eight. The version is what the
        robot is actually working from, so it is the honest source for
        "which regions exist".
        """
        from .models.robot_info import parse_map_version_region_ids

        return parse_map_version_region_ids(
            await self._rest.get_map_version(map_id, map_version)
        )

    async def get_map_geojson_link(self, map_id: str, map_version: str) -> dict[str, Any]:
        """NEW (thirteenth session) -- was missing as a wrapper. Returns
        the presigned download URL for download_map_bundle() (see
        there). CORRECTED (session 48, this docstring was outdated):
        response shape IS confirmed -- the URL lives under the
        "map_url" key (P2MapURL$$serializer's own <clinit>), not an
        unconfirmed guess among candidate keys the way this docstring
        used to say. See rest_client.py's own get_map_geojson_link()
        docstring for the full evidence trail."""
        return await self._rest.get_map_geojson_link(map_id, map_version)

    async def get_map_raw_link(
        self, map_id: str, map_version: str, response_type: str | None = "link"
    ) -> dict[str, Any]:
        """The same map version in the vendor's raw format -- see
        rest_client.py::get_map_raw_link().

        Wrapped at the same time as the REST method rather than a
        release later: this project has twice shipped a REST call whose
        wrapper was missing or dropped a parameter, and both times a
        tester's whole run died before a request left the machine."""
        return await self._rest.get_map_raw_link(map_id, map_version, response_type)

    async def download_map_bundle(self, url: str) -> bytes:
        """NEW (thirteenth session) -- was missing as a wrapper, even
        though the diagnostics script and parse_map_bundle() depend on
        it. Deliberately WITHOUT SigV4 signing -- see rest_client.py's
        docstring."""
        return await self._rest.download_map_bundle(url)

    async def edit_map(
        self, p2map_id: str, command: MapEditCommandV1,
        response_type: str | None = "link",
    ) -> dict[str, Any]:
        """command is one of the 9 V1 command dataclasses from
        models/map_editing.py (RenameRoomV1, SplitRoomV1, MergeRoomsV1,
        ...) -- the actually active path (see rest_client.py's
        docstring, PRIME_APP_GAP_ANALYSIS). For the unused V2 path see
        edit_map_v2().

        response_type forwarded to the REST client -- see its own
        docstring for why it is a parameter at all.

        ADDED HERE ONE RELEASE LATE (a29). a28 added it to the REST
        client and not to this wrapper, so all three variants of a
        field experiment died with TypeError before a single request
        left the machine. The tester's whole run was wasted, and the
        script then printed "that rules out response_type as the
        cause" -- which was false, because nothing had been tested."""
        return await self._rest.edit_map(p2map_id, command, response_type=response_type)

    async def edit_map_checked(
        self, p2map_id: str, command: MapEditCommandV1,
        response_type: str | None = "link",
    ) -> MapEditResult:
        """edit_map(), with the answer read instead of handed back raw.

        SAME REQUEST, PARSED RESPONSE. edit_map() stays exactly as it
        is -- callers relying on the raw dict keep working, and a first
        real response can still be inspected whole through `.raw`.

        WHY A SECOND METHOD RATHER THAN A CHANGED RETURN TYPE: nothing
        here has ever sent a map edit outside a dry run, so the four
        response shapes are documented and unobserved. Changing what
        edit_map() returns would bet a working signature on that; adding
        a method next to it does not.

        The distinction worth having is partial success -- a new map
        version with no URL, meaning the edit applied and the rendered
        map did not follow. Raw JSON made that indistinguishable from
        success, and a caller would have shown a stale map without
        knowing."""
        return MapEditResult.from_json(
            await self._rest.edit_map(p2map_id, command, response_type=response_type)
        )

    async def edit_map_v2(self, p2map_id: str, command: MapEditCommand) -> dict[str, Any]:
        """The V2 path never called by the app itself -- see
        edit_map()'s docstring and rest_client.py::edit_map_v2()."""
        return await self._rest.edit_map_v2(p2map_id, command)

    async def edit_map_v2_checked(
        self, p2map_id: str, command: MapEditCommand
    ) -> MapEditResult:
        """edit_map_v2(), with the answer read. See edit_map_checked()."""
        return MapEditResult.from_json(
            await self._rest.edit_map_v2(p2map_id, command)
        )

    async def get_live_map_stream(self) -> LiveMapStreamInit:
        """CORRECTED UNDERSTANDING (July 11, see
        docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md point B1): this REST
        call is likely a KEEP-ALIVE ping, not a "give me the topic"
        call -- in the real app, the response
        (LiveMapStreamResponse.mqtt_topic) is never read anywhere, only
        parsed. watch_live_map() accordingly no longer uses this
        method to determine the topic, only as a periodic background
        keep-alive. Still public for callers who need the raw REST
        call itself."""
        return await self._rest.get_live_map_stream(self.blid)

    # --- Favorites (FavoriteV1) ------------------------------------------

    async def get_favorites(
        self, app_edition: str | None = "1"
    ) -> list[FavoriteV1]:
        """See rest_client.py::get_favorites() -- the only one of the
        five favorite endpoints whose HTTP method AND response shape
        are both fully confirmed."""
        # Only forwarded when it differs from the client's own default,
        # so the plain call stays a plain call.
        if app_edition == "1":
            return await self._rest.get_favorites()
        return await self._rest.get_favorites(app_edition)

    async def get_favorites_raw(
        self, app_edition: str | None = "1"
    ) -> list[dict[str, Any]]:
        """See rest_client.py::get_favorites_raw() -- diagnostic
        round-trip fidelity check, not part of the normal path."""
        if app_edition == "1":
            return await self._rest.get_favorites_raw()
        return await self._rest.get_favorites_raw(app_edition)

    async def create_favorite(self, favorite: FavoriteV1) -> dict[str, Any]:
        """See rest_client.py::create_favorite() -- HTTP method
        (POST) confirmed (eighth session)."""
        return await self._rest.create_favorite(favorite)

    async def update_favorite(self, favorite_id: str, favorite: FavoriteV1) -> dict[str, Any]:
        """See rest_client.py::update_favorite() -- HTTP method
        (PUT) confirmed (eighth session)."""
        return await self._rest.update_favorite(favorite_id, favorite)

    async def delete_favorite(self, favorite_id: str) -> dict[str, Any]:
        return await self._rest.delete_favorite(favorite_id)

    async def order_favorite(
        self,
        favorite_id: str,
        *,
        insert_at: int | None = None,
        insert_before: str | None = None,
        insert_after: str | None = None,
    ) -> dict[str, Any]:
        return await self._rest.order_favorite(
            favorite_id, insert_at=insert_at, insert_before=insert_before, insert_after=insert_after
        )

    async def get_mission_history(
        self,
        blid: str,
        *,
        max_reports: int | None = None,
        max_age: int | None = None,
        filter_type: str | None = None,
        exclusive_start_timestamp: int | None = None,
        supported_done_codes: list[str] | None = None,
    ) -> Any:
        """See rest_client.py::get_mission_history() -- fully
        confirmed from FetchMissionHistoryRequest.java. Returns a list
        of records in practice (annotated `dict` until 0.4.0)."""
        return await self._rest.get_mission_history(
            blid,
            max_reports=max_reports,
            max_age=max_age,
            filter_type=filter_type,
            exclusive_start_timestamp=exclusive_start_timestamp,
            supported_done_codes=supported_done_codes,
        )

    async def get_schedules(self, household_id: str) -> SchedulesResponse:
        """UPDATED (session 51) -- now returns a parsed
        SchedulesResponse, see rest_client.py::get_schedules()'s
        docstring."""
        return await self._rest.get_schedules(household_id)

    async def get_dnd_settings_raw(self, household_id: str) -> Any:
        """See rest_client.py::get_dnd_settings_raw() -- the first
        populated quiet-hours response is what unblocks the feature."""
        return await self._rest.get_dnd_settings_raw(household_id)

    async def get_automations_raw(self) -> Any:
        """See rest_client.py::get_automations_raw() -- third-party
        triggers and geofencing, NOT schedules. Endpoint liveness
        unproven."""
        return await self._rest.get_automations_raw()

    async def get_firmware_raw(self, sku: str | None = None) -> Any:
        """See rest_client.py::get_firmware_raw() -- available releases,
        method and envelope both unconfirmed.

        Takes the sku because this object does not have one: the sku is
        on the login entry, not on the robot. Writing `self.sku` here was
        the fifth invented attribute in a single day, and @DaRealGuGu
        found it on the first run.
        """
        # NO `self.sku` EXISTS, and writing one was the fifth invented
        # attribute in a single day (@DaRealGuGu found it on the first
        # run). The sku lives on the login entry, not on the robot
        # object -- and a caller who has one can pass it.
        return await self._rest.get_firmware_raw(sku)

    async def get_firmware(self, sku: str | None = None) -> list[FirmwareItem]:
        """Available firmware releases, parsed.

        The envelope is `{"firmware": [item, ...]}`, confirmed against a
        live response (SKU W155040). Returns the items; an empty list
        means the catalogue had nothing for this sku rather than an
        error. See get_firmware_raw() for the unparsed form and the
        host/parameter history.
        """
        raw = await self._rest.get_firmware_raw(sku)
        items = raw.get("firmware") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        return [FirmwareItem.from_json(item) for item in items if isinstance(item, dict)]

    async def get_clean_score_raw(self, p2map_id: str) -> Any:
        """See rest_client.py::get_clean_score_raw() -- per-room
        cleanliness, request body still a guess."""
        return await self._rest.get_clean_score_raw(p2map_id)

    async def get_schedules_raw(self, household_id: str) -> Any:
        """See rest_client.py::get_schedules_raw() -- field diagnosis,
        not part of the normal path."""
        return await self._rest.get_schedules_raw(household_id)

    async def create_schedules(self, household_id: str, schedules: list[ScheduleOptions]) -> dict[str, Any]:
        """HTTP method (POST) confirmed (eighth session), see
        rest_client.py::create_schedules()."""
        return await self._rest.create_schedules(household_id, schedules)

    async def update_schedules(
        self, household_id: str, household_schedule_id: str, schedules: list[HouseholdSchedule]
    ) -> dict[str, Any]:
        """HTTP method (PUT) confirmed (eighth session)."""
        return await self._rest.update_schedules(household_id, household_schedule_id, schedules)

    async def delete_schedule(self, household_id: str, household_schedule_id: str) -> dict[str, Any]:
        return await self._rest.delete_schedule(household_id, household_schedule_id)

    async def get_user_households(self) -> dict[str, Any]:
        """Not used by the current app version -- see
        rest_client.py::get_user_households()'s docstring."""
        return await self._rest.get_user_households()

    async def get_household_id(self) -> str | None:
        """Convenience wrapper: finds the household_id of the
        household that contains THIS robot (matched by
        HouseholdRobot.robot_id == self.blid), without the caller
        needing to know the response shape.

        Response shape handled defensively on purpose: get_user_households()'s
        own docstring describes a CONFIRMED real response with
        household_id/owner_cognito_id/etc. as TOP-LEVEL keys (a single
        household, not a list) -- but parse_user_households() (this
        module's own models) expects `list[dict] | None`. These two
        haven't been reconciled against a real multi-household account,
        so this method accepts either shape rather than assuming one:
        a bare dict (single household) or a list of dicts (multiple
        households, or a wrapping structure).

        Returns None if no household contains a robot matching this
        blid (including the case where the account genuinely has none) --
        never raises for a simple "not found".

        MATCHING WIDENED (this session, real field report): this used to
        compare only `robot.robot_id == self.blid`, i.e. it silently
        assumed those two identifiers are the same value. On one real
        account they are not -- a 16-character BLID
        ("3178480C91223620") alongside a 32-character robot_id
        ("0B710054CA277C04B2700374A8349C9A"), with the robot's own map
        id carrying the robot_id's prefix rather than the BLID's. On
        another account they are identical, so the assumption held
        everywhere it had been tested.

        The consequence was not an error but a silent None, which then
        made every household-scoped operation -- schedule writes above
        all -- fail on that account for reasons that would have looked
        like anything except an identifier mismatch.

        Now matches against self.robot_id -- the value the LOGIN
        response gives for this BLID -- falling back to the BLID itself.
        A first attempt at this compared against a `blid` attribute on
        the household robot entries; that was useless, because those
        entries carry only robot_id and never had such a field. The
        identifier we need was in the login response all along."""
        from .models import parse_user_households

        raw = await self.get_user_households()
        if isinstance(raw, dict) and "household_robots" in raw:
            raw_list = [raw]
        elif isinstance(raw, list):
            raw_list = raw
        else:
            raw_list = []

        for household in parse_user_households(raw_list):
            if any(r.robot_id in (self.robot_id, self.blid) for r in household.household_robots):
                return household.household_id
        return None

    async def get_dnd_settings(self, household_id: str) -> DNDStatusResponse:
        """UPDATED (session 53) -- now returns a parsed
        DNDStatusResponse, see rest_client.py's docstring."""
        return await self._rest.get_dnd_settings(household_id)

    async def set_dnd_settings(self, household_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        return await self._rest.set_dnd_settings(household_id, settings)

    async def get_cleaning_profiles(self, asset_id: str, p2map_id: str | None = None) -> dict[str, Any]:
        """NEW (session 6) -- see rest_client.py::get_cleaning_profiles(). `p2map_id` is
        optional, matching the real query construction (session 38)."""
        return await self._rest.get_cleaning_profiles(asset_id, p2map_id)

    async def get_default_routines(self, p2map_id: str) -> RoutinesDefaultsResponse:
        """UPDATED (session 53) -- now returns a parsed
        RoutinesDefaultsResponse, see rest_client.py's docstring."""
        return await self._rest.get_default_routines(p2map_id)

    async def get_robot_parts(self) -> RobotPartsInfo:
        """NEW (session 15) -- see rest_client.py::get_robot_parts().
        UPDATED (session 53) -- now returns a parsed RobotPartsInfo."""
        return await self._rest.get_robot_parts(self.blid)

    async def reset_robot_parts(
        self,
        part_ids: Sequence[str],
        counters: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """Mark parts as new -- see rest_client.py::reset_robot_parts().

        `counters` maps a part id to the value to write; anything not
        named resets to zero. Forwarded because the REST client gained
        it and a wrapper that drops a parameter is how a tester's whole
        run once died on `response_type`.

        `part_ids` is required since 0.6.0: the call without it sent a
        body naming no part, which nothing could act on."""
        return await self._rest.reset_robot_parts(self.blid, part_ids, counters)

    async def get_serial_number_data(self) -> RobotSerialInfo:
        """NEW (session 15) -- see rest_client.py::get_serial_number_data().
        UPDATED (session 53) -- now returns a parsed RobotSerialInfo."""
        return await self._rest.get_serial_number_data(self.blid)

    async def poll_echo_value(self) -> dict[str, Any]:
        """NEW (session 16) -- "find my robot" feature, see
        rest_client.py::poll_echo_value()."""
        return await self._rest.poll_echo_value(self.blid)

    async def get_time_estimates(
        self,
        smart_map_id: str | None = None,
        region_id: str | None = None,
        zone_id: str | None = None,
    ) -> dict[str, Any]:
        """Per-room time estimates for this robot.

        Takes no arguments now: the request body is `{"robot_id": blid}`
        and this object already knows its blid. The previous signature
        took a raw dict because the body shape was unknown -- see
        rest_client.py::get_time_estimates() for how it was traced.
        """
        # Only what was asked for is forwarded, so the plain call stays
        # a plain call -- a caller that names nothing produces exactly
        # the request this library has field-confirmed.
        narrowing = {
            k: v for k, v in (
                ("smart_map_id", smart_map_id),
                ("region_id", region_id),
                ("zone_id", zone_id),
            ) if v is not None
        }
        return await self._rest.get_time_estimates(self.blid, **narrowing)

    async def reset_robot(
        self,
        robot_password: str | None = None,
        synchronous: bool | None = None,
        send_wipe: bool | None = None,
    ) -> dict[str, Any]:
        """NEW (session 16) -- WARNING: likely a consequential action,
        see rest_client.py::reset_robot().

        The three body fields are forwarded because a wrapper that drops
        a parameter is how a tester's whole run once died on
        `response_type` -- and on this endpoint the dropped parameter
        would be `send_wipe`."""
        return await self._rest.reset_robot(
            self.blid, robot_password, synchronous, send_wipe
        )

    async def get_notifications(self, app_version: str = "2.2.4") -> dict[str, Any]:
        """NEW (session 16) -- see rest_client.py::get_notifications(). Default
        `app_version` updated in session 36, see that method's docstring."""
        return await self._rest.get_notifications(self.blid, app_version)

    # --- Continuous dispatch loops --------------------------------------

    async def watch_state(
        self,
        named: str | None = None,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        max_reconnect_backoff: float = DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS,
    ) -> AsyncIterator[ShadowResponse]:
        """Delivers every shadow delta as soon as it arrives -- until
        the caller breaks the iteration (break/return from an
        `async for`, or .aclose()).

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotwatch_state
    """
        topic = self._mqtt.shadow_topic("update/delta", named=named)
        # contextlib.aclosing() (not a bare `async for`) is required here --
        # a bare `async for inner_gen(): yield ...` does NOT guarantee
        # inner_gen's .aclose() runs when THIS generator is closed (a real
        # bug found this session: unsubscribe() in _watch_topic()'s finally
        # block never fired on agen.aclose(), only on natural exhaustion).
        async with contextlib.aclosing(
            self._watch_topic(
                topic, queue_maxsize=queue_maxsize, max_reconnect_backoff=max_reconnect_backoff
            )
        ) as inner:
            async for response in inner:
                yield response

    async def request_mission_timeline(self) -> int:
        """Asks the robot for its mission timeline and returns the id.

        The report arrives on the watch topic carrying the same
        `timelineRequestId`, so a caller that has a watcher running can
        match the answer to its question rather than taking the next
        thing that appears.

        The counter starts at 1 and increments, as the app's does.
        Reusing an id would make two requests indistinguishable, which
        is the one thing this field exists to prevent.
        """
        self._timeline_request_id += 1
        request_id = self._timeline_request_id
        if self._irbt_topic_prefix is None:
            # Same guard the watch_*() methods carry; this one was
            # missing, so a robot constructed without a LoginResult
            # would have sent the request to "None/things/...".
            raise ValueError(
                "request_mission_timeline() needs irbt_topic_prefix (from LoginResult) -- "
                "this was None."
            )
        await self._mqtt.request_mission_timeline(self._irbt_topic_prefix, request_id)
        return request_id

    async def watch_mission_timeline(
        self,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        max_reconnect_backoff: float = DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS,
    ) -> AsyncIterator[ShadowResponse]:
        """NEW (this session) -- EXPLORATORY, not yet confirmed live.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotwatch_mission_timeline
    """
        if self._irbt_topic_prefix is None:
            raise ValueError(
                "watch_mission_timeline() needs irbt_topic_prefix (from LoginResult) -- "
                "this was None."
            )
        topic = self._mqtt.mission_timeline_topic(self._irbt_topic_prefix, report=True)
        # See watch_state()'s equivalent comment -- aclosing() is required,
        # not a bare `async for`, for the inner generator's cleanup to run
        # reliably when THIS generator is closed.
        async with contextlib.aclosing(
            self._watch_topic(
                topic, queue_maxsize=queue_maxsize, max_reconnect_backoff=max_reconnect_backoff
            )
        ) as inner:
            async for response in inner:
                yield response

    async def watch_dock_reports(
        self,
        report_type: str | None = None,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        max_reconnect_backoff: float = DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS,
    ) -> AsyncIterator[ShadowResponse]:
        """Watch the `dock/{reportType}/report` family.

        `dock/paddry/report` is confirmed live (chairstacker) and
        parses into DockReport. With no `report_type` this subscribes
        the whole family via a `+` wildcard, which is the only way to
        find out whether a sibling like `charge` or `battery` exists --
        the open question this method is built to answer.

        Payloads parse with DockReport.from_json(); a `reportType` the
        parser has not seen still comes through, with its fields
        best-effort mapped and the rest reaching the caller as raw.

        `evac/report` is a sibling one level up and is not covered here
        -- use watch_raw_topic for it.
        """
        if self._irbt_topic_prefix is None:
            raise ValueError(
                "watch_dock_reports() needs irbt_topic_prefix (from LoginResult) -- "
                "this was None."
            )
        topic = self._mqtt.dock_report_topic(self._irbt_topic_prefix, report_type)
        async with contextlib.aclosing(
            self._watch_topic(
                topic, queue_maxsize=queue_maxsize, max_reconnect_backoff=max_reconnect_backoff
            )
        ) as inner:
            async for response in inner:
                yield response

    async def watch_rejected_commands(
        self,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        max_reconnect_backoff: float = DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS,
    ) -> AsyncIterator[ShadowResponse]:
        """NEW (this session) -- EXPLORATORY, not yet confirmed live.

        Subscribes to {irbt_prefix}/things/{blid}/rejected/report,
        found via the same native decompilation pass as
        watch_mission_timeline() (AssetIotTopicFactory's third method,
        createCommandRejectedTopic() -- a sibling of the
        already-live-confirmed createCommandPublishTopic() behind
        cmd_topic()/send_simple_command()).

        DIRECTLY COMPLEMENTS send_simple_command(): if a command call
        appears to succeed (no exception) but the robot doesn't react,
        this topic is where a rejection reason -- if the device reports
        one at all -- would be expected to arrive. Same confidence
        level as watch_mission_timeline(): see
        rejected_report_topic()'s own docstring.

        Needs irbt_topic_prefix, same as watch_mission_timeline() --
        raises ValueError immediately if not available.

        Same reconnect-with-backoff behavior as the other watch_*()
        methods -- see _watch_topic()'s docstring.
        """
        if self._irbt_topic_prefix is None:
            raise ValueError(
                "watch_rejected_commands() needs irbt_topic_prefix (from LoginResult) -- "
                "this was None."
            )
        topic = self._mqtt.rejected_report_topic(self._irbt_topic_prefix)
        async with contextlib.aclosing(
            self._watch_topic(
                topic, queue_maxsize=queue_maxsize, max_reconnect_backoff=max_reconnect_backoff
            )
        ) as inner:
            async for response in inner:
                yield response

    async def watch_raw_topic(
        self,
        topic: str,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        max_reconnect_backoff: float = DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS,
    ) -> AsyncIterator[ShadowResponse]:
        """NEW (this session) -- a thin, public wrapper around
        _watch_topic() for ad-hoc diagnostic subscriptions to a topic
        this library has no dedicated method for yet.

        CONCRETE USE CASE (not just hypothetical): a wildcard
        subscription like "{irbt_prefix}/things/{blid}/#" is currently
        the only way to potentially catch robot position/pose data --
        createRobotPositionTopic() (a sibling of
        mission_timeline_topic()/rejected_report_topic() in the same
        native factory) builds its topic dynamically at runtime rather
        than from a static format string, so no literal path exists to
        subscribe to directly. See mqtt_client.py's notes next to
        rejected_report_topic() for the full investigation trail
        (including a separate finding that pose data specifically can
        arrive over MQTT, distinct from plain "position").

        Same reconnect-with-backoff behavior as watch_state()/
        watch_mission_timeline() -- see _watch_topic()'s own docstring.
        Deliberately does not validate or construct the topic string at
        all -- the caller is responsible for it, unlike the dedicated
        watch_*() methods above which build a specific, evidenced
        topic themselves."""
        async with contextlib.aclosing(
            self._watch_topic(
                topic, queue_maxsize=queue_maxsize, max_reconnect_backoff=max_reconnect_backoff
            )
        ) as inner:
            async for response in inner:
                yield response

    async def watch_named_shadows_updates(
        self,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        max_reconnect_backoff: float = DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS,
    ) -> AsyncIterator[ShadowResponse]:
        """Watches update/accepted across ALL named shadows at once via
        a single-level ("+") wildcard subscription -- CONFIRMED
        SAFE, distinct from the reserved-namespace multi-level ("#")
        wildcard this project already removed (--watch-aws-tree, see
        that flag's own removal history) after it caused a real
        connection disruption. AWS's own MQTT design guidance
        distinguishes the two explicitly: multi-level ("#") wildcards
        are discouraged for device subscriptions ("reserve use of
        multi-level wildcards as part of the IoT rules engine"),
        while single-level ("+") wildcards are the RECOMMENDED
        approach for exactly this use case -- subscribing across
        several named shadows without listing each one individually.
        A native-analysis track independently found the real app uses
        this exact pattern (a "+" wildcard on the shadow-name segment
        of update/accepted) to monitor all its named shadows at once.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotwatch_named_shadows_updates
    """
        topic = f"$aws/things/{self.blid}/shadow/name/+/update/accepted"
        async with contextlib.aclosing(
            self._watch_topic(
                topic, queue_maxsize=queue_maxsize, max_reconnect_backoff=max_reconnect_backoff
            )
        ) as inner:
            async for response in inner:
                yield response

    async def _watch_topic(
        self,
        topic: str,
        *,
        queue_maxsize: int,
        max_reconnect_backoff: float,
    ) -> AsyncGenerator[ShadowResponse, None]:
        """Shared core behind watch_state()/watch_mission_timeline() --
        extracted (this session) when the second caller appeared, to
        avoid duplicating the reconnect-hardening logic.

        RECONNECTS TRANSPARENTLY (reconnect hardening): previously a
        dropped connection left a caller of this hung forever on an
        empty queue with no signal anything was wrong -- mqtt_client.py
        had no on_disconnect handling at all. Now, a drop is detected
        via self._mqtt.wait_for_disconnect() and triggers an automatic
        reconnect with exponential backoff (1s, 2s, 4s, ... capped at
        max_reconnect_backoff), unbounded retry count -- appropriate
        for a long-running background consumer (e.g. a Home Assistant
        coordinator) that should keep trying rather than give up
        permanently. The caller's `async for` loop never sees this
        happen; it just resumes receiving messages once reconnected.
        Only a caller-initiated break/.aclose() ends this generator
        now, not a connection drop.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[ShadowResponse] = asyncio.Queue(maxsize=queue_maxsize)

        def _on_message(response: ShadowResponse) -> None:
            loop.call_soon_threadsafe(_put_with_backpressure, queue, response, topic)

        await self._mqtt.subscribe(topic, _on_message)
        # THE CONNECTION THIS WATCHER WATCHES. A drop is "this generation
        # has ended", which stays true however late the watcher asks --
        # see wait_for_disconnect() for what an event missed before.
        generation = self._mqtt.generation
        try:
            while True:
                get_task = asyncio.ensure_future(queue.get())
                end_task = asyncio.ensure_future(self._mqtt.wait_for_disconnect(generation))
                done = await _first_of(get_task, end_task)

                if get_task in done:
                    # A drop that arrived together with this message is
                    # not lost: the next wait reports it at once.
                    yield get_task.result()
                    continue

                generation = await self._resume_after_end(
                    generation, topic, max_reconnect_backoff
                )
        finally:
            await self._mqtt.unsubscribe(topic, _on_message)

    _MAX_UNCONFIRMED_RETRIES = 2
    _SUBACK_RECHECK_SECONDS = 1.0
    """How long after a reconnect a watcher looks again at the restored
    subscriptions: a late SUBACK is still a SUBACK."""
    _FIRST_RECONNECT_BACKOFF = 1.0
    """How often a watcher reconnects because a restored subscription got
    no SUBACK. Unbounded until 0.5.0b1's review: on a session that never
    shows SUBACKs (@utkjmitch) one drop became a reconnect every minute,
    each tearing down a connection that was delivering, and the watcher
    delivered nothing while it looped."""

    def _report_drop(self, generation: int, reason: str, topic: str) -> bool:
        """Logs the end of connection `generation` -- once, however many
        watchers notice it. Returns whether this call logged it.

        ONCE PER CONNECTION. Since every watcher hears of a drop (0.4.x
        woke only one), each one logged it and counted it: with Home
        Assistant's three watchers one ordinary drop made three WARNING
        lines and tripped the three-drops warning below on its own
        (review finding, 0.5.0b1)."""
        if generation <= self._drop_reported_generation:
            return False
        self._drop_reported_generation = generation
        _LOGGER.warning(
            "roombapy-prime: MQTT connection dropped (%s) while watching %s -- reconnecting",
            reason, topic,
        )
        # DROPS ARE NORMAL. EVICTION IS ONE CAUSE OF MANY.
        #
        # AN EARLIER VERSION OF THIS NOTE NAMED EVICTION AS THE
        # EXPLANATION, and @ratpic83 disproved that within a day: he
        # force-quit the iRobot app on every phone in the household and
        # the drops continued, roughly 82 and 55 minutes apart. That
        # spacing reads like a credential or session lifetime rather
        # than a race, and the subscription recovered on its own each
        # time -- his robot ran a full job and docked correctly during
        # that window.
        #
        # So the useful question is not "who evicted whom" but
        # "does the reconnect succeed", and on the evidence it does. A drop every
        # hour with a recovery after it is working software.
        #
        # WHAT STILL POINTS AT EVICTION is FREQUENCY. A session lifetime
        # does not expire every few seconds. Several drops inside five
        # minutes is a different phenomenon from one an hour, and worth
        # naming when it happens -- but as the first thing to rule out,
        # not as the answer.
        #
        # AWS IoT disconnects the OLDER connection when a second one
        # arrives with the same client_id, and this server issues the
        # client_id -- it is not ours to randomise. So a phone app and
        # this library talking to one robot take turns evicting each
        # other, and each side sees an unexplained "Normal
        # disconnection". @ratpic83's Home Assistant received nothing for
        # fifteen minutes while `roombapy-prime-validate`, run from a
        # SEPARATE machine on the same account, passed 28 checks on the
        # first try.
        now = _time.monotonic()
        recent = [t for t in self._recent_drops if now - t < 300.0]
        recent.append(now)
        self._recent_drops = recent
        if len(recent) >= 3:
            _LOGGER.warning(
                "roombapy-prime: %d disconnections in five minutes on "
                "%s -- faster than a session lifetime, so something is "
                "cutting them short. Our own token refresh is no "
                "longer a candidate: those are recognised and not "
                "reported here. Another client on the same account (a "
                "second Home Assistant, a diagnostic script, the "
                "iRobot app) is the thing to rule out -- AWS IoT "
                "evicts the older connection when a second one uses "
                "the same client_id, and this server assigns it. Each "
                "drop reconnects on its own; watch for the 'watch "
                "resumed' line.",
                len(recent), topic,
            )
        return True

    async def _resume_after_end(
        self, generation: int, topic: str, max_reconnect_backoff: float
    ) -> int:
        """Called by a watcher whose connection `generation` has ended.
        Returns the generation of the connection to watch next, once one
        is up -- rebuilding it if nobody else does.

        ONE COORDINATOR AT A TIME. The watcher holding this robot's lock
        rebuilds, backs off and retries; the others wait for the lock and
        then find the new connection up. Two watchers each reconnecting
        take turns tearing down each other's connection -- the
        ping-pong a live validation log once showed as dozens of
        immediate drops, every reconnect succeeding and then being
        evicted by the other task.

        OUR OWN DISCONNECT IS NOT A DROP. @ratpic83 (2026-08-16) logged
        26 disconnects in a day, each exactly 55 minutes apart:
        authenticate, reconnect, THEN the "drop" -- ours, from the
        proactive token refresh. A connection that is already up again,
        whoever rebuilt it, is simply adopted; a deliberate end is not
        logged at all (26 lines a day saying the thing worked as
        designed).

        A CLOSED CLIENT IS NOT REBUILT. After robot.disconnect() the
        watcher waits until connect() opens it again (review finding:
        watch_live_map() used to undo a disconnect at once)."""
        mqtt = self._mqtt
        # A token swap or another reconnect in progress finishes first:
        # its result is the connection to watch.
        await mqtt.wait_until_settled()
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()
        reported = False
        async with self._reconnect_lock:
            backoff = self._FIRST_RECONNECT_BACKOFF
            while not mqtt.closed:
                current = mqtt.generation
                if mqtt.connected and current != generation:
                    # Someone rebuilt it: a token swap, a shadow read's
                    # lazy reconnect, or the watcher before us. A real
                    # drop repaired that way is still worth its line.
                    end = mqtt.ended(generation)
                    if end is not None and not end[1]:
                        reported = self._report_drop(generation, end[0], topic) or reported
                    if reported:
                        _LOGGER.warning(
                            "roombapy-prime: MQTT reconnected, watch resumed for %s", topic
                        )
                    else:
                        _LOGGER.debug(
                            "roombapy-prime: resuming %s on connection %d", topic, current
                        )
                    return current

                # Down, and nobody is rebuilding it: this watcher does.
                reported = (
                    self._report_drop(current, mqtt.disconnect_reason or "unknown", topic)
                    or reported
                )
                try:
                    # A FRESH TOKEN ONLY WHEN THE OLD ONE IS DUE. reconnect()
                    # reuses the token it has; if a drop lands after it
                    # expired (or the refresh task died), every attempt
                    # would reuse a token that can no longer connect --
                    # "stuck, restart doesn't help". But logging in on
                    # every reconnect traded a fast MQTT reconnect for a
                    # full Gigya + iRobot login on every blip. And only
                    # ONE watcher does it now, under this lock: with
                    # three watchers, a due token meant three logins and
                    # three reconnects, each tearing down the connection
                    # the one before had built (review finding).
                    relogin = self._relogin
                    if relogin is not None and mqtt.seconds_until_token_refresh_due() == 0.0:
                        login_result = await relogin()
                        await mqtt.replace_token(login_result.token_for_blid(self.blid))
                    else:
                        await mqtt.reconnect(if_generation=current)
                except Exception as exc:  # noqa: BLE001
                    if mqtt.closed:
                        break
                    _LOGGER.warning(
                        "roombapy-prime: MQTT reconnect attempt failed (%s) -- retrying in %.0fs",
                        exc, backoff,
                    )
                    # Ends early if a shadow read or a command rebuilds
                    # the connection meanwhile: the other watchers wait
                    # behind this lock (review finding).
                    await mqtt.wait_for_state_change(backoff)
                    backoff = min(backoff * 2, max_reconnect_backoff)
                    continue

                await self._confirm_subscriptions(topic, backoff, max_reconnect_backoff)
                # WARNING, MATCHING THE DROP THAT PRECEDED IT. This used
                # to be INFO, so at Home Assistant's default level a user
                # saw the failure and never the recovery; @ratpic83 read
                # "nothing further from roombapy_prime" as a dead
                # reconnect and spent two hours on it.
                if reported:
                    _LOGGER.warning(
                        "roombapy-prime: MQTT reconnected, watch resumed for %s", topic
                    )
                return mqtt.generation

        await mqtt.wait_until_open()
        return mqtt.generation

    async def _confirm_subscriptions(
        self, topic: str, backoff: float, max_reconnect_backoff: float
    ) -> None:
        """After a reconnect: are the restored subscriptions acknowledged?

        "RESUMED" HAS TO MEAN ACKNOWLEDGED. @ratpic83 (2026-08-16) caught
        "no SUBACK within 3.0s" and "watch resumed" one millisecond
        apart, twice in a day. An unacknowledged subscription delivers
        nothing and looks exactly like a robot with nothing to say.

        ASK AGAIN BEFORE ACTING ON A DEADLINE. The 3-second wait is a
        snapshot, and a late SUBACK is still a SUBACK. @utkjmitch
        (second household, b7): EVERY reconnect on his instance logs
        `no SUBACK within 3.0s`, on the 55-minute cycle. So this looks a
        second later, reconnects at most _MAX_UNCONFIRMED_RETRIES times,
        and then resumes anyway: a session that delivers without a
        visible SUBACK must not be torn down forever."""
        mqtt = self._mqtt
        for attempt in range(self._MAX_UNCONFIRMED_RETRIES + 1):
            await asyncio.sleep(self._SUBACK_RECHECK_SECONDS)
            generation = mqtt.generation
            unconfirmed = mqtt.resubscribe_still_unconfirmed()
            if not unconfirmed or not mqtt.connected:
                return
            if attempt == self._MAX_UNCONFIRMED_RETRIES:
                _LOGGER.warning(
                    "roombapy-prime: after %d reconnects, %d subscription(s) are still "
                    "unacknowledged (%s) -- resuming anyway. Some sessions deliver "
                    "without a visible SUBACK; if %s stays silent, this is why.",
                    attempt, len(unconfirmed), unconfirmed, topic,
                )
                return
            _LOGGER.warning(
                "roombapy-prime: MQTT reconnected for %s but %d subscription(s) were "
                "never acknowledged (%s) -- reconnecting again in %.0fs (%d of %d)",
                topic, len(unconfirmed), unconfirmed, backoff,
                attempt + 1, self._MAX_UNCONFIRMED_RETRIES,
            )
            # BACK OFF BEFORE TRYING AGAIN: the broker is already
            # refusing or ignoring the subscribe.
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_reconnect_backoff)
            try:
                await mqtt.reconnect(if_generation=generation)
            except Exception as exc:  # noqa: BLE001
                # The watcher's next wait sees the connection down and
                # starts over, with its own backoff.
                _LOGGER.warning(
                    "roombapy-prime: MQTT reconnect attempt failed (%s)", exc
                )
                return

    async def watch_live_map(
        self,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        #: THE APP DOES NOT USE A FIXED INTERVAL. LiveMapKeepAlivePublisher
        #: schedules relative to an expiration/refreshWindowMillis whose
        #: value has not been reconstructed, so this is a stand-in.
        #:
        #: Ten seconds is almost certainly too aggressive: it is 8,640
        #: REST calls per robot per day, running whether or not anything
        #: is moving. scripts/check_request_budget.py in the integration
        #: exists because of a bug of exactly that size, and it cannot
        #: see this one -- it lives here, not in an entity.
        #:
        #: Not changed on a guess. Raising it without knowing the real
        #: expiry would trade a cost problem for a stream that dies
        #: mid-mission, which is worse.
        keep_alive_interval: float = 10.0,
    ) -> AsyncIterator[PositionUpdateMessage | MapUpdateMessage]:
        """CONFIRMED LIVE (this session, jayjay13011, roombapy-prime
        v0.1.11a6): both PositionUpdateMessage and MapUpdateMessage
        deliveries via this exact method were verified against a real
        capture with topic tracking -- previously this whole method had
        never been live-tested successfully. See livemap_topic()'s own
        docstring for the topic confirmation, and
        models/livemap.py's PositionUpdateMessage/MapUpdateMessage for
        the confirmed payload shapes (including operating_modes
        genuinely varying, not a fixed constant -- see that module).

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotwatch_live_map
    """
        if self._irbt_topic_prefix is None:
            msg = (
                "watch_live_map() needs irbt_topic_prefix (from LoginResult) -- "
                "None means: the discovery response didn't contain the "
                "(uncertain-named) field, or the field name was a wrong guess. See "
                "auth.py's LoginResult docstring."
            )
            raise RuntimeError(msg)

        topic = self._mqtt.livemap_topic(self._irbt_topic_prefix)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[PositionUpdateMessage | MapUpdateMessage | Exception] = asyncio.Queue(
            maxsize=queue_maxsize
        )

        def _on_livemap_message(response: ShadowResponse) -> None:
            if not isinstance(response.payload, dict):
                error = ValueError(
                    f"Expected JSON object on livemap topic, got: {response.payload!r}"
                )
                loop.call_soon_threadsafe(_put_with_backpressure, queue, error, topic)
                return
            try:
                parsed = parse_livemap_message_data(response.payload)
            except ValueError as exc:
                loop.call_soon_threadsafe(_put_with_backpressure, queue, exc, topic)
                return
            # The robot tells us how long its stream stays valid. Handing
            # that to the keep-alive turns a fixed poll into a schedule
            # the robot itself sets -- see _keep_alive_loop().
            expires = getattr(parsed, "expires_at", None)
            if expires is not None:
                loop.call_soon_threadsafe(expiry.set_result_if_pending, expires)
            loop.call_soon_threadsafe(_put_with_backpressure, queue, parsed, topic)

        expiry = _StreamExpiry()

        async def _keep_alive_loop() -> None:
            # THE FIRST PING COMES BEFORE THE FIRST SLEEP, and that is a
            # correction rather than a tidy-up.
            #
            # This loop used to sleep first. The subscription was in
            # place, but nothing had asked the robot to publish -- so
            # `watch_live_map()` sat in `await queue.get()` with an empty
            # queue, producing no messages, no exception and no counter
            # movement.
            #
            # If that first ping then FAILS, the except below logs a
            # warning and carries on, so the state is permanent and
            # completely silent: subscribed, waiting, forever. A field
            # capture showed exactly that -- mid-mission, every live-map
            # counter at zero, no error recorded anywhere
            # (@chairstacker).
            #
            # `_ping_failures` is counted so the caller can tell "one
            # hiccup" from "this has never worked". Continuing after a
            # single failure is right; continuing after fifty is how a
            # dead stream stays invisible.
            while True:
                backoff: float | None = None
                try:
                    await self.get_live_map_stream()
                    self._live_map_ping_failures = 0
                except Exception as exc:
                    self._live_map_ping_failures = (
                        getattr(self, "_live_map_ping_failures", 0) + 1
                    )
                    # BACK OFF, AND ESPECIALLY ON 429.
                    #
                    # Retrying at the same cadence used to be the whole
                    # behaviour, and against a rate limit it is the one
                    # thing that cannot work: on a failure no message
                    # arrives, so `expiry` is never set, so `next_delay`
                    # falls back to `keep_alive_interval` -- ten seconds,
                    # forever, at exactly the endpoint that just said
                    # there were too many requests.
                    #
                    # It scales with robot count, which is why it stayed
                    # invisible. @jpatchMC ran two Prime robots on one
                    # account and got HTTP 429 from
                    # /v1/p2maps/livemap?robotId=... twelve times a
                    # minute between them. One robot's map and status
                    # went quiet while the other kept working, commands
                    # still went through on both -- those travel over
                    # MQTT and never touch this endpoint -- and the only
                    # sign was this warning repeating.
                    #
                    # He then reloaded the entries repeatedly to clear
                    # it, which is what locked the account out. The
                    # lockout was downstream of this.
                    status = getattr(exc, "status", None)
                    attempt = min(self._live_map_ping_failures, 6)
                    backoff = min(keep_alive_interval * (2 ** attempt), 300.0)
                    if status == 429:
                        # A rate limit is a statement about the whole
                        # account, not this robot: another watcher is
                        # asking too, and both must give way or neither
                        # recovers. Starting at a minute rather than at
                        # the doubled interval reflects that.
                        backoff = min(max(backoff, 60.0), 300.0)
                    _LOGGER.warning(
                        "watch_live_map(): keep-alive ping failed (%d in a row, "
                        "HTTP %s), retrying in %.0fs -- the robot only publishes "
                        "while these pings succeed, so a run of failures means a "
                        "silent, empty stream rather than a slow one",
                        self._live_map_ping_failures, status or "?", backoff,
                        exc_info=True,
                    )
                # PACED BY THE ROBOT, not by a constant.
                #
                # Each position message carries `update_expire_ts`: when
                # the stream lapses unless asked again. The app pings a
                # refresh window before that, defaulting to ten seconds
                # -- a SAFETY MARGIN, not an interval.
                #
                # This loop polled at a flat ten seconds, which is the
                # same number meaning something else entirely: 8,640
                # REST calls per robot per day, running whether or not
                # anything moves. With a one-minute validity window the
                # same coverage costs about sixty.
                #
                # `keep_alive_interval` stays the fallback for as long as
                # the robot has not said anything, and for robots that
                # never send the field. Pacing on a guessed expiry would
                # risk the stream lapsing mid-mission, and this project
                # has just spent a week on one silent stream.
                await asyncio.sleep(
                    backoff if backoff is not None
                    else expiry.next_delay(keep_alive_interval)
                )

        await self._mqtt.subscribe(topic, _on_livemap_message)
        generation = self._mqtt.generation
        keep_alive_task = asyncio.ensure_future(_keep_alive_loop())
        try:
            while True:
                # WATCH FOR THE DROP, NOT JUST FOR MESSAGES.
                #
                # This used to be a bare `await queue.get()`, and after
                # the first reconnect the live map was permanently dead: an
                # empty queue, and a keep-alive still reporting success
                # because the REST ping is a different transport entirely.
                # That is the shape @chairstacker reported -- every
                # live-map counter at zero mid-mission with no error
                # anywhere.
                #
                # THE SAME RESUME AS EVERY OTHER WATCHER since 0.5.0b1.
                # This used to subscribe again after a drop and let the
                # client reconnect lazily -- a second reconnect path next
                # to _watch_topic()'s, without its lock, its relogin or
                # its backoff, and one that also reconnected after
                # robot.disconnect() (review finding). A reconnect
                # restores this topic like every other persistent one.
                get_task = asyncio.ensure_future(queue.get())
                end_task = asyncio.ensure_future(self._mqtt.wait_for_disconnect(generation))
                done = await _first_of(get_task, end_task)

                if get_task not in done:
                    generation = await self._resume_after_end(
                        generation, topic, max_reconnect_backoff=60.0
                    )
                    continue

                item = get_task.result()
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            keep_alive_task.cancel()
            await asyncio.wait({keep_alive_task})
            await self._mqtt.unsubscribe(topic, _on_livemap_message)
