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

import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass
from json.decoder import JSONDecodeError
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .auth import ConnectionToken

_LOGGER = logging.getLogger(__name__)


class ShadowError(Exception):
    """Raised when a shadow operation is rejected or times out."""


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
    many operations yet — construct, do what you need, disconnect. A
    production version would add automatic token refresh (tokens expire
    in ~1h) and reconnection with backoff; neither exists here yet."""

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
        self._client.connect(self._endpoint, port=443, keepalive=300)
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
            assert self._client is not None, "call connect() first"
            _LOGGER.info(
                "roombapy-prime MQTT: replacing token and reconnecting (%d persistent subscription(s) to restore)",
                len(self._persistent),
            )
            self._token = new_token
            topics_to_restore = list(self._persistent.keys())

            self.disconnect()
            self._connected = False
            self._connect_error = None
            self.connect(timeout=timeout)

            # _persistent itself is state on self, not on the paho
            # client object -- so it survives disconnect()/connect()
            # automatically. The BROKER no longer knows the
            # subscriptions after a fresh connect(), though --
            # re-subscribe directly on the new paho client, NOT via
            # subscribe() (that would append duplicate callback entries).
            # NEW (session 33): wait for all SUBACKs before returning --
            # for the same consistency reasons as subscribe()/
            # get_shadow(), see their comments.
            self._subscribe_and_wait(topics_to_restore)

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None) -> None:
        if reason_code == 0:
            self._connected = True
        else:
            self._connect_error = str(reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload)
        except JSONDecodeError:
            payload = msg.payload.decode(errors="replace")
        response = ShadowResponse(topic=msg.topic, payload=payload)
        callbacks = self._pending.pop(msg.topic, [])
        for cb in callbacks:
            cb(response)
        # Persistent subscribers are separate and NOT popped -- they stay
        # registered for every future message on this topic.
        for cb in self._persistent.get(msg.topic, []):
            cb(response)

    def shadow_topic(self, suffix: str, named: str | None = None) -> str:
        """Public accessor for building a full shadow topic, e.g.
        shadow_topic("update/delta") -> "$aws/things/{blid}/shadow/update/delta".
        Exists so callers (prime_robot.py) don't need to reach into the
        private _shadow_base() helper."""
        return f"{_shadow_base(self._blid, named)}/{suffix}"

    def livemap_topic(self, irbt_topic_prefix: str) -> str:
        """NEW, UNCERTAIN. Builds the fixed live-map topic pattern the
        way the real app uses it (core::MQTTTopicResolverAdapter.resolve()
        -> "{prefix}/{identifier}", mqttClient.subscribe(irbt,
        "livemap/update", assetId) in P2MapAPIFetching.observeLiveMap())
        -- NOT a shadow topic, completely independent of get_shadow()/
        update_shadow(). The exact concatenation order of blid and
        "livemap/update" into a single "identifier" isn't conclusively
        confirmed -- assumed here as "{blid}/livemap/update" (the most
        plausible reading of the three subscribe() arguments), not read
        directly from the wire format."""
        return f"{irbt_topic_prefix}/{self._blid}/livemap/update"

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
        a token swap, never by more than `timeout` itself."""
        with self._client_lock:
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
        docstring for the tradeoff."""
        with self._client_lock:
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
