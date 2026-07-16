"""Public robot class (analogous to roombapy.roomba.Roomba).

STATUS: Draft. Connects auth.LoginResult, mqtt_client.PrimeMqttClient
and rest_client.PrimeRestClient. NOT tested against a real V4 account
-- the individual building blocks are confirmed to varying degrees
(see their respective docstrings), this class itself is pure wiring,
untested as a whole.

Also part of this draft (see watch_state()/watch_live_map() below):
continuous dispatch loops for shadow deltas and live-map/-position
messages -- previously deliberately left out (see
docs/ROOMBAPY_COMPARISON.md section 3). Bridges from paho's background
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

Relogin = Callable[[], Awaitable[LoginResult]]

DEFAULT_WATCH_QUEUE_MAXSIZE = 100
# Chosen arbitrarily (not an empirical value) -- large enough to
# absorb brief processing delays on the caller's side, small enough to
# not tie up unbounded memory if the consumer permanently falls behind.


def _put_with_backpressure(queue: "asyncio.Queue[object]", item: object, topic: str) -> None:
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
        irbt_topic_prefix: str | None = None,
        deployment: dict[str, Any] | None = None,
    ) -> None:
        self.blid = blid
        self._mqtt = mqtt_client
        self._rest = rest_client
        self._relogin = relogin
        self._irbt_topic_prefix = irbt_topic_prefix
        self.deployment = deployment or {}
        self._refresh_task: asyncio.Task[None] | None = None

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
        known limitation, not a silent bug."""
        while True:
            wait_seconds = self._mqtt.seconds_until_token_refresh_due()
            if wait_seconds is None:
                return
            await asyncio.sleep(wait_seconds)
            assert self._relogin is not None  # invariant: only started if set
            login_result = await self._relogin()
            new_token = login_result.token_for_blid(self.blid)
            await asyncio.to_thread(self._mqtt.replace_token, new_token)

    # --- Shadow-based operations (via mqtt_client.py) -----------------

    async def get_state(self, timeout: float = 8.0) -> ShadowResponse:
        """Classic/unnamed shadow -- identity, capabilities, current
        mission status. Responds reliably on both tiers tested so
        far (EPHEMERAL + SMART)."""
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

    async def set_setting(self, key: str, value: object, timeout: float = 8.0) -> ShadowResponse:
        """Writes to the "rw-settings" shadow. Only meaningful on
        SMART tier -- on EPHEMERAL, presumably the same timeout as
        get_settings(), never tested."""
        return await asyncio.to_thread(self._mqtt.update_shadow, {key: value}, "rw-settings", timeout)

    async def send_mission_command(self, command: RoutineCommand, timeout: float = 8.0) -> ShadowResponse:
        """STRONGLY SUSPECTED WRONG (session 39) -- kept for the
        region-based/richer use case (RoutineCommand.regions/params),
        which remains unconfirmed by any source. For basic mission
        control (start/pause/stop/resume/dock/etc.), use
        send_simple_command() instead -- see its docstring for the full
        story of why this method is now believed incorrect.

        Originally CONFIRMED (session 15) via
        base_roomba_config.json's "Control" commandId entry:

            {"commandId": "Control", "topic": "cmd", "namedShadow": ""}

        MISREADING CORRECTED (session 39): "namedShadow": "" was read
        as "classic (unnamed) shadow, therefore send via
        $aws/things/{blid}/shadow/update" -- but cross-referencing the
        "topic" field across ALL 77 commandIds in the same file (not
        just this one entry in isolation) shows "topic" is itself a
        discriminator with (at least) three distinct categories:
        "shadow" (2 commandIds, incl. GetThingShadow -- confirmed live
        as get_state()'s classic shadow GET), "delta" (57 commandIds,
        all settings/schedule-style writes -- confirmed live as
        update_shadow()'s desired-state mechanism), and "cmd" (4
        commandIds: Control, AssetControlCommand, ResetRobotCommand,
        StartMatterCommissioning). "cmd" being its own category,
        distinct from both "shadow" and "delta", was the clue that got
        missed the first time -- mission commands were never meant to
        go through the shadow /update mechanism at all. "namedShadow":
        "" for a "cmd"-category entry doesn't mean "classic shadow";
        it's presumably just not applicable to this category.

        A live test (chairstacker, session 39) confirms this
        practically: every attempt via this method (update_shadow())
        timed out with ZERO response -- not even /rejected, which is
        consistent with publishing to a topic the AWS IoT shadow
        service doesn't recognize as a shadow topic at all, not a
        payload or permission problem on an otherwise-valid one."""
        return await asyncio.to_thread(
            self._mqtt.update_shadow, command.to_shadow_desired(), None, timeout
        )

    async def send_simple_command(self, command: str, initiator: str = "localApp") -> None:
        """NEW (session 39) -- the corrected mission-control path,
        replacing send_mission_command() for basic commands. See
        mqtt_client.py's cmd_topic()/publish_cmd() docstrings for the
        full evidence trail (this library's own native disassembly of
        libcorebase.so independently corroborated by a third-party,
        unaffiliated GitHub project that reports this exact path
        working against a real device).

        `command` is a plain string, not MissionCommandType -- the
        confirmed verb set from that third-party project is narrower
        than this library's own 30-value enum (start, pause, stop,
        resume, dock, find, evac, reset, StartOnDemandOta) -- pass
        MissionCommandType.START.value etc. if you want enum safety, or
        a plain string for anything not yet in the enum.

        Needs irbt_topic_prefix (see __init__/auth.py's LoginResult),
        same requirement as watch_live_map() -- raises RuntimeError
        immediately if missing, rather than silently publishing to a
        malformed topic.

        NOT region-aware -- there is no known way to specify
        rooms/zones/CommandParams through this simple payload shape.
        For that, RoutineCommand/send_mission_command() may still be
        the right (if unconfirmed) tool -- or an entirely different,
        not-yet-discovered mechanism may be needed. Fire-and-forget, no
        response wait -- see publish_cmd()'s docstring for why. NOT YET
        live-tested by this library itself."""
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_simple_command() needs irbt_topic_prefix (from LoginResult) -- "
                "missing here, so the correct topic can't be built."
            )
        await asyncio.to_thread(self._mqtt.publish_cmd, self._irbt_topic_prefix, command, initiator)

    async def send_routine_command_via_cmd_topic(self, command: RoutineCommand) -> None:
        """EXPERIMENTAL, UNCONFIRMED (session 46) -- a well-reasoned
        hypothesis for the region-aware case send_simple_command()
        explicitly can't cover, NOT a confirmed working path. Read
        this whole docstring before using it against a real device.

        THE HYPOTHESIS: send_simple_command()'s confirmed-working
        payload ({"command": str, "time": int, "initiator": str}) and
        RoutineCommand.to_json()'s own, independently-confirmed field
        mapping (see models/mission_control.py's RoutineCommand docstring, confirmed
        via @SerialName annotations in the decompiled source) share
        TWO exact key names: "command" (from RoutineCommand.type) and
        "initiator" (RoutineCommand's own confirmed field). This is
        not likely to be coincidence -- it suggests cmd_topic() may
        accept RoutineCommand's fuller structure (region_id/params/
        p2map_id/favorite_id and the rest), with "time" added on top,
        rather than being a fundamentally different, unrelated schema
        that happens to share two names.

        WHAT THIS METHOD DOES: publishes `command.to_json()` merged
        with a "time" field to cmd_topic(), via
        mqtt_client.py's publish_cmd_payload(). Nothing more.

        WHY THIS HAS NOT BEEN LIVE-TESTED: unlike the original
        transport question (where a wrong guess just produced silence,
        confirmed safe), a wrong guess HERE could mean a real device
        accepts a malformed but plausible-looking command and behaves
        unpredictably -- not zero risk, unlike the topic-discovery
        problem this hypothesis descends from. If experimenting with
        this, favor a `favorite_id`-based RoutineCommand (referencing
        an existing, already-defined favorite/routine the person set
        up themselves in the real app) over hand-built `regions` --
        that way the actual room/zone definitions come from something
        already confirmed correct by the app itself, not from this
        library's own unconfirmed region-construction logic.

        Same requirements/behavior as send_simple_command(): needs
        irbt_topic_prefix, fire-and-forget (no response wait, see
        publish_cmd()'s docstring for why)."""
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_routine_command_via_cmd_topic() needs irbt_topic_prefix (from LoginResult) "
                "-- missing here, so the correct topic can't be built."
            )
        await asyncio.to_thread(self._mqtt.publish_cmd_payload, self._irbt_topic_prefix, command.to_json())

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
        there). Response shape/URL key name unconfirmed -- see
        rest_client.py's docstring."""
        return await self._rest.get_map_geojson_link(map_id, map_version)

    async def download_map_bundle(self, url: str) -> bytes:
        """NEW (thirteenth session) -- was missing as a wrapper, even
        though the diagnostics script and parse_map_bundle() depend on
        it. Deliberately WITHOUT SigV4 signing -- see rest_client.py's
        docstring."""
        return await self._rest.download_map_bundle(url)

    async def edit_map(self, p2map_id: str, command: MapEditCommandV1) -> dict:
        """NEW (July 11, fourth session) -- command is now one of the
        9 V1 command dataclasses from models/map_editing.py (RenameRoomV1,
        SplitRoomV1, MergeRoomsV1, ...) -- the actually active path
        (see rest_client.py's docstring, PRIME_APP_GAP_ANALYSIS). For
        the unused V2 path see edit_map_v2()."""
        return await self._rest.edit_map(p2map_id, command)

    async def edit_map_v2(self, p2map_id: str, command: MapEditCommand) -> dict:
        """The V2 path never called by the app itself -- see
        edit_map()'s docstring and rest_client.py::edit_map_v2()."""
        return await self._rest.edit_map_v2(p2map_id, command)

    async def get_live_map_stream(self) -> LiveMapStreamInit:
        """CORRECTED UNDERSTANDING (July 11, see
        docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md point B1): this REST
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

    async def get_time_estimates(self, body: dict) -> dict:
        """NEW (session 16) -- see rest_client.py::get_time_estimates()
        for the note on the unconfirmed body shape."""
        return await self._rest.get_time_estimates(body)

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
        self, named: str | None = None, *, queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE
    ) -> AsyncIterator[ShadowResponse]:
        """Delivers every shadow delta as soon as it arrives -- until
        the caller breaks the iteration (break/return from an
        `async for`, or .aclose()).

        named=None -> classic shadow delta (works on both tiers tested
        so far). named="rw-settings" -> named shadow delta, expected to
        only work on SMART tier -- on EPHEMERAL, this iterator then
        presumably never delivers anything (no error, just silence,
        analogous to get_shadow()'s timeout behavior -- but there's no
        timeout here, since "wait for the next change" is the whole
        point).

        queue_maxsize: bounds the internal buffer (see
        DEFAULT_WATCH_QUEUE_MAXSIZE). When the buffer is full, the
        OLDEST entry is dropped (not the newest) -- a lagging consumer
        this way gets the most current state, not the longest queue.
        Every drop is logged as a WARNING.

        IMPORTANT: the delta topic itself (.../update/delta) is part of
        AWS IoT's standard shadow behavior (delivers a message
        immediately upon subscribing if desired/reported differ, then
        on every subsequent change) -- this standard semantic is
        assumed here, not specifically verified for Classic/Prime.
        """
        topic = self._mqtt.shadow_topic("update/delta", named=named)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[ShadowResponse] = asyncio.Queue(maxsize=queue_maxsize)

        def _on_delta(response: ShadowResponse) -> None:
            loop.call_soon_threadsafe(_put_with_backpressure, queue, response, topic)

        await asyncio.to_thread(self._mqtt.subscribe, topic, _on_delta)
        try:
            while True:
                yield await queue.get()
        finally:
            await asyncio.to_thread(self._mqtt.unsubscribe, topic, _on_delta)

    async def watch_live_map(
        self,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        keep_alive_interval: float = 10.0,
    ) -> AsyncIterator[PositionUpdateMessage | MapUpdateMessage]:
        """CORRECTED (July 11, see
        docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md point B1) -- an
        earlier version called get_live_map_stream() and subscribed to
        the topic returned in it. That was a misunderstanding: in the
        real app (P2MapAPIFetching.observeLiveMap()), a FIXED topic is
        subscribed to (see mqtt_client.py's livemap_topic()), and
        get_live_map_stream() only keeps running as a periodic
        keep-alive in the background, for as long as it's being
        watched.

        Needs irbt_topic_prefix (see __init__/auth.py's LoginResult)
        -- if that's None (field name from the discovery response not
        confirmed, see there), this method immediately raises a
        RuntimeError, instead of silently waiting on an incorrectly
        constructed topic.

        keep_alive_interval: how often the keep-alive ping is sent
        while watching. The real app uses a more complex scheme
        (timer relative to an expiration/refreshWindowMillis, see
        LiveMapKeepAlivePublisher) -- deliberately simplified here to
        a fixed interval, since the original's exact lookup/trigger
        logic wasn't fully reconstructed. If a single keep-alive ping
        fails, this is logged as a WARNING, but watching continues (a
        ping failure shouldn't abort the whole watcher).

        queue_maxsize: see watch_state() -- same drop-oldest
        backpressure policy. IMPORTANT LIMITATION here: errors (see
        next paragraph) go through the same queue as normal messages
        and are therefore NOT exempt from the drop-oldest policy -- an
        error could theoretically be dropped if the queue happens to
        be full when it arrives. An accepted limitation for this
        draft, no special case built in for errors.

        Messages of unknown shape (neither pos_update nor map_update,
        see parse_livemap_message_data) are NOT silently skipped -- the
        error propagates through the generator, the caller sees it on
        the next `async for` step. This is a deliberate choice: an
        unknown message format on a channel that's never been tested
        live is something that should stand out, not something to
        silently discard.
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
