"""Public robot class (analogous to roombapy.roomba.Roomba).

STATUS: Draft. Connects auth.LoginResult, mqtt_client.PrimeMqttClient
and rest_client.PrimeRestClient. NOT tested against a real V4 account
-- the individual building blocks are confirmed to varying degrees
(see their respective docstrings), this class itself is pure wiring,
untested as a whole.

Also part of this draft (see watch_state()/watch_live_map() below):
continuous dispatch loops for shadow deltas and live-map/-position
messages -- previously deliberately left out (see
docs/internal/ROOMBAPY_COMPARISON.md section 3). Bridges from paho's background
thread (drives mqtt_client.py's subscribe() callbacks) into the
asyncio world: one asyncio.Queue PER watch_*() call, filled via
loop.call_soon_threadsafe(). No lock needed -- each watcher gets its
own queue, mqtt_client.py's subscribe()/unsubscribe() are already
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

Still NOT part of this draft:
  - No backpressure handling -- the internal queue is unbounded. A
    consumer that falls behind lets it grow without limit.
  - replace_token() (see mqtt_client.py) is NOT safe against a
    concurrently running get_shadow()/update_shadow() call -- a known,
    accepted limitation, no lock in place.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from .auth import LoginResult
from .mqtt_client import PrimeMqttClient, ShadowResponse
from .rest_client import PrimeRestClient
from .models import (
    DNDStatusResponse,
    FavoriteV1,
    HouseholdSchedule,
    LiveMapStreamInit,
    MapEditCommand,
    MapEditCommandV1,
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


class _AlreadyReconnected(Exception):
    """Internal signal: another watcher rebuilt the shared connection
    while this one was waiting for the reconnect lock, so there is
    nothing left to do but resume."""

Relogin = Callable[[], Awaitable[LoginResult]]

DEFAULT_WATCH_QUEUE_MAXSIZE = 100
DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS = 60.0
# Chosen arbitrarily (not an empirical value) -- large enough to
# absorb brief processing delays on the caller's side, small enough to
# not tie up unbounded memory if the consumer permanently falls behind.


def _put_with_backpressure(queue: asyncio.Queue[object], item: object, topic: str) -> None:
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
    anything beyond exposing it for diagnostics."""

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
        self._reconnect_generation = 0
        self._rest = rest_client
        self._relogin = relogin
        self._irbt_topic_prefix = irbt_topic_prefix
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
        """Blocking paho connection setup in a worker thread, so the
        rest of the app can stay async (see mqtt_client.py -- the
        client itself was deliberately not rebuilt). Also starts the
        refresh loop in the background, if relogin was provided (see
        class docstring)."""
        await asyncio.to_thread(self._mqtt.connect, timeout)
        if self._relogin is not None:
            self._refresh_task = asyncio.ensure_future(self._refresh_loop())

    async def disconnect(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None
        await asyncio.to_thread(self._mqtt.disconnect)

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
            try:
                login_result = await self._relogin()
                new_token = login_result.token_for_blid(self.blid)
                await asyncio.to_thread(self._mqtt.replace_token, new_token)
            except Exception:  # noqa: BLE001
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
        return await asyncio.to_thread(self._mqtt.get_shadow, None, timeout)

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
        return await asyncio.to_thread(self._mqtt.get_shadow, "rw-settings", timeout)

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
        return await asyncio.to_thread(self._mqtt.get_shadow, name, timeout)

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
        app. Writing schedHold here is evidently not the mechanism the
        app itself uses to pause a schedule.

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
        return await asyncio.to_thread(self._mqtt.update_shadow, {key: value}, "rw-settings", timeout)

    async def trigger_echo_via_shadow(self, value: object = True, timeout: float = 8.0) -> ShadowResponse:
        """DISPROVEN (this session, chairstacker, real device test) --
        writing to "rw-constatus"'s "echo" field does NOT trigger the
        "find my robot" chime. Kept for what it does confirm (see
        below), not as a working locate mechanism.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robottrigger_echo_via_shadow
    """
        return await asyncio.to_thread(self._mqtt.update_shadow, {"echo": value}, "rw-constatus", timeout)

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
        return await asyncio.to_thread(
            self._mqtt.update_shadow, command.to_shadow_desired(), None, timeout
        )

    async def send_simple_command(self, command: str, initiator: str = "localApp") -> bool:
        """NEW (session 39) -- the corrected mission-control path,
        replacing send_mission_command() for basic commands. See
        mqtt_client.py's cmd_topic()/publish_cmd() docstrings for the
        full evidence trail (this library's own native disassembly of
        libcorebase.so independently corroborated by a third-party,
        unaffiliated GitHub project that reports this exact path
        working against a real device).

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotsend_simple_command
    """
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_simple_command() needs irbt_topic_prefix (from LoginResult) -- "
                "missing here, so the correct topic can't be built."
            )
        return await asyncio.to_thread(self._mqtt.publish_cmd, self._irbt_topic_prefix, command, initiator)

    async def send_routine_command_via_cmd_topic(self, command: RoutineCommand) -> bool:
        """EXPERIMENTAL, UNCONFIRMED (session 46) -- a well-reasoned
        hypothesis for the region-aware case send_simple_command()
        explicitly can't cover, NOT a confirmed working path. Read the
        linked evidence trail before using it against a real device.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#prime_robotsend_routine_command_via_cmd_topic
    """
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_routine_command_via_cmd_topic() needs irbt_topic_prefix (from LoginResult) "
                "-- missing here, so the correct topic can't be built."
            )
        return await asyncio.to_thread(self._mqtt.publish_cmd_payload, self._irbt_topic_prefix, command.to_json())

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
        await asyncio.to_thread(self._mqtt.publish_cmd_payload, self._irbt_topic_prefix, payload)

    # --- REST-based p2maps operations (already natively async) -------

    async def get_active_map_versions(self) -> list[dict]:
        """NEW (July 11, eleventh session) -- was missing as a wrapper
        until now, even though rest_client.py's version had already
        existed for a while."""
        return await self._rest.get_active_map_versions(self.blid)

    async def get_map_metadata(self, p2map_id: str) -> P2MapData:
        """UPDATED (session 51) -- now returns a parsed P2MapData, see
        rest_client.py::get_map_metadata()'s docstring."""
        return await self._rest.get_map_metadata(p2map_id)

    async def set_map_name(self, p2map_id: str, name: str) -> dict:
        return await self._rest.set_map_name(p2map_id, name)

    async def set_map_orientation(self, p2map_id: str, orientation_rad: float) -> dict:
        return await self._rest.set_map_orientation(p2map_id, orientation_rad)

    async def delete_map(self, p2map_id: str) -> dict:
        """NEW (thirteenth session) -- was missing as a wrapper despite
        a rest_client.py version having existed for a while (found
        during a systematic review)."""
        return await self._rest.delete_map(p2map_id)

    async def get_map_geojson_link(self, map_id: str, map_version: str) -> dict:
        """NEW (thirteenth session) -- was missing as a wrapper. Returns
        the presigned download URL for download_map_bundle() (see
        there). CORRECTED (session 48, this docstring was outdated):
        response shape IS confirmed -- the URL lives under the
        "map_url" key (P2MapURL$$serializer's own <clinit>), not an
        unconfirmed guess among candidate keys the way this docstring
        used to say. See rest_client.py's own get_map_geojson_link()
        docstring for the full evidence trail."""
        return await self._rest.get_map_geojson_link(map_id, map_version)

    async def download_map_bundle(self, url: str) -> bytes:
        """NEW (thirteenth session) -- was missing as a wrapper, even
        though the diagnostics script and parse_map_bundle() depend on
        it. Deliberately WITHOUT SigV4 signing -- see rest_client.py's
        docstring."""
        return await self._rest.download_map_bundle(url)

    async def edit_map(
        self, p2map_id: str, command: MapEditCommandV1,
        response_type: str | None = "link",
    ) -> dict:
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

    async def edit_map_v2(self, p2map_id: str, command: MapEditCommand) -> dict:
        """The V2 path never called by the app itself -- see
        edit_map()'s docstring and rest_client.py::edit_map_v2()."""
        return await self._rest.edit_map_v2(p2map_id, command)

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

    async def get_favorites(self) -> list[FavoriteV1]:
        """See rest_client.py::get_favorites() -- the only one of the
        five favorite endpoints whose HTTP method AND response shape
        are both fully confirmed."""
        return await self._rest.get_favorites()

    async def get_favorites_raw(self) -> list[dict]:
        """See rest_client.py::get_favorites_raw() -- diagnostic
        round-trip fidelity check, not part of the normal path."""
        return await self._rest.get_favorites_raw()

    async def create_favorite(self, favorite: FavoriteV1) -> dict:
        """See rest_client.py::create_favorite() -- HTTP method
        (POST) confirmed (eighth session)."""
        return await self._rest.create_favorite(favorite)

    async def update_favorite(self, favorite_id: str, favorite: FavoriteV1) -> dict:
        """See rest_client.py::update_favorite() -- HTTP method
        (PUT) confirmed (eighth session)."""
        return await self._rest.update_favorite(favorite_id, favorite)

    async def delete_favorite(self, favorite_id: str) -> dict:
        return await self._rest.delete_favorite(favorite_id)

    async def order_favorite(
        self,
        favorite_id: str,
        *,
        insert_at: int | None = None,
        insert_before: str | None = None,
        insert_after: str | None = None,
    ) -> dict:
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
    ) -> dict:
        """See rest_client.py::get_mission_history() -- fully
        confirmed from FetchMissionHistoryRequest.java."""
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

    async def create_schedules(self, household_id: str, schedules: list[ScheduleOptions]) -> dict:
        """HTTP method (POST) confirmed (eighth session), see
        rest_client.py::create_schedules()."""
        return await self._rest.create_schedules(household_id, schedules)

    async def update_schedules(
        self, household_id: str, household_schedule_id: str, schedules: list[HouseholdSchedule]
    ) -> dict:
        """HTTP method (PUT) confirmed (eighth session)."""
        return await self._rest.update_schedules(household_id, household_schedule_id, schedules)

    async def delete_schedule(self, household_id: str, household_schedule_id: str) -> dict:
        return await self._rest.delete_schedule(household_id, household_schedule_id)

    async def get_user_households(self) -> dict:
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

    async def set_dnd_settings(self, household_id: str, settings: dict) -> dict:
        return await self._rest.set_dnd_settings(household_id, settings)

    async def get_cleaning_profiles(self, asset_id: str, p2map_id: str | None = None) -> dict:
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

    async def reset_robot_parts(self) -> dict:
        """NEW (session 15) -- see rest_client.py::reset_robot_parts()."""
        return await self._rest.reset_robot_parts(self.blid)

    async def get_serial_number_data(self) -> RobotSerialInfo:
        """NEW (session 15) -- see rest_client.py::get_serial_number_data().
        UPDATED (session 53) -- now returns a parsed RobotSerialInfo."""
        return await self._rest.get_serial_number_data(self.blid)

    async def poll_echo_value(self) -> dict:
        """NEW (session 16) -- "find my robot" feature, see
        rest_client.py::poll_echo_value()."""
        return await self._rest.poll_echo_value(self.blid)

    async def get_time_estimates(self) -> dict:
        """Per-room time estimates for this robot.

        Takes no arguments now: the request body is `{"robot_id": blid}`
        and this object already knows its blid. The previous signature
        took a raw dict because the body shape was unknown -- see
        rest_client.py::get_time_estimates() for how it was traced.
        """
        return await self._rest.get_time_estimates(self.blid)

    async def reset_robot(self) -> dict:
        """NEW (session 16) -- WARNING: likely a consequential action,
        see rest_client.py::reset_robot()."""
        return await self._rest.reset_robot(self.blid)

    async def get_notifications(self, app_version: str = "2.2.4") -> dict:
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
    ) -> AsyncIterator[ShadowResponse]:
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

        await asyncio.to_thread(self._mqtt.subscribe, topic, _on_message)
        backoff = 1.0
        try:
            while True:
                get_task = asyncio.ensure_future(queue.get())
                disconnect_task = asyncio.ensure_future(self._mqtt.wait_for_disconnect())
                tasks = {get_task, disconnect_task}
                try:
                    done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    # Unconditional cleanup, regardless of WHY we got here --
                    # one task completing normally, or this whole generator
                    # being cancelled from outside (agen.aclose()/task.cancel()
                    # while both tasks are still pending). Without this, the
                    # "loser" of the race (or both, on outer cancellation)
                    # would be left running as an orphaned task.
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    for t in tasks:
                        with contextlib.suppress(BaseException):
                            await t

                if get_task in done:
                    backoff = 1.0  # a live message means the connection is healthy
                    yield get_task.result()
                    continue

                # Connection dropped -- reconnect with exponential backoff,
                # unbounded retries.
                reason = disconnect_task.result()
                _LOGGER.warning(
                    "roombapy-prime: MQTT connection dropped (%s) while watching %s -- reconnecting",
                    reason, topic,
                )
                if self._reconnect_lock is None:
                    self._reconnect_lock = asyncio.Lock()
                generation_seen = self._reconnect_generation

                while True:
                    # If another watcher already rebuilt the shared
                    # connection while we were noticing the drop, resume on
                    # theirs instead of tearing it down again -- that is
                    # exactly the ping-pong this guards against.
                    if self._reconnect_generation != generation_seen:
                        _LOGGER.info(
                            "roombapy-prime: another watcher already reconnected -- "
                            "resuming %s without a second reconnect", topic,
                        )
                        backoff = 1.0
                        break
                    try:
                        # CORRECTED (this session, prompted by a real field
                        # report: an integration stuck permanently
                        # reconnecting-but-never-succeeding, surviving even
                        # multiple full restarts of the calling application).
                        # reconnect() on its own is "same-token" by design
                        # (see its own docstring) -- it does NOT check
                        # whether that token is still valid. The proactive
                        # _refresh_loop() background task normally keeps the
                        # token fresh well before expiry, but if a disconnect
                        # happens to land after the token has already expired
                        # (or that task died for any reason -- an exception,
                        # a race with disconnect()/reconnect() happening
                        # concurrently), every subsequent reconnect() attempt
                        # here would keep reusing the same now-permanently-
                        # invalid token, retrying forever at an
                        # ever-increasing backoff but never actually able to
                        # succeed -- exactly matching a "stuck, restart
                        # doesn't help" symptom IF the restart itself
                        # somehow reused stale state (this specific failure
                        # mode is defended against below regardless of
                        # whether that's the exact mechanism in any given
                        # report). See the follow-up correction right below
                        # for exactly when a fresh token gets fetched.
                        #
                        # CORRECTED AGAIN (this session, self-review): an
                        # earlier version of this fix relogged in on EVERY
                        # reconnect attempt whenever relogin was configured
                        # at all -- including ordinary transient blips where
                        # the token is still perfectly valid. That trades a
                        # fast, simple MQTT reconnect for a full Gigya+
                        # iRobot auth round-trip on every single disconnect,
                        # adding real latency and a genuinely new failure
                        # mode (if the login backend itself is slow, rate-
                        # limiting, or briefly unavailable) to the COMMON
                        # case, not just the rare one this was meant to fix.
                        # Narrowed: only relogin when the token is ACTUALLY
                        # at or near expiry (checked the same way
                        # _refresh_loop() itself decides this) -- an
                        # ordinary reconnect with a still-valid token uses
                        # the fast, same-token path exactly as it always did
                        # before either fix existed.
                        needs_relogin = (
                            self._relogin is not None
                            and self._mqtt.seconds_until_token_refresh_due() == 0.0
                        )
                        if needs_relogin:
                            login_result = await self._relogin()
                            new_token = login_result.token_for_blid(self.blid)
                            await asyncio.to_thread(self._mqtt.replace_token, new_token)
                        else:
                            async with self._reconnect_lock:
                                # Second check under the lock: another
                                # watcher may have finished reconnecting
                                # while we waited for it.
                                if self._reconnect_generation != generation_seen:
                                    raise _AlreadyReconnected
                                await asyncio.to_thread(self._mqtt.reconnect)
                    except _AlreadyReconnected:
                        _LOGGER.info(
                            "roombapy-prime: another watcher reconnected first -- resuming %s", topic
                        )
                        backoff = 1.0
                        break
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning(
                            "roombapy-prime: MQTT reconnect attempt failed (%s) -- retrying in %.0fs",
                            exc, backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, max_reconnect_backoff)
                    else:
                        # Announce the new connection so any other watcher
                        # that noticed the same drop resumes on it rather
                        # than tearing it down to build its own.
                        self._reconnect_generation += 1
                        _LOGGER.info("roombapy-prime: MQTT reconnected, watch resumed for %s", topic)
                        backoff = 1.0
                        break
        finally:
            await asyncio.to_thread(self._mqtt.unsubscribe, topic, _on_message)

    async def watch_live_map(
        self,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
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
            loop.call_soon_threadsafe(_put_with_backpressure, queue, parsed, topic)

        async def _keep_alive_loop() -> None:
            while True:
                await asyncio.sleep(keep_alive_interval)
                try:
                    await self.get_live_map_stream()
                except Exception:
                    _LOGGER.warning("watch_live_map(): keep-alive ping failed, continuing anyway", exc_info=True)

        await asyncio.to_thread(self._mqtt.subscribe, topic, _on_livemap_message)
        keep_alive_task = asyncio.ensure_future(_keep_alive_loop())
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            keep_alive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keep_alive_task
            await asyncio.to_thread(self._mqtt.unsubscribe, topic, _on_livemap_message)
