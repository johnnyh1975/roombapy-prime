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

        WHY THIS MATTERS (context from that analysis): the real app
        subscribes to a wildcard covering every named shadow
        ("/things/{blid}/shadow/name/+/get/accepted" and the "update/
        accepted" sibling), and five named shadows are known to exist
        from that pattern -- but this library has only ever queried two
        of them (classic + "rw-settings"). The other three --
        "rw-constatus", "rw-schedule", "rw-software" -- have never been
        queried. "rw-constatus" is a strong candidate for where
        battery/charging status might live (plausibly short for
        "connection status"), given RobotStatusV2's own confirmed value
        is derived in the native app from FOUR combined streams
        (MissionData/SettingsData/AssetNetworkData/OTAStatusData) via
        rxcpp::combine_latest, not received as one ready-made field --
        meaning it's very plausibly assembled from more shadows than
        the two already queried. A specific EARLIER mistake, worth
        remembering: "rw-constatus" was previously written off because
        the app's own command config only lists a write-side
        SetEchoCommand (read: false) for it -- but that config
        describes COMMANDS, not SUBSCRIPTIONS; the wildcard subscribes
        to a named shadow regardless of whether any explicit read
        command exists for it. That distinction is exactly what this
        method exists to let someone check.

        Purely a read -- no different in risk from get_state()/
        get_settings(), which already do the same underlying MQTT
        request/response exchange against a different name."""
        return await asyncio.to_thread(self._mqtt.get_shadow, name, timeout)

    async def set_setting(self, key: str, value: object, timeout: float = 8.0) -> ShadowResponse:
        """Writes to the "rw-settings" shadow. Only meaningful on
        SMART tier -- on EPHEMERAL, presumably the same timeout as
        get_settings(), never tested.

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
        trigger_echo_via_shadow()'s own docstring). A successful
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

        Originally a hypothesis prompted by a real bug report: a field
        tester found ha_roomba_plus's existing locate action --
        poll_echo_value(), a REST POST to /v1/robots/{blid}/echo --
        does NOT actually make the robot chime, even though the same
        action works from the real app. ConnectionStatusShadow's
        "echo" field was noted to plausibly correspond to the app's
        own "SetEchoCommand" -- the exact command name the "find my
        robot" feature is built on, per the app's command config --
        making a shadow write, rather than a REST call, seem like a
        promising alternative mechanism.

        ACTUAL TEST RESULT: calling this with value=True produced a
        genuine, accepted shadow write -- confirmed by a real
        update/delta response (ShadowResponse with a real version
        number, "state": {"echo": True}). The write mechanism itself
        works correctly. But the robot did NOT chime, and "locate"
        from the real app worked fine on the same device immediately
        after -- confirming the ROBOT's own locate feature is not
        broken, only this particular guess at how to trigger it
        remotely. A delta response specifically (not just
        update/accepted) means a listening device would normally see
        this as "something changed that I should act on" -- yet
        nothing observable happened, suggesting either the robot
        doesn't actually watch this specific field for this purpose,
        or "SetEchoCommand" refers to something else entirely (e.g. a
        connectivity heartbeat/ping, consistent with rw-constatus
        otherwise being about network connection status, not
        chime-related at all).

        STILL UNRESOLVED: the actual "find my robot" trigger mechanism
        for Prime/V4 robots. Kept as a library method since the
        underlying write mechanism (arbitrary rw-constatus field
        writes) may still be useful for other, unrelated
        investigation, not because this specific use case works."""
        return await asyncio.to_thread(self._mqtt.update_shadow, {"echo": value}, "rw-constatus", timeout)

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
        confirmed-LIVE verb set (start, pause, stop, resume, dock,
        find) is narrower than this library's own 30-value enum (evac,
        reset, StartOnDemandOta, and more) -- pass MissionCommandType
        values for enum safety, or a plain string for anything not yet
        in the enum.

        CONFIRMED WORKING (jayjay, real device test): sending "find"
        produced a genuine, audible chime with no robot movement --
        exactly the expected find-my-robot behavior. This is the
        RESOLUTION of this whole project's locate-mechanism search --
        the two earlier attempts (a REST endpoint, a shadow write; see
        trigger_echo_via_shadow()'s own docstring) were both tried
        live and confirmed NOT working; this third, distinct transport
        (send_simple_command's own cmd-topic channel, not another
        shadow write) is the one that actually works.

        A separate native-analysis track had already traced the real
        app's locate button through
        MissionUIServiceCommand.FindLocateRobotRunAction to a
        CommandType enum value named FIND (Kotlin constant name,
        uppercase, from liblegacyCore.so's own string table) --
        MissionCommandType.FIND above IS this exact same enum
        (com.irobot.data.missioncommand.datamodels.CommandType), and
        its confirmed @SerialName wire value is the lowercase "find"
        already listed -- that reasoning is what predicted this result
        correctly, now confirmed live rather than just plausible.

        A second candidate from that same analysis, "FBEEP" (also
        found in liblegacyCore.so, right next to FIND) is NOT part of
        this project's own confirmed CommandType enum at all --
        "liblegacyCore" in its own filename raised a real question
        about whether it even applies to Prime robots' command channel
        the way FIND does -- moot now that FIND itself is confirmed
        working, no fallback needed.

        Needs irbt_topic_prefix (see __init__/auth.py's LoginResult),
        same requirement as watch_live_map() -- raises RuntimeError
        immediately if missing, rather than silently publishing to a
        malformed topic.

        NOT region-aware -- there is no known way to specify
        rooms/zones/CommandParams through this simple payload shape.
        For that, RoutineCommand/send_mission_command() may still be
        the right (if unconfirmed) tool -- or an entirely different,
        not-yet-discovered mechanism may be needed. Fire-and-forget, no
        response wait -- see publish_cmd()'s docstring for why.

        CONFIRMED STRUCTURAL LIMITATION (parallel native-analysis
        track): the real app's own basic "Start" button does NOT send
        a bare command the way this method does -- it explicitly
        fetches the account's currently active cleaning preferences
        (suction level, carpet boost mode, etc.) and sends a full
        CommandParams built from them (see CommandParams's own
        docstring for the confirmed evidence trail). This method's
        payload shape structurally cannot carry any of that -- not a
        missing optional field, a fundamentally simpler wire shape
        than RoutineCommand. Whatever suction/carpet-boost setting the
        robot ends up running a mission with, when started this way,
        is decided entirely by the robot's own fallback, not by
        mirroring the account's actual saved preferences -- a real
        parity gap with the app's own behavior, worth knowing if a
        mission behaves differently (e.g. runs at unexpectedly high
        power) than starting the same robot from the real app would."""
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
        problem this hypothesis descends from.

        CORRECTED (this session, parallel native-analysis track,
        directly reversing an earlier recommendation here): the
        earlier advice was to favor a favorite_id-ONLY RoutineCommand
        over hand-built regions, reasoning that referencing something
        already app-defined would be safer. That's now known to be
        WRONG -- traced directly through the real app's own
        RoutineCommandBuilder: setFromFavorite(favoriteId, commandDefs)
        stores BOTH the favorite_id AND the favorite's full, resolved
        command definitions (regions/params/id_multipolys/map_id/
        pmap_version_id), and build() sends ALL of it together, not
        favorite_id alone. A favorite_id-only command is not a safer
        subset of what the app does -- it's something the app itself

        GAP CLOSED (roombapy-prime v0.1.11a21): this finding sat
        documented but unimplemented for a while -- verify_region_
        commands.py's own stages 1/1b/2 never actually added
        favorite_id to the outgoing command, despite fetching the
        favorite (and therefore knowing its real id) every time. Every
        real field-test payload up to that point (chairstacker,
        jayjay13011) was missing this field entirely. Now fixed via
        _add_favorite_id_if_missing() -- see that function's own
        docstring in verify_region_commands.py.
        never actually sends, and deviating from confirmed real
        behavior is the greater risk here, not the lesser one.

        UPDATE (same track, follow-up): build() also computes a
        routine_modified flag by comparing the command being built
        against the ORIGINAL favorite (region count, region order/IDs,
        and each region's user-modifiable params -- see CommandParams'
        own docstring for the exact 7-field non-user-modifiable list).
        This is a COMPUTED value, not something to set arbitrarily --
        which means hand-constructing a "favorite_id + resolved
        regions" command from scratch would ALSO need this comparison
        done correctly to match real behavior.

        UPDATE (same track, ad-hoc zones specifically): a hand-built
        TID (ad-hoc/temporary zone) region is a further, separate risk
        on top of the above -- its id must come from a reserved
        160-199 range (a real device manages this via its own
        adHocCounter, not something to invent), and its paired
        CommandPolygon must share that exact same id, with metadata
        referencing a real furniture id. RID/ZID (persistent rooms/
        zones from actual map data) don't have this extra constraint.
        See RegionType.TID's own docstring for the full mechanism.

        THE ACTUAL SAFEST TEST, given all of this: don't hand-
        construct anything, and avoid TID/ad-hoc regions entirely.
        Fetch an existing favorite via get_favorites(), take one of
        its own command_defs entries completely UNCHANGED (ideally
        one using ordinary RID/ZID regions from real map data, not an
        ad-hoc one), and send exactly that via this method. Since
        nothing was modified relative to its own origin, whatever
        routine_modified value it already carries (likely False/
        absent, as an unmodified replay) should already be correct --
        this sidesteps both the modified-flag computation question and
        the ad-hoc-region-construction question entirely, rather than
        needing to get either right from scratch.

        Same requirements/behavior as send_simple_command(): needs
        irbt_topic_prefix, fire-and-forget (no response wait, see
        publish_cmd()'s docstring for why).

        FIRST LIVE TEST RESULT (chairstacker, real device): the
        actual safest test described above -- an existing favorite's
        own command_def, resent completely unchanged -- produced NO
        observable effect. The robot did not move, and nothing
        appeared in the real app either. Cause not yet isolated
        between two real possibilities: (a) the favorite's own map/
        zone reference (p2map_id + user_p2mapv_id) may simply be
        stale if the map has been rebuilt since the favorite was
        created -- a robot-side rejection of an outdated reference
        that would happen regardless of transport, or (b) the
        transport hypothesis itself (this method existing at all) may
        be wrong. Distinguishing test in progress: whether the exact
        same favorite still works when run from the real app. Treat
        this method as still fully unconfirmed either way -- this
        result doesn't newly confirm OR rule out the hypothesis by
        itself."""
        if self._irbt_topic_prefix is None:
            raise RuntimeError(
                "send_routine_command_via_cmd_topic() needs irbt_topic_prefix (from LoginResult) "
                "-- missing here, so the correct topic can't be built."
            )
        await asyncio.to_thread(self._mqtt.publish_cmd_payload, self._irbt_topic_prefix, command.to_json())

    async def send_umi_get_request(self, args: list[str], request_id: int = 1) -> None:
        """EXPERIMENTAL, UNCONFIRMED (this session) -- a well-reasoned
        hypothesis found via native decompilation, NOT a confirmed
        working path. Read this whole docstring before using it
        against a real device.

        UPDATE (this session, live wildcard capture, chairstacker): a
        live mission was captured with THIS exact request sent, and
        separately, "pos_update" messages containing what looks like
        live position/path data were ALSO observed arriving on the
        wildcard channel -- repeatedly, throughout the mission. CHECKED
        DIRECTLY, not just assumed: the FIRST pos_update in that
        capture (timestamp 1784491542) arrived 8 seconds BEFORE this
        exact request was sent (its own echoed "time" field:
        1784491550) -- pos_update was already flowing before the
        request existed, which settles it: this is not a response to
        this request, position data is simply pushed continuously
        regardless (see mqtt_client.py's notes next to
        rejected_report_topic() for the full pos_update finding).
        UPDATED again (jayjay13011, v0.1.11a6): the exact topic is now
        confirmed too (livemap_topic()), and watch_live_map() is the
        proper, already-built, now-also-confirmed way to consume this
        -- no request needed, and no need to fall back to a generic
        wildcard capture either. Left in place since the request itself
        was still a reasonable thing to have tried, and this doesn't
        rule out this request mattering for some other purpose (args
        other than "pose"?) --
        but "pose" specifically no longer looks like it needs this path.

        THE HYPOTHESIS: a request payload for the legacy "UMI" protocol
        family was found as a literal string in libcorebase.so:
        {"do": "get", "args": ["pose"], "id": <n>} -- alongside a
        general write-side pattern {"do": "set", "args": [%s]}. This is
        a generic do/args/id request protocol, not tied to a specific
        topic path -- which also explains why no dedicated
        "/things/%s/position"-style topic literal could be found at
        all (see mqtt_client.py's notes next to rejected_report_topic()
        for the full investigation trail): the intent lives in the
        payload (args=["pose"]), not in the topic.

        WHAT THIS METHOD DOES: publishes {"do": "get", "args": args,
        "id": request_id} to cmd_topic() -- the SAME topic
        send_simple_command() already uses, confirmed working for its
        own (differently-shaped) payload. Nothing more; this does not
        wait for or know where a response would arrive.

        WHY THE RESPONSE SIDE IS ESPECIALLY UNCERTAIN: a separate
        finding, core::RoombaSchemaField::kRobotPositionResponseTopic,
        suggests the response topic may be specified BY the requester
        inside the request payload itself, rather than being a fixed,
        predictable path -- meaning even a successful request might
        not have anywhere obvious to listen for the answer. A wildcard
        subscription (see watch_raw_topic(), or
        verify_mission_timeline.py's --watch-wildcard) is the practical
        way to have any chance of catching a response, since its
        destination can't be predicted in advance.

        WHY THIS HAS NOT BEEN LIVE-TESTED, AND THE CAVEAT THAT MATTERS
        MOST: this exact do/args/id literal was found associated with
        the UMI/legacy protocol family, which a related investigation
        confirmed has AT LEAST ONE transport variant that is local-
        HTTPS-only, not cloud-reachable at all (see
        GetAssetMissionStatusCommand's notes in mqtt_client.py). UMI
        does have other, MQTT-capable variants too (confirmed by a
        "Could not parse mqtt umi pose response" error string), so
        this is not automatically a dead end -- but whether THIS
        specific request, sent to THIS specific topic (cmd_topic, the
        AWS IoT command channel), is one of the MQTT-capable variants
        or the local-only kind is genuinely unknown. Same elevated-
        risk caveat as send_routine_command_via_cmd_topic(): a wrong
        guess here means a real device receiving a plausible-looking
        but not-actually-matching request, not the safe silence a
        topic-discovery mismatch would produce.

        Same requirements as the other cmd_topic()-based methods: needs
        irbt_topic_prefix, fire-and-forget."""
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
        never raises for a simple "not found"."""
        from .models import parse_user_households

        raw = await self.get_user_households()
        if isinstance(raw, dict) and "household_robots" in raw:
            raw_list = [raw]
        elif isinstance(raw, list):
            raw_list = raw
        else:
            raw_list = []

        for household in parse_user_households(raw_list):
            if any(r.robot_id == self.blid for r in household.household_robots):
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
        self,
        named: str | None = None,
        *,
        queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE,
        max_reconnect_backoff: float = DEFAULT_MAX_RECONNECT_BACKOFF_SECONDS,
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

        IMPORTANT (this session): a live idle-vs-mid-mission diff of
        get_state() (chairstacker) confirmed the classic shadow's
        reported state is BYTE-IDENTICAL whether the robot is idle or
        actively cleaning -- but that's a snapshot comparison (two
        separate GET requests), not a test of this method itself.

        CORRECTION (this session, parallel reverse-engineering track):
        this docstring previously claimed live mission status "does NOT
        flow through get_state()/watch_state() at all" -- the
        watch_state() part of that claim was an unverified extension of
        the get_state() snapshot-diff finding, never actually tested.
        This method's own delta subscription has never been run live
        during an active mission. It remains a real, concrete,
        not-yet-run test: AWS IoT's standard shadow/update/delta
        semantics push a message on every change, which a before/after
        snapshot comparison could simply never surface even if changes
        genuinely happen in between. Kept for whatever it DOES cover
        (map/settings-adjacent changes) -- but "kept for" no longer
        means "confirmed to be useless for mission/battery status
        specifically"; that's now an open question again, not a closed
        one.

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

        RECONNECTS TRANSPARENTLY, unbounded retries with exponential
        backoff -- see _watch_topic()'s own docstring, which does the
        actual work here; this method's only job is picking the topic.
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

        Subscribes to {irbt_prefix}/things/{blid}/mission/timeline/report,
        found via native decompilation (libcorebase.so's
        core::protocol::AssetIotTopicFactory::createMissionTimelineTopic(),
        prompted by a live finding: two separate get_state() snapshots
        (idle vs. mid-mission) were byte-identical.

        CORRECTION (this session, parallel reverse-engineering track):
        the original framing here overreached. What was actually tested
        is a snapshot DIFF of get_state() -- two point-in-time GET
        requests, compared. watch_state()'s own delta subscription
        (.../shadow/update/delta, AWS IoT's standard push-on-change
        mechanism) has never actually been run live WHILE a mission was
        active -- only assumed, by extension, to behave the same way.
        That assumption was never tested and may be wrong: a delta
        subscription could plausibly see intermediate changes a
        before/after snapshot comparison would never surface. See
        watch_state()'s own docstring for the correction there too.
        This topic (mission/timeline/report) remains a solid, separately
        justified finding either way -- it doesn't depend on the
        watch_state() question.

        WHAT'S CONFIRMED vs. NOT, precisely:
        - The topic NAME and its existence: confirmed, from native
          symbols (createMissionTimelineTopic, IotTopicType::kReport).
        - The irbt_topic_prefix applying here the same way it does for
          the already-live-confirmed command topic
          (createCommandPublishTopic, same factory, same constructor):
          a strong, well-reasoned inference (same factory instance,
          same ServiceDiscoveryData source), NOT independently
          live-confirmed for THIS specific topic.
        - The payload SHAPE: genuinely unknown. RobotMissionStatusEventImpl's
          decompiled constructor signature (AssetId, RobotMissionType,
          string, RobotMissionPhase, string, short, short, int,
          RobotReadinessState, short, vector<RobotReadinessState>,
          vector<short>, short, int, long, long, long, string,
          optional<int>) suggests real mission fields exist somewhere
          in whatever arrives here, but there is no confirmed JSON
          mapping for any of it -- this method exists to capture a live
          sample, not to parse one. ShadowResponse.payload is whatever
          JSON (or raw string, if not JSON) arrives, completely
          unparsed/untyped.

        Needs irbt_topic_prefix (see __init__/auth.py's LoginResult) --
        raises ValueError immediately if not available, same as
        send_simple_command()/watch_live_map().

        Same reconnect-with-backoff behavior as watch_state() -- see
        _watch_topic()'s docstring.
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

        WHY update/accepted, not update/delta: delta only reflects
        differences between desired and reported state -- fields that
        are purely device-reported (never written as "desired", e.g.
        a battery percentage) never appear in a delta message no
        matter how often they change, confirmed directly from AWS's
        own Device Shadow documentation. update/accepted fires on
        every accepted shadow update regardless of desired/reported
        matching, making it the correct channel for read-only,
        report-only shadow content like ro-currentstate's battery/
        dock/bin fields.

        Each yielded ShadowResponse's own `.topic` tells you which
        named shadow the update came from (the wildcard resolves to
        the real shadow name in the actual message) -- parse the
        segment between ".../shadow/name/" and "/update/accepted" if
        you need to distinguish them.

        NOT YET LIVE-TESTED as of this writing -- a reasoned, safety-
        checked hypothesis (matching a confirmed real-app pattern),
        not a confirmed-working mechanism yet."""
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
                while True:
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
                            await asyncio.to_thread(self._mqtt.reconnect)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning(
                            "roombapy-prime: MQTT reconnect attempt failed (%s) -- retrying in %.0fs",
                            exc, backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, max_reconnect_backoff)
                    else:
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

        CORRECTED (July 11, see
        docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md point B1) -- an
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
