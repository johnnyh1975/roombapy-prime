"""
roombapy_prime.mqtt_client — AWS IoT Custom Authorizer connection over
MQTT-over-WebSocket.

Extracted and cleaned up from validated, live-tested standalone scripts
(stage2/3/4/7). Confirmed working: connect, read (named + classic shadow),
write (confirmed to actually reach the robot, not just the shadow
document — see CLOUD_SHADOW_PUSH_FINDINGS.md section 5 for the
timing-correlated proof).

Key corrections baked in here that were NOT obvious from the start:
  - This is WebSocket (wss://{host}:443/mqtt), not raw MQTT-over-TLS on
    port 8883. The three auth values go in as custom WebSocket headers,
    not as MQTT username/password.
  - client_id MUST be the server-issued connection_tokens[0].client_id
    (see auth.py's ConnectionToken) — a locally-generated one will not
    match what's embedded inside iot_token and the connection will fail.
  - Never subscribe to a wildcard (shadow/#) or to any topic not
    confirmed via APK/native analysis — both have caused immediate
    "Unspecified error" disconnects in testing. Only use the specific
    get/update/delta topics this module already constructs.
  - Disable paho-mqtt's automatic reconnect (_reconnect_on_failure) for
    short-lived diagnostic-style connections, or guard against re-running
    setup logic on every reconnect — otherwise a disconnect can trigger
    an effectively infinite reconnect loop.

Confirmed on EPHEMERAL (900-series) and SMART-tier (i7-series) robots.
NOT yet confirmed against a Prime/V4 account — native strings suggest the
same shadow topic conventions apply (ClassicThingShadowTopicFactory /
NamedThingShadowTopicFactory both exist in the shared native core), but
this is unverified live for V4.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass
from json.decoder import JSONDecodeError
from typing import Any
from collections.abc import Callable

import paho.mqtt.client as mqtt

from .auth import ConnectionToken

_LOGGER = logging.getLogger(__name__)


class ShadowError(Exception):
    """Raised when a shadow operation is rejected or times out.

    Subclassed below (this session, ha_roomba_plus translation-key
    prep) -- see auth.py's AuthError docstring for the same reasoning:
    callers that only care about "something failed" keep catching
    ShadowError itself, callers that need to distinguish categories
    for translation-key mapping catch the specific subclass."""


class ShadowSSLError(ShadowError):
    """TLS/certificate verification failure -- see
    _raise_clear_ssl_error()."""


class ShadowConnectionError(ShadowError):
    """Could not establish the connection at all -- DNS failure,
    connection refused, or a connect-level timeout (paho-mqtt's
    synchronous connect() raises all of these as plain OSError
    subclasses, indistinguishable from each other in a way that would
    justify a more specific message -- unlike the aiohttp side, there's
    no separate "timeout after connecting" case here, since a TLS
    handshake or MQTT CONNACK timeout would also surface as one of
    these OSError subclasses from the same blocking call, not
    separately). Deliberately does NOT claim to know whether this is
    iRobot's fault or the caller's own network, same as
    AuthConnectionError/RestConnectionError."""


def _raise_clear_ssl_error(exc: ssl.SSLError) -> None:
    """Re-raise a TLS/certificate failure as a clear ShadowSSLError
    instead of letting the raw ssl module exception bubble up as an
    opaque error.

    NEW (V4/Prime prep, following the same fix in auth.py/rest_client.py
    -- but a genuinely different mechanism here, not just a copy-paste).
    This module uses paho-mqtt directly (synchronous connect(), not
    aiohttp), so a TLS handshake failure here would never surface as
    aiohttp.ClientSSLError -- paho-mqtt's Client.connect() is a blocking
    call that raises ssl.SSLError (or a subclass, e.g.
    SSLCertVerificationError) directly, before on_connect's reason_code
    path ever gets a chance to fire (that path is for MQTT-protocol-level
    rejections, which only happen AFTER a successful TLS handshake).
    UNLIKE the aiohttp fix, this one is NOT based on a real captured
    failure in this project -- it's based on paho-mqtt's documented,
    stable connect() behavior, not a reverse-engineered assumption.
    Treat this path itself as reasoned-through, not live-confirmed,
    until an actual iRobot cert incident is caught here."""
    raise ShadowSSLError(
        "Could not verify iRobot's cloud server certificate. This is "
        "almost always a temporary problem on iRobot's servers (an "
        "expired or currently-renewing TLS certificate), not something "
        "wrong with your setup -- it should resolve on its own within a "
        "few hours."
    ) from exc


def _raise_clear_connection_error(exc: OSError) -> None:
    """Re-raise a connection-establishment failure (DNS, connection
    refused, connect-level timeout) as a clear ShadowConnectionError.
    Same reasoning as auth.py's/rest_client.py's equivalents -- see
    ShadowConnectionError's docstring for why this covers what would be
    three separate cases on the aiohttp side."""
    raise ShadowConnectionError(
        "Could not connect to iRobot's cloud servers. This could be a "
        "temporary problem with iRobot's servers, or with your own "
        "internet connection -- check that other internet-dependent "
        "services are working, and try again in a few minutes."
    ) from exc


@dataclass
class ShadowResponse:
    topic: str
    payload: dict[str, Any] | str


def _shadow_base(blid: str, named: str | None) -> str:
    """named=None -> classic/unnamed shadow. named='rw-settings' (or
    whatever a future named shadow turns out to be called) -> named
    shadow. Confirmed tier-dependent: EPHEMERAL robots only answer the
    classic shadow; SMART-tier robots answer both."""
    if named:
        return f"$aws/things/{blid}/shadow/name/{named}"
    return f"$aws/things/{blid}/shadow"


class PrimeMqttClient:
    """One connection, one blid. Not designed for long-lived reuse across
    many operations yet — construct, do what you need, disconnect.

    UPDATE (this session): disconnect detection now exists
    (on_disconnect wired up, see wait_for_disconnect()) -- previously
    there was none at all, silently leaving a long-running consumer
    hung with no signal anything had dropped. The actual reconnect-
    with-backoff LOOP lives one level up, in prime_robot.py's
    watch_state() -- this class only detects and reports the drop,
    it does not retry on its own."""

    def __init__(self, token: ConnectionToken, endpoint: str, blid: str) -> None:
        self._token = token
        self._endpoint = endpoint
        self._blid = blid
        self._client: mqtt.Client | None = None
        self._connected = False
        self._connect_error: str | None = None
        self._pending: dict[str, list[Callable[[ShadowResponse], None]]] = {}
        # Separate from _pending: _pending is one-shot (popped on first
        # matching message, used by get_shadow/update_shadow). _persistent
        # is for continuous dispatch (see subscribe()/unsubscribe() below)
        # -- callbacks stay registered until explicitly removed, and
        # multiple callbacks per topic can coexist (reference-counted at
        # the broker-subscribe level, see unsubscribe()).
        self._persistent: dict[str, list[Callable[[ShadowResponse], None]]] = {}
        # NEW (session 33): fixes a real, previously unnoticed bug --
        # subscribe() in Paho is itself asynchronous (only queues the
        # SUBSCRIBE packet, doesn't wait for the broker's SUBACK).
        # Previously, publish() was called right after, without waiting
        # for confirmation -- if the response came back BEFORE the
        # SUBACK was processed, it was lost (the client was technically
        # not yet subscribed at that point). Likely explains the
        # "get_settings() sometimes responds, sometimes doesn't" on the
        # same device observed by chairstacker -- a pure network-timing
        # race, not a tier difference.
        self._confirmed_mids: set[int] = set()
        # NEW: closes a previously documented gap (see README) --
        # replace_token() disconnects/reconnects self._client; without
        # protection, a CONCURRENTLY (via asyncio.to_thread, i.e. a real
        # OS thread) running get_shadow()/update_shadow() call could
        # access an already-disconnected or not-yet-fully-connected
        # self._client in the middle of this switch. threading.Lock, not
        # asyncio.Lock -- these methods run in real threads (to_thread),
        # not as coroutines on the same event loop.
        self._client_lock = threading.Lock()

        # NEW (this session, roombapy-prime reconnect hardening): no
        # on_disconnect callback existed at all before this -- the client
        # had zero visibility into a dropped connection. _disconnect_loop
        # and _disconnect_reason let an async caller (see watch_state())
        # await a disconnect event instead of polling self._connected.
        # A plain threading.Event wouldn't work here: the callback fires
        # on paho's own background thread, but the waiter is a coroutine
        # on the asyncio event loop -- same call_soon_threadsafe pattern
        # already used for _on_delta/queue in watch_state().
        self._disconnect_loop: asyncio.AbstractEventLoop | None = None
        self._disconnect_event: asyncio.Event | None = None
        self._disconnect_reason: str | None = None

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._token.client_id,
            protocol=mqtt.MQTTv311,
            transport="websockets",
        )
        client.ws_set_options(
            path="/mqtt",
            headers={
                "x-amz-customauthorizer-name": self._token.iot_authorizer_name,
                "x-amz-customauthorizer-signature": self._token.iot_signature,
                "x-irobot-auth": self._token.iot_token,
            },
        )
        try:
            import certifi
            ca_certs = certifi.where()
        except ImportError:
            ca_certs = None
        client.tls_set(ca_certs=ca_certs, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        # Short-lived connections in practice so far — avoid an infinite
        # reconnect loop if the broker drops us for any reason.
        client._reconnect_on_failure = False
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        return client

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None) -> None:
        """NEW (session 33) -- records that the broker has actually
        confirmed the SUBSCRIBE with this mid (SUBACK). See __init__'s
        comment on _confirmed_mids for the bug this fixes."""
        self._confirmed_mids.add(mid)

    def _subscribe_and_wait(self, topics: list[str], timeout: float = 3.0) -> None:
        """NEW (session 33) -- subscribes to all given topics and waits
        for the SUBACK of EACH ONE before returning. The actual fix for
        the race described in get_shadow()/update_shadow() -- publish()
        must only happen after this. `timeout` deliberately short
        (SUBACKs are usually very fast, unlike the actual shadow
        response) -- if this timeout runs out, proceeds anyway (better
        a small residual risk than a broken library, in case a broker
        never sends a SUBACK for some reason)."""
        assert self._client is not None
        mids = []
        for topic in topics:
            result, mid = self._client.subscribe(topic, qos=1)
            mids.append(mid)
        waited = 0.0
        while waited < timeout and not all(m in self._confirmed_mids for m in mids):
            time.sleep(0.05)
            waited += 0.05
        for m in mids:
            self._confirmed_mids.discard(m)

    def connect(self, timeout: float = 10.0) -> None:
        self._client = self._build_client()
        try:
            self._client.connect(self._endpoint, port=443, keepalive=300)
        except ssl.SSLError as exc:
            _raise_clear_ssl_error(exc)
        except OSError as exc:
            _raise_clear_connection_error(exc)
        self._client.loop_start()
        waited = 0.0
        while waited < timeout and not self._connected and self._connect_error is None:
            time.sleep(0.2)
            waited += 0.2
        if self._connect_error:
            raise ShadowError(f"Connect failed: {self._connect_error}")
        if not self._connected:
            raise ShadowError(f"Connect timed out after {timeout}s")

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()

    # --- Proactive token refresh ---------------------------------------
    #
    # There's no refresh endpoint (see auth.py) -- "refresh" here means:
    # reconnect with a newly-logged-in token, WHILE any running
    # subscribe() watchers keep running transparently.

    REFRESH_MARGIN_SECONDS = 300  # 5 minutes before expiry -- chosen
    # arbitrarily to leave time for the re-login roundtrip itself, not
    # empirically tested against the real ~1h token lifetime.

    def seconds_until_token_refresh_due(self) -> float | None:
        """None if the token has no expires field (see
        ConnectionToken.seconds_until_expiry) -- then proactive
        scheduling isn't possible, which is a known limitation, not a
        silent bug."""
        remaining = self._token.seconds_until_expiry()
        if remaining is None:
            return None
        return max(remaining - self.REFRESH_MARGIN_SECONDS, 0.0)

    def replace_token(self, new_token: ConnectionToken, timeout: float = 10.0) -> None:
        """Swaps the token, disconnects, reconnects, restores all
        running persistent subscriptions (see subscribe()) -- so
        running watch_*() generators keep going transparently, without
        the caller needing to re-subscribe.

        NOT restored: open _pending entries (in-flight get_shadow()/
        update_shadow() calls). If a refresh happens to fall in the
        middle of such a call, it simply runs into its timeout and
        raises ShadowError -- an accepted edge case, since refreshes
        are scheduled with lead time (see REFRESH_MARGIN_SECONDS), no
        guarantee against overlap.

        NEW: now runs under self._client_lock -- closes the gap
        documented here before ("not thread-/call-safe against
        get_shadow()/update_shadow()"). A concurrent get_shadow()/
        update_shadow() call now waits until replace_token() is done,
        instead of accessing a half-disconnected client. The
        _pending edge case described above still remains, though --
        the lock only prevents concurrent ACCESS to self._client, not
        the underlying issue of "a refresh falls into an in-flight
        get/update"."""
        with self._client_lock:
            self._token = new_token
            self.reconnect(timeout=timeout)

    def reconnect(self, timeout: float = 10.0) -> None:
        """NEW (this session, reconnect-after-drop hardening). Same-
        token counterpart to replace_token() -- extracted from it,
        since the "disconnect, connect, restore all persistent
        subscriptions" sequence is identical either way, only whether
        the token changes first differs. Used by prime_robot.py's
        watch_state() to recover after wait_for_disconnect() fires.

        Not itself under self._client_lock -- callers that need that
        protection (replace_token()) take it themselves before calling
        this; watch_state()'s reconnect loop deliberately does NOT hold
        it for the length of a potentially-long backoff wait."""
        assert self._client is not None, "call connect() first"
        _LOGGER.info(
            "roombapy-prime MQTT: reconnecting (%d persistent subscription(s) to restore)",
            len(self._persistent),
        )
        topics_to_restore = list(self._persistent.keys())

        self.disconnect()
        self._connected = False
        self._connect_error = None
        self.connect(timeout=timeout)

        # _persistent itself is state on self, not on the paho client
        # object -- so it survives disconnect()/connect() automatically.
        # The BROKER no longer knows the subscriptions after a fresh
        # connect(), though -- re-subscribe directly on the new paho
        # client, NOT via subscribe() (that would append duplicate
        # callback entries, since _persistent already has them).
        self._subscribe_and_wait(topics_to_restore)

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None) -> None:
        if reason_code == 0:
            self._connected = True
        else:
            self._connect_error = str(reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None) -> None:
        """NEW (this session). Previously not wired up at all -- the
        client had zero visibility into a dropped connection, silently
        leaving any long-running watch_state() consumer hung on an
        empty queue forever with no signal anything was wrong (see
        this class's own docstring: "reconnection with backoff;
        neither exists here yet" -- this is the first half of closing
        that gap; watch_state() in prime_robot.py is the second)."""
        self._connected = False
        self._disconnect_reason = str(reason_code)
        if self._disconnect_loop is not None and self._disconnect_event is not None:
            self._disconnect_loop.call_soon_threadsafe(self._disconnect_event.set)

    async def wait_for_disconnect(self) -> str:
        """Resolves with the disconnect reason once this connection
        drops -- lets an async caller (see prime_robot.py's
        watch_state()) detect a drop via await, instead of polling
        self._connected in a loop. Must be called again after each
        reconnect (the event is created fresh here, not reused) --
        this is deliberately a one-shot wait, not a persistent
        subscription, to keep the ownership of "what happens on
        disconnect" entirely with the caller."""
        self._disconnect_loop = asyncio.get_running_loop()
        self._disconnect_event = asyncio.Event()
        await self._disconnect_event.wait()
        return self._disconnect_reason or "unknown"

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload)
        except JSONDecodeError:
            payload = msg.payload.decode(errors="replace")
        response = ShadowResponse(topic=msg.topic, payload=payload)
        callbacks = self._pending.pop(msg.topic, [])
        for cb in callbacks:
            cb(response)
        # BUG FOUND AND FIXED (this session, via a live wildcard capture
        # that came back suspiciously empty despite matching traffic
        # demonstrably existing -- chairstacker). Persistent subscribers
        # are matched by PATTERN now, not an exact dict-key lookup on
        # msg.topic. A persistent registration can be a wildcard filter
        # (e.g. "{prefix}/things/{blid}/#", see watch_raw_topic()) --
        # msg.topic is always the concrete topic a message actually
        # arrived on, never the literal wildcard string itself, so a
        # plain `self._persistent.get(msg.topic, [])` could NEVER find a
        # wildcard registration: its own dedicated watcher would show
        # "zero messages" forever, regardless of how much matching
        # traffic actually existed. _pending (above) is unaffected --
        # it's only ever used for one-shot exact-topic request/response
        # waits (get_shadow()/update_shadow()), never wildcards.
        for pattern, cbs in self._persistent.items():
            if mqtt.topic_matches_sub(pattern, msg.topic):
                for cb in cbs:
                    cb(response)

    def shadow_topic(self, suffix: str, named: str | None = None) -> str:
        """Public accessor for building a full shadow topic, e.g.
        shadow_topic("update/delta") -> "$aws/things/{blid}/shadow/update/delta".
        Exists so callers (prime_robot.py) don't need to reach into the
        private _shadow_base() helper."""
        return f"{_shadow_base(self._blid, named)}/{suffix}"

    def livemap_topic(self, irbt_topic_prefix: str) -> str:
        """CONFIRMED LIVE (this session, jayjay13011, roombapy-prime
        v0.1.11a6 -- the first capture with response.topic tracking,
        settling this exactly): this topic pattern
        ("{prefix}/things/{blid}/livemap/update") is EXACTLY where both
        PositionUpdateMessage and MapUpdateMessage payloads arrive,
        confirmed directly against a real device's topic-frequency
        summary (63 messages on this exact topic in one capture). No
        longer just an analogy to cmd_topic()'s pattern -- this is now
        independently, directly confirmed for livemap specifically.

        UPDATED (session 39, superseded by the above): Builds the
        fixed live-map topic pattern the way the real app uses it
        (core::MQTTTopicResolverAdapter.resolve() -> "{prefix}/
        {identifier}", mqttClient.subscribe(irbt, "livemap/update",
        assetId) in P2MapAPIFetching.observeLiveMap()) -- NOT a shadow
        topic, completely independent of get_shadow()/update_shadow().
        """
        return f"{irbt_topic_prefix}/things/{self._blid}/livemap/update"

    def cmd_topic(self, irbt_topic_prefix: str) -> str:
        """NEW (session 39). Mission commands (start/pause/stop/resume/
        dock/find/evac/reset/etc.) do NOT go through the device shadow
        at all, unlike this library's previous assumption (see
        update_shadow()'s docstring and prime_robot.py's
        send_mission_command(), both now believed WRONG for this
        purpose).

        CORRECTED basis: a live test against a real account
        (chairstacker, session 39) showed every attempt via
        update_shadow() timing out with zero response (not even
        /rejected) -- consistent with publishing to a topic the shadow
        service doesn't recognize at all, not a permission or payload
        problem on an otherwise-correct topic. Independently, this
        library's own native disassembly (objdump on libcorebase.so)
        found the literal format string "/things/%s/cmd" -- a
        DIFFERENT topic family from "$aws/things/%s/shadow/update"
        (which does exist in liblegacyCore.so, but is presumably used
        for something else, e.g. the settings/schedule "delta"
        mechanism, not mission commands). This matches
        base_roomba_config.json's own "topic" field for mission-control
        commandIds ("Control", "AssetControlCommand"): the value is
        literally "cmd", a THIRD category distinct from "shadow" (used
        by GetThingShadow, confirmed working) and "delta" (used by
        settings/schedule commands) -- not just a coincidental label.

        Independently, a third-party, unaffiliated GitHub project
        (lvigilantecorreo-commits/roomba-v4, MIT-licensed, reverse-
        engineered via mitmproxy + APK strings + Ghidra, author reports
        the exact command actually moved a real robot) documents this
        same topic shape explicitly: "{irbt_topics}/things/{BLID}/cmd",
        with a simple payload {"command": ..., "time": ..., "initiator":
        ...} -- see publish_cmd()'s docstring. This is an external,
        unverified-by-us source, not an Anthropic/roombapy-prime
        finding -- but the topic pattern it describes independently
        matches this library's own native string discovery, which is
        the strongest kind of corroboration available without a live
        test of our own against this exact path.

        UPDATE (session 43): the "irbt_topic_prefix" VALUE extraction
        itself is now confirmed (see auth.py's LoginResult docstring --
        real field name "irbtTopics", real confirmed value
        "v011-irbthbu" from a live account, byte-identical to the
        third-party project's example value above). What remains
        UNCONFIRMED BY THIS LIBRARY ITSELF is whether the resulting
        topic, once correctly built, actually gets a real robot to
        react when published to -- that's the next thing a live test
        needs to settle."""
        return f"{irbt_topic_prefix}/things/{self._blid}/cmd"

    def mission_timeline_topic(self, irbt_topic_prefix: str, *, report: bool = True) -> str:
        """NEW (this session). Found via native decompilation
        (libcorebase.so's core::protocol::AssetIotTopicFactory::
        createMissionTimelineTopic(IotTopicType), a sibling method of
        the SAME factory/constructor as createCommandPublishTopic() --
        the already-live-confirmed source of cmd_topic() above.

        PROMPTED BY: a live idle-vs-mid-mission diff (chairstacker) that
        showed the classic shadow's reported state is byte-identical
        whether the robot is idle or actively cleaning -- proving that
        specific comparison (two point-in-time get_state() snapshots)
        doesn't move during a mission. CORRECTION (this session,
        parallel reverse-engineering track): this was previously
        over-stated as "live mission status does NOT flow through the
        shadow mechanism at all" -- that's broader than the evidence.
        The snapshot diff says nothing about whether the shadow's
        update/delta PUSH channel (watch_state()) sees intermediate
        changes; that specific test has never been run live during an
        active mission. This mission-timeline topic is believed to be
        the actual channel for it regardless, based on: (a) an
        "eventList" entry named "cleanMissionStatus"
        in base_roomba_config.json (matching the Classic protocol's own
        live-mission-status channel name), and (b) a decompiled native
        class, core::RobotMissionStatusEventImpl, whose constructor
        signature contains real per-mission fields (mission type,
        phase, readiness state, multiple counters/timestamps) --
        structurally nothing like the classic shadow's static
        capability data.

        report=True -> ".../mission/timeline/report" (the direction a
        robot would push status TO the cloud/subscriber -- what a
        caller watching for live status wants). report=False ->
        ".../mission/timeline/request" (the other half of the
        kRequest/kReport pair the native IotTopicType enum defines).

        UPDATE (this session, chairstacker): the request side is no
        longer just "included for completeness, not expected to be
        useful to subscribe to" -- a real message was captured on it
        during a wildcard watch: {"timelineRequestId": <int>}, a bare
        correlation ID (NOT a Unix timestamp -- checked directly,
        decodes to 2009, nowhere near this session's actual date).
        This is the standalone confirmation of the same field
        MissionTimelineReport.timeline_request_id (added in v0.1.11a6)
        already carries when embedded in a report -- meaning the two
        topics are a genuine, now-observed request/response pair: this
        topic carries the bare request correlation ID on its own,
        and a matching report (same ID) arrives separately on the
        report topic. Only the request SIDE'S payload shape is
        confirmed by this; still unconfirmed whether publishing to
        this topic ourselves (rather than just observing the robot's
        own traffic on it) would actually trigger a fresh report --
        not attempted.

        CONFIDENCE LEVEL, precisely: the topic NAME and its existence
        are confirmed from native symbols AND now from a live capture
        (the request side specifically, this session). Whether
        irbt_topic_prefix applies here the same way it does for
        cmd_topic() is a strong, well-reasoned inference (same
        factory, same constructor source), not independently
        confirmed for this specific topic -- unlike cmd_topic(), which
        HAS a live-confirmed real-world reaction behind it. The report
        side's payload shape (beyond timeline_request_id, which IS
        confirmed) is covered by watch_mission_timeline()'s own
        docstring in prime_robot.py."""
        direction = "report" if report else "request"
        return f"{irbt_topic_prefix}/things/{self._blid}/mission/timeline/{direction}"

    def rejected_report_topic(self, irbt_topic_prefix: str) -> str:
        """NEW (this session). Found via the same native decompilation
        pass as mission_timeline_topic() -- AssetIotTopicFactory's
        third method, createCommandRejectedTopic(), a sibling of
        createCommandPublishTopic() (cmd_topic() above, already
        live-confirmed) in the exact same factory/constructor. Directly
        complements cmd_topic(): if a send_simple_command() call is
        silently ignored or has no visible effect, this topic is where
        the reason (if the device reports one at all) would be
        expected to arrive.

        Same confidence level as mission_timeline_topic(): topic name
        confirmed from native symbols, irbt_topic_prefix application
        here a strong inference (same factory) rather than
        independently live-confirmed, payload shape unknown."""
        return f"{irbt_topic_prefix}/things/{self._blid}/rejected/report"

    # NOTE (this session, for future contributors -- saves re-investigating
    # both of these): AssetIotTopicFactory has a FOURTH method beyond the
    # three above, createRobotPositionTopic(IotTopicType) -- but unlike
    # cmd_topic()/mission_timeline_topic()/rejected_report_topic(), no
    # "/things/%s/..." format-string literal for it exists anywhere in the
    # binaries (exhaustively searched: "position", "pose", "/pos", every
    # "mission/" prefix). The reason: three separate serializers exist for
    # this one command (GetRobotPositionAwsIotRobotSerializer confirms an
    # AWS IoT path DOES exist, alongside a local-secure-socket variant and a
    # RoombaPoseDeserializer) -- but the AWS IoT topic is built dynamically
    # at runtime, not from a literal, and a separate finding
    # (core::RoombaSchemaField::kRobotPositionResponseTopic) suggests the
    # response topic may be read FROM the request payload itself rather
    # than being static at all. Resolving this further would need Ghidra
    # disassembly of createRobotPositionTopic() itself -- pure string
    # analysis is exhausted here. A live wildcard capture (see
    # verify_mission_timeline.py's --watch-wildcard) is the practical way
    # to actually catch this, not more static analysis.
    #
    # Also: "Position" and "Pose" turned out to be two separate concepts
    # with their own event/deserializer pairs (RobotPositionEventImpl vs.
    # RobotPoseEventImpl/RoombaPoseDeserializer, the latter WITH
    # orientation) -- and an error string ("Could not parse mqtt umi pose
    # response") confirms pose data specifically CAN arrive over MQTT, not
    # just locally. Another concrete thing a wildcard capture might catch.
    #
    # Separately: GetAssetMissionStatusCommand (mentioned in an earlier
    # investigation, absent from base_roomba_config.json) is CONFIRMED a
    # dead end for this library -- its serializer
    # (GetAssetMissionStatusUmiSerializer) routes through
    # PollingProtocolAdapterRoombaLocalHttps, i.e. local HTTPS polling via
    # the legacy "UMI" protocol family, not any cloud channel. This also
    # explains its absence from base_roomba_config.json: that config
    # covers cloud/LSS-relevant commands only, not the UMI legacy path.
    # Not pursued further -- no cloud transport exists for it.

    # RESOLVED (this session, live wildcard capture, chairstacker): the
    # createRobotPositionTopic()/send_umi_get_request() investigation
    # above asked "does position data flow over MQTT, and if so how do
    # we ask for it" -- turns out the more useful answer is "it's
    # already being pushed continuously, unprompted, during any active
    # mission, no request needed at all." A live wildcard capture
    # (verify_mission_timeline.py --watch-wildcard) showed repeated
    # messages of this exact shape, roughly every 1-10 seconds while
    # the robot was moving:
    #
    #   {"pos_update": {"cur_path": [13, -0.104733, -0.197565,
    #    -0.489053, 5, -0.090486, -0.189392, 0.039259, 5, 1784491542]},
    #    "timestamp": 1784491542, "update_expire_ts": 1784491601}
    #
    # cur_path's shape (HYPOTHESIS for the numbers' MEANING, but the
    # STRUCTURE itself is now checked rigorously, not just eyeballed:
    # a leading point index, then repeated groups of 4 numbers, ending
    # in a Unix timestamp matching the outer "timestamp" field. Verified
    # programmatically against all 29 pos_update messages in the
    # capture -- every single group's 4th number was exactly 5, zero
    # exceptions; every group count divided the body evenly by 4, zero
    # exceptions. The first three numbers per group are plausibly x, y,
    # theta -- not confirmed against any decompiled source, but the "5"
    # being constant across every group in every message (not just most)
    # is now solid evidence it's a real structural marker, not noise --
    # its MEANING (point type? confidence level?) remains unconfirmed.
    #
    # ONE CAVEAT FOUND BY THIS SAME CHECK: point-index continuity holds
    # WITHIN a streaming session (each message's start index picks up
    # exactly where the previous one's last index left off), but NOT
    # across a session boundary -- index jumped from an expected 44 to
    # 62 at the exact point stop+dock were sent (see the expire_ts
    # window boundary below). Don't assume the index sequence is
    # globally continuous across gaps.
    #
    # CORRECTED (this session, second capture, chairstacker): an earlier
    # note here said update_expire_ts is "~60s after timestamp" -- WRONG,
    # verified directly against the numbers. update_expire_ts stays the
    # SAME fixed value across MULTIPLE consecutive pos_update messages
    # (each with its own, different, timestamp) -- not a per-message
    # expiry at all. RE-VERIFIED against all 29 pos_update messages in
    # the capture, not just a sample: exactly two distinct expire_ts
    # values, 26 messages sharing the first (spanning 59s from its
    # earliest message to that expiry) and 3 sharing the second
    # (spanning 58s) -- both windows independently landing within a
    # second of 60s, not a coincidence. Consistent with a renewable
    # ~60s "live position streaming session" window, not a per-message
    # TTL -- also matching the separately-observed {"operation": "start",
    # "start": {"duration": 60}} messages seen interspersed on the same
    # wildcard channel, plausibly the mechanism that opens/renews each
    # window (right message, right relative position in the sequence,
    # both times -- not a precisely timestamped confirmation, since
    # these messages carry no timestamp field of their own to check
    # exact alignment against). Not confirmed against any decompiled
    # source, but this framing fits every number seen in both live
    # captures so far.
    #
    # THE EXACT TOPIC IS NOW CONFIRMED (jayjay13011, roombapy-prime v0.1.11a6
    # -- the first capture with response.topic tracking, from the fix
    # described immediately below): livemap_topic() -- both pos_update and
    # map_update arrive on the SAME topic ("{prefix}/things/{blid}/
    # livemap/update"), discriminated by which key is present in the
    # payload. watch_live_map() (prime_robot.py) already wraps this
    # correctly, also now confirmed live for the first time. The gap that
    # made this unknown for a while: an earlier capture (chairstacker)
    # predated a fix to verify_mission_timeline.py that printed only the
    # static watch label for wildcard messages, not response.topic (the
    # actual concrete topic each one arrived on) -- so all 81 wildcard
    # messages in that capture were logged indistinguishably. The
    # jayjay13011 re-run, with the fixed tooling, settled it directly.

    def publish_cmd(self, irbt_topic_prefix: str, command: str, initiator: str = "localApp") -> None:
        """NEW (session 39). Publishes a simple mission command via
        cmd_topic() -- see that method's docstring for the full
        evidence trail. Payload shape {"command": str, "time": int,
        "initiator": str} matches the third-party project's
        documented, reportedly-working format exactly -- "time" is a
        Unix timestamp in SECONDS (not millis), "initiator" defaults
        to "localApp" (their literal, observed value) as opposed to
        the "cloud"/"rmtApp" values seen in this library's own
        confirmed mission HISTORY data (session 25) -- those are
        presumably what gets RECORDED afterward, not necessarily what
        the app itself sends when initiating live.

        Deliberately FIRE-AND-FORGET, no response wait: unlike
        get_shadow()/update_shadow(), there is no known
        accepted/rejected acknowledgment topic for this command family
        -- the third-party project's own account of how this was
        confirmed working describes observing the physical robot react,
        not any MQTT-level acknowledgment. Callers who want
        confirmation should poll get_state() afterward instead."""
        self.publish_cmd_payload(irbt_topic_prefix, {"command": command, "initiator": initiator})

    def publish_cmd_payload(self, irbt_topic_prefix: str, payload: dict[str, Any]) -> None:
        """NEW (session 46). Lower-level sibling of publish_cmd() --
        publishes an ARBITRARY payload dict to cmd_topic(), adding a
        "time" field (Unix seconds) if not already present. Exists for
        prime_robot.py's send_routine_command_via_cmd_topic() -- see
        that method's docstring for why a richer payload than
        publish_cmd()'s simple {command, time, initiator} might also
        be accepted here, and for the significant, elevated risk
        caveat that comes with sending anything richer than the basic
        confirmed-working case to this topic.

        Same fire-and-forget behavior as publish_cmd() -- see that
        method's docstring for why."""
        assert self._client is not None, "call connect() first"
        topic = self.cmd_topic(irbt_topic_prefix)
        full_payload = {**payload}
        full_payload.setdefault("time", int(time.time()))
        self._client.publish(topic, payload=json.dumps(full_payload), qos=1)

    def subscribe(self, topic: str, callback: Callable[[ShadowResponse], None]) -> None:
        """Register a callback that fires on EVERY message on this topic,
        indefinitely (until unsubscribe() removes it) -- for continuous
        dispatch (shadow deltas, live-map/-position streams), as opposed
        to get_shadow()/update_shadow()'s one-shot wait-for-one-response
        pattern.

        Multiple callbacks on the same topic coexist fine (each gets
        every message) -- the broker-level subscribe only happens once,
        the first time this topic is used."""
        assert self._client is not None, "call connect() first"
        is_new_topic = topic not in self._persistent
        self._persistent.setdefault(topic, []).append(callback)
        if is_new_topic:
            # NEW (session 33): same confirmation as get_shadow()/
            # update_shadow(), for consistency -- the risk here is
            # milder (only a very early first message could be missed
            # in the brief gap, not "the one expected response" like
            # with get_shadow()), but it's worth not having the same
            # bug type in two places just because the symptoms show up
            # differently.
            self._subscribe_and_wait([topic])

    def unsubscribe(self, topic: str, callback: Callable[[ShadowResponse], None]) -> None:
        """Removes exactly this callback. Reference-counted: only
        unsubscribes at the broker level once no callbacks remain for
        this topic, so two concurrent watchers on the same topic don't
        kill each other's subscription when one of them stops."""
        callbacks = self._persistent.get(topic)
        if callbacks is None:
            return
        if callback in callbacks:
            callbacks.remove(callback)
        if not callbacks:
            self._persistent.pop(topic, None)
            assert self._client is not None, "call connect() first"
            self._client.unsubscribe(topic)

    def get_shadow(self, named: str | None = None, timeout: float = 8.0) -> ShadowResponse:
        """Fetch current shadow state. named=None for the classic/unnamed
        shadow (confirmed working on all tested tiers so far); pass a
        specific name (e.g. "rw-settings") to try a named shadow — only
        confirmed to respond on SMART-tier robots, silent on EPHEMERAL.
        A ShadowError on timeout does not distinguish "doesn't exist for
        this tier" from "transient failure" — callers on EPHEMERAL-like
        devices should expect named-shadow timeouts as normal, not a bug.

        NEW: now runs under self._client_lock -- serializes against a
        concurrently running replace_token(). Deliberate tradeoff: if
        replace_token() is currently active, this call waits until it's
        done, instead of accessing a half-disconnected client -- in the
        worst case this can extend the response time by the duration of
        a token swap, never by more than `timeout` itself.

        NEW (this session, prompted by a real field report): reconnects
        first if the connection is currently known to be down.
        Previously, any caller doing a plain sequential series of
        get_shadow() calls with no reconnect logic of its own (e.g.
        verify_named_shadows.py's simple loop, unlike watch_state()/
        watch_mission_timeline()'s own hardened _watch_topic()) would,
        after a single silent mid-run disconnect, have EVERY subsequent
        get_shadow() call in that run keep trying to subscribe/publish
        on a dead connection and time out -- matching a real report of
        "first N shadows succeed, every one after that fails" with N
        varying between runs (consistent with a disconnect landing at
        an unpredictable point in the sequence, not a fixed request-
        count limit). This matches a known, documented AWS IoT MQTT SDK
        behavior: after a session is lost (a broker-side session
        timeout, or the connection dropping for long enough), the
        broker forgets prior subscriptions, and a client that doesn't
        proactively reconnect/resubscribe before its next operation
        will simply never receive a response, silently -- see e.g.
        aws/aws-iot-device-sdk-js-v2#117, where a field report
        (unrelated project, same underlying AWS IoT behavior) describes
        this exact symptom for shadow topics specifically. Cheap when
        already connected (self._connected is checked first, no-op in
        the common case) -- only pays the reconnect cost when actually
        needed."""
        with self._client_lock:
            if not self._connected:
                self.reconnect(timeout=timeout)
            base = _shadow_base(self._blid, named)
            result: list[ShadowResponse] = []

            def _capture(resp: ShadowResponse) -> None:
                result.append(resp)

            assert self._client is not None, "call connect() first"
            topics = []
            for suffix in ("get/accepted", "get/rejected"):
                topic = f"{base}/{suffix}"
                self._pending.setdefault(topic, []).append(_capture)
                topics.append(topic)
            self._subscribe_and_wait(topics)
            self._client.publish(f"{base}/get", payload=b"", qos=1)

            waited = 0.0
            while waited < timeout and not result:
                time.sleep(0.2)
                waited += 0.2
            if not result:
                raise ShadowError(f"No response to GET on {base} within {timeout}s")
            response = result[0]
            if response.topic.endswith("/get/rejected"):
                raise ShadowError(f"GET rejected: {response.payload}")
            return response

    def update_shadow(
        self, desired: dict[str, Any], named: str | None = None, timeout: float = 8.0
    ) -> ShadowResponse:
        """Set desired state. Confirmed to actually propagate to the
        physical robot, not just the shadow document — verified via a
        real, observable value change with exact timing correlation in
        the local MQTT log (see CLOUD_SHADOW_PUSH_FINDINGS.md section 5).
        A no-op write (value unchanged) will still get update/accepted
        but gives you no way to confirm actual delivery — use a genuinely
        different, restorable value if you need to verify delivery.

        NEW: now runs under self._client_lock, see get_shadow()'s
        docstring for the tradeoff. NEW (this session): also reconnects
        first if the connection is currently known to be down -- same
        reasoning as get_shadow()'s own docstring."""
        with self._client_lock:
            if not self._connected:
                self.reconnect(timeout=timeout)
            base = _shadow_base(self._blid, named)
            result: list[ShadowResponse] = []

            def _capture(resp: ShadowResponse) -> None:
                result.append(resp)

            assert self._client is not None, "call connect() first"
            topics = []
            for suffix in ("update/accepted", "update/rejected", "update/delta"):
                topic = f"{base}/{suffix}"
                self._pending.setdefault(topic, []).append(_capture)
                topics.append(topic)
            self._subscribe_and_wait(topics)
            self._client.publish(
                f"{base}/update", payload=json.dumps({"state": {"desired": desired}}), qos=1
            )

            waited = 0.0
            while waited < timeout and not result:
                time.sleep(0.2)
                waited += 0.2
            if not result:
                raise ShadowError(f"No response to UPDATE on {base} within {timeout}s")
            response = result[0]
            if response.topic.endswith("/update/rejected"):
                raise ShadowError(f"UPDATE rejected: {response.payload}")
            return response
