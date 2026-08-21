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

# HISTORICAL NOTE -- do not re-add a User-Agent header without new
# evidence. One was added here in a22, on a third-party project's
# documented (but untested) claim that AWS IoT's custom authorizer
# inspects it and grants a more restricted policy when absent.
#
# The parallel APK research then examined the real app's own connection
# code and found it sends exactly three headers: the two authorizer
# fields and x-irobot-auth. No fourth. The hypothesis is disproven.
#
# It was removed in a23 -- not because it was proven harmful, but
# because it shipped to every consumer of this library, Home Assistant
# included, in the same release that broke Prime setup there. An
# unvalidated experiment does not belong in that path.

def _suback_is_failure(reason_code: Any) -> bool:
    """True if a SUBACK reason code means the broker REFUSED the
    subscription.

    REAL CRASH FOUND IN THE FIELD (DaRealGuGu, v0.1.11a22): the first
    version of this check did `int(rc) >= 0x80`. With paho-mqtt 2.x the
    callback receives `ReasonCode` OBJECTS, not ints, and `int()` on one
    raises TypeError -- inside paho's own network thread, which killed
    the client, triggered an endless reconnect loop, and made every
    subsequent shadow read and PUBACK time out.

    The damage was worse than a crash: the resulting "broker did NOT
    confirm receipt (no PUBACK)" was reported to the tester as evidence
    of a policy-level block. It was our own bug. Three stages of their
    test run produced a confident, wrong diagnosis.

    Hence: no int() coercion, no assumption about the type. Prefer
    paho's own `is_failure`, fall back to `.value`, fall back to a bare
    int, and if none of that works treat it as NOT a failure -- a
    missed rejection is a far smaller harm than crashing the MQTT
    thread again."""
    is_failure = getattr(reason_code, "is_failure", None)
    if isinstance(is_failure, bool):
        return is_failure
    raw = getattr(reason_code, "value", reason_code)
    try:
        return int(raw) >= 0x80
    except (TypeError, ValueError):
        _LOGGER.debug("roombapy-prime: unrecognized SUBACK reason code %r", reason_code)
        return False


class ShadowError(Exception):
    """Raised when a shadow operation is rejected or times out.

    Subclassed below (this session, ha_roomba_plus translation-key
    prep) -- see auth.py's AuthError docstring for the same reasoning:
    callers that only care about "something failed" keep catching
    ShadowError itself, callers that need to distinguish categories
    for translation-key mapping catch the specific subclass."""


class SubscriptionRejectedError(Exception):
    """NEW (this session) -- raised when the broker's own SUBACK
    reason code says a subscribe() call was REJECTED (MQTT's 0x80
    failure code, typically an IoT-policy/ACL denial in AWS IoT's
    case), as opposed to genuinely subscribing successfully and simply
    seeing no traffic afterward. Deliberately a SEPARATE exception type
    from ShadowError (this isn't about a shadow operation specifically,
    and every existing subscribe() caller across this whole library --
    watch_state(), watch_mission_timeline(), watch_rejected_commands(),
    watch_raw_topic(), and anything built on top of them -- gets this
    new distinction for free, without deliberately catching a
    shadow-specific exception type for a subscribe-level problem)."""


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


def _publish_confirmed(
    info: Any, topic: str, timeout: float = 5.0, disconnect_reason: str | None = None
) -> None:
    """Raises unless the broker actually took the message.

    A publish that never leaves and a robot that never answers look the
    same from the caller: silence, then a timeout. paho reports the
    difference in the return code and in `is_published()`, and both were
    being discarded.
    """
    # A client that returns nothing at all is a stand-in, not a broker.
    # Refusing here would fail tests rather than find bugs.
    if info is None:
        return
    rc = getattr(info, "rc", None)
    if rc is not None and rc != mqtt.MQTT_ERR_SUCCESS:
        # WHY THE SOCKET DIED, when we know it.
        #
        # `rc=4` is paho's MQTT_ERR_NO_CONN -- it says the connection was
        # gone at publish time, not why. The broker's own reason arrives
        # earlier, on disconnect, and this library recorded it and never
        # showed it.
        #
        # @utkjmitch's run is why that matters: CONNACK, then no SUBACK
        # on the shadow topics, then rc=4 -- while cmd-topic publishes on
        # the SAME session went through and the robot obeyed them.
        # Subscribes dead, publishes alive. A broker that drops a client
        # for an unauthorised subscribe looks exactly like that, and its
        # disconnect reason would say so.
        why = f" The broker's last disconnect reason was: {disconnect_reason}." if disconnect_reason else ""
        raise ShadowError(
            f"PUBLISH to {topic} was refused by the client (paho rc={rc}) -- "
            f"the request never left, so a timeout below would mean nothing.{why}"
        )
    try:
        info.wait_for_publish(timeout=timeout)
    except (RuntimeError, ValueError) as exc:
        raise ShadowError(
            f"PUBLISH to {topic} could not be confirmed: {exc}"
        ) from exc
    if not info.is_published():
        raise ShadowError(
            f"PUBLISH to {topic} was queued but never sent within {timeout}s -- "
            "the connection accepts messages and is not delivering them."
        )


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
        #: Topics this client has subscribed to and not released, so a
        #: repeat read does not re-subscribe to what the broker already
        #: granted. Cleared on disconnect, because a new session starts
        #: with none of them.
        self._subscribed_topics: set[str] = set()
        # REAL BUG FOUND AND FIXED (this session, prompted directly by a
        # field result: chairstacker triggered a favorite AND a room
        # clean from the real app -- the robot genuinely reacted to
        # both within 20 seconds -- while our OWN --watch-wildcard
        # subscription, covering the robot's entire topic tree, saw
        # NOTHING at all during that exact window). _on_subscribe()
        # received the broker's SUBACK reason code for every single
        # subscribe() call this library has ever made, but NEVER
        # CHECKED it -- any subscription, successful OR actively
        # REJECTED by the broker's IoT policy (MQTT's own 0x80 failure
        # code exists for exactly this), was recorded identically as
        # "confirmed". A silently rejected subscription and a genuinely
        # empty topic look completely identical from the caller's side
        # without this check -- exactly the ambiguity chairstacker's
        # result could not resolve on its own. _mid_to_topic and
        # _subscribe_failures close this gap; see _on_subscribe()'s own
        # docstring for the exact mechanism.
        self._mid_to_topic: dict[int, str] = {}
        self._subscribe_failures: dict[int, Any] = {}
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

        #: Set while WE are taking the connection down on purpose.
        #:
        #: @ratpic83 (2026-08-16) logged 26 disconnects in one day, each
        #: exactly 55 minutes after the last, and the ordering shows the
        #: cause: authenticate, reconnect, THEN the drop is reported.
        #: The "Normal disconnection" is paho's reason string for a
        #: clean client-initiated close -- ours, from reconnect()'s own
        #: disconnect() call.
        #:
        #: Without this flag the watcher in prime_robot.py sees a
        #: planned disconnect as an unexplained drop, warns about it,
        #: and starts a SECOND reconnect racing the one already running.
        self._deliberate_disconnect = False
        self._disconnect_reason: str | None = None
        #: Counts reconnects so each gets its own client id.
        self._reconnects = 1

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
                # NO User-Agent, deliberately. One was added in a22 as an
                # experiment, on a third-party project's claim that AWS
                # IoT's authorizer inspects it. The parallel APK research
                # then DISPROVED that: the real app sends exactly the three
                # headers above and no fourth.
                #
                # It shipped to every consumer, including Home Assistant,
                # in the same release that broke Prime setup there. Whether
                # it contributed is unknown and now moot -- an unvalidated,
                # since-disproven experiment has no business in the
                # connection path of an integration people actually run.
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
        comment on _confirmed_mids for the bug this fixes.

        NOW ALSO RECORDS FAILURE REASON CODES (this session) -- a
        SUBACK isn't inherently a success signal. MQTT's own protocol
        has a dedicated failure code (0x80/128) for exactly this case:
        the broker accepted the SUBSCRIBE packet but the requested
        topic was denied (by IoT policy/ACL, in AWS IoT's case) --
        distinct from "granted at QoS 0/1/2" (success). Every reason
        code >= 0x80 is stored in _subscribe_failures, keyed by mid, so
        _subscribe_and_wait() (below) can raise a clear, specific error
        instead of silently treating a REJECTED subscription exactly
        the same as a successful, simply-quiet one."""
        # NOTHING in this callback may raise: it runs on paho's own
        # network thread, and an exception here takes the whole MQTT
        # client down with it (found the hard way -- see
        # _suback_is_failure's docstring).
        try:
            self._confirmed_mids.add(mid)
            failed_codes = [rc for rc in reason_codes or () if _suback_is_failure(rc)]
            if failed_codes:
                self._subscribe_failures[mid] = failed_codes
        except Exception:  # noqa: BLE001
            _LOGGER.exception("roombapy-prime: error handling SUBACK -- ignoring, connection kept alive")

    def _subscribe_and_wait(self, topics: list[str], timeout: float = 3.0) -> None:
        """NEW (session 33) -- subscribes to all given topics and waits
        for the SUBACK of EACH ONE before returning. The actual fix for
        the race described in get_shadow()/update_shadow() -- publish()
        must only happen after this. `timeout` deliberately short
        (SUBACKs are usually very fast, unlike the actual shadow
        response) -- if this timeout runs out, the subscription is
        RECORDED AS UNCONFIRMED and warned about, but not treated as a
        failure: some deployed Prime sessions deliver shadow traffic
        without a visible SUBACK.

        THIS PARAGRAPH WAS STALE (corrected via @jouwdan, PR #62). It
        still read "proceeds anyway (better a small residual risk than a
        broken library)", describing behaviour that had already been
        replaced by the recording and warning below. A docstring that
        describes the previous version of its own function is worse than
        no docstring: it was read as current by at least one person
        writing a fix against it.

        NOW RAISES SubscriptionRejectedError on a REJECTED subscription
        (this session) -- see _on_subscribe()'s own docstring for the
        full finding. Previously proceeded identically whether the
        broker granted or actively denied the subscription.

        NOW ALSO REVIVES A DEAD CONNECTION FIRST (this session), the
        same way get_shadow() and publish_cmd_payload() do. This was
        the last operation in the module still using the client without
        checking it was alive -- and the most damaging one to get
        wrong, because subscribing to a dead connection fails silently:
        the watcher then observes nothing at all, and a real robot
        reaction gets reported as "nothing happened".

        Field logs showed exactly this ordering: subscribe, then a
        shadow GET timing out, then a publish with no PUBACK -- three
        symptoms of one dead connection, of which only the middle one
        was visible as an error."""
        if self._client is None or not self._connected:
            # Was a bare assert -- see reconnect()'s own note on why
            # that is the wrong tool here.
            self.reconnect(timeout=timeout)
        mids = []
        not_sent: dict[str, int] = {}
        for topic in topics:
            result, mid = self._client.subscribe(topic, qos=1)
            # THE RETURN CODE WAS DISCARDED. paho answers MQTT_ERR_NO_CONN
            # when the client is not connected, and then no SUBSCRIBE
            # packet leaves at all -- no SUBACK follows, the wait below
            # times out, and this function returned as if everything had
            # worked. The caller then watches a topic it never
            # subscribed to, forever, with nothing anywhere saying so.
            if result != mqtt.MQTT_ERR_SUCCESS:
                not_sent[topic] = result
                continue
            mids.append(mid)
            self._mid_to_topic[mid] = topic
        waited = 0.0
        while waited < timeout and not all(m in self._confirmed_mids for m in mids):
            time.sleep(0.05)
            waited += 0.05
        # UNCONFIRMED IS NOT THE SAME AS GRANTED, and this loop used to
        # treat it that way: "proceeds anyway, better a small residual
        # risk than a broken library". The residual risk is a watcher
        # that reports nothing for the rest of its life, and it is
        # indistinguishable from a quiet robot.
        #: Kept so the caller can re-check after the wait expires --
        #: see resubscribe_still_unconfirmed().
        self._last_subscribe_mids = list(mids)
        unconfirmed = [
            self._mid_to_topic.get(m, "?") for m in mids
            if m not in self._confirmed_mids
        ]
        self.last_subscribe_unconfirmed = unconfirmed
        self.subscribe_unconfirmed_count = (
            getattr(self, "subscribe_unconfirmed_count", 0) + len(unconfirmed)
        )
        if unconfirmed:
            # THE BROKER'S REASON, IF IT GAVE ONE.
            #
            # Three testers hit this on three accounts with three
            # different symptoms -- no SUBACK, publish queued but never
            # sent, publish refused with rc=4 -- and @utkjmitch's
            # half-alive session ties them together: shadow subscribes
            # dead, cmd-topic publishes working, same connection.
            #
            # A broker that drops a client for an unauthorised subscribe
            # produces exactly that, and it says why on disconnect. This
            # warning is the first place anyone notices, so it is the
            # right place to carry the reason.
            _LOGGER.warning(
                "roombapy-prime: no SUBACK within %.1fs for %s -- proceeding, but a "
                "subscription that was never acknowledged delivers nothing and looks "
                "exactly like a robot with nothing to say.%s",
                timeout, unconfirmed,
                f" Last disconnect reason from the broker: {self._disconnect_reason}."
                if self._disconnect_reason else
                " The broker has not reported a disconnect, so the socket is"
                " probably still open and the subscription simply unanswered.",
            )
        rejected = {self._mid_to_topic.get(m, "?"): self._subscribe_failures.pop(m)
                    for m in mids if m in self._subscribe_failures}
        for m in mids:
            self._confirmed_mids.discard(m)
            self._mid_to_topic.pop(m, None)
        if not_sent:
            raise SubscriptionRejectedError(
                f"SUBSCRIBE was never sent for {not_sent} (paho error codes) -- the "
                "client reported a failure before anything reached the broker. "
                "Distinct from a rejection: the broker never saw this."
            )
        if rejected:
            raise SubscriptionRejectedError(
                f"Broker REJECTED subscription (SUBACK failure code) for: {rejected}. "
                "This is a different, more specific finding than 'nothing arrived' -- "
                "the broker's own IoT policy denied this topic outright, not a silent "
                "absence of traffic on it."
            )

    def connect(self, timeout: float = 10.0) -> None:
        # Logged because a same-client_id collision is invisible
        # otherwise, and its symptoms look like anything but what they
        # are.
        #
        # AWS IoT disconnects the OLDER connection when a second one
        # arrives using the same client_id. If two consumers of this
        # library talk to one robot at once -- a Home Assistant
        # integration and a diagnostic script, say -- and the server
        # hands out the same client_id to both, they take turns
        # evicting each other indefinitely. From each side that looks
        # like an unexplained drop, not like a conflict.
        #
        # THE THREE-ACCOUNT PATTERN FITS THIS, and nothing else fits it
        # as well. @utkjmitch's session was HALF ALIVE: shadow
        # subscribes dead, cmd-topic publishes working, robot physically
        # obeying -- one connection, one moment. An eviction produces
        # exactly that, because the socket dies between CONNACK and the
        # first SUBACK and paho only notices at the next publish. A
        # subscribe always loses that race; a bare publish fired quickly
        # enough wins it.
        #
        # It also explains why a FIRST read sometimes succeeds and every
        # later one fails (@jouwdan: 21 keys, then nothing), which an
        # IoT-policy denial would not -- a policy denies every time.
        #
        # WHAT WOULD SETTLE IT COSTS NOTHING: run the check with the
        # iRobot phone app fully closed, and with Home Assistant's own
        # integration stopped if it points at the same robot. If the
        # read then works, the wall is an eviction and not a protocol
        # question at all.
        #
        # SUSPECTED, NOT CONFIRMED (this session): a tester's Home
        # Assistant sensors froze across two separate coordinators at
        # once, during a period when he was running command-line tests
        # against the same robot. Two independent data paths stopping
        # together points at the connection rather than at either
        # sensor. Whether this server issues a stable client_id per
        # account is not established -- hence logging it rather than
        # asserting anything.
        _LOGGER.debug(
            "roombapy-prime: connecting blid=%s with client_id=%s", self._blid, self._token.client_id
        )
        self._client = self._build_client()
        try:
            # keepalive=60 (paho's own default), LOWERED FROM 300 this
            # session. MQTT declares a connection dead after 1.5x the
            # keepalive interval, so 300 meant a broken connection went
            # unnoticed for up to 450 SECONDS -- and during that window
            # publish() succeeds locally while nothing reaches the
            # broker, which is exactly the "no PUBACK, no error" state
            # three field sessions kept producing.
            #
            # 60 costs one small PINGREQ per minute and cuts that blind
            # window to about 90 seconds. AWS IoT accepts anything from
            # 30 upward, so this stays well inside spec.
            # RAISE THE IN-FLIGHT LIMIT. paho defaults to 20
            # unacknowledged QoS-1 messages; the iRobot app sets 1000.
            #
            # This matters for `_subscribe_and_wait`, which subscribes
            # to every persistent topic in one loop and then waits for
            # each SUBACK. A restore carrying more than twenty topics
            # would have paho queue the rest behind the window --
            # arriving late, or looking like the "no SUBACK within
            # 3.0s" that @utkjmitch sees on every reconnect.
            #
            # Not claimed as the cause of that: his robot has four
            # persistent subscriptions, well under twenty. But the
            # app's own value is the safer default, and it costs
            # nothing.
            #
            # Found in samm-git/irobot-explore's reconstruction, which
            # documents the app's connection parameters.
            self._client.max_inflight_messages_set(1000)
            self._client.connect(self._endpoint, port=443, keepalive=60)
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

    def disconnect(self, deliberate: bool = True) -> None:
        """`deliberate` marks this as our own close, so the watcher does
        not report it as a drop and does not start a competing
        reconnect. Defaults True: every caller of this method is
        choosing to disconnect."""
        self._deliberate_disconnect = deliberate
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
            # The caller just handed us a token; its client id is the one
            # to use. Rotating it here would discard what they chose.
            self.reconnect(timeout=timeout)

    def reconnect(self, timeout: float = 10.0) -> None:
        """Reconnects with the same client id, which is the only one
        that works -- see the note in the body.

        THE EVICTION MAY BE OURS. @DaRealGuGu's 0.3.0b1 run, with the
        phone app closed and Home Assistant stopped, still failed -- and
        the log shows why it could not have been the phone:

            08:19:14  reconnecting (0 persistent subscriptions)
            08:19:14  connecting ... client_id=app-roombapy-prime-XQWR87YE0
            08:19:18  no SUBACK ... disconnect reason: Unspecified error

        **The same client id, twice.** AWS IoT drops the older
        connection when a second arrives using it, and if the broker
        still holds the first session, our own reconnect is the second
        one. Nothing external needs to be running for that.

        A fresh id per reconnect costs nothing -- the id is ours to
        choose, and nothing depends on it staying the same across a
        reconnect.
        """
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
        if self._client is None:
            # Was an assert, which surfaced to a field tester as a bare
            # AssertionError traceback ending in "call connect() first" --
            # a message written for whoever wrote the calling code, not
            # for the person running a diagnostic script. It also fired
            # from get_shadow()'s lazy-reconnect path, so the visible
            # failure was several frames away from the actual cause.
            raise ShadowError(
                "Not connected. This client needs connect() to have been called at least "
                "once before any shadow read -- named shadows travel over MQTT, not REST. "
                "If you are running one of the diagnostic scripts, this is a bug in the "
                "script rather than anything you did: it asked for shadow data without "
                "opening the connection first."
            )
        _LOGGER.info(
            "roombapy-prime MQTT: reconnecting (%d persistent subscription(s) to restore)",
            len(self._persistent),
        )
        topics_to_restore = list(self._persistent.keys())

        self.disconnect()
        self._connected = False
        # THE CLIENT ID IS NOT OURS TO CHOOSE. Tried in b2, reverted in
        # b3, and the failure was informative.
        #
        # b2 rotated it on every reconnect on the theory that we were
        # evicting ourselves. @DaRealGuGu's run made things worse in a
        # specific way: the connection stopped being dropped after a
        # subscribe and started **failing outright** --
        # "Connect timed out after 8.0s", every time, on ids like
        # `...-r1-r2-r3-r4-r5-r6`.
        #
        # Two things were wrong. The id accumulated rather than being
        # replaced, which is a plain bug. But the useful part is that a
        # derived id does not connect AT ALL: the id comes from iRobot's
        # login response, and the broker's policy evidently expects that
        # one. It is issued, not chosen.
        #
        # So the eviction theory is not disproven -- but rotating the id
        # is not the way to test it, and cannot be the fix.
        self._reconnects += 1
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
        # A NEW SESSION GRANTS NOTHING. Keeping the set across a
        # disconnect would make the next read skip a subscription it no
        # longer has -- the exact silence this change exists to avoid.
        self._subscribed_topics.clear()
        self._disconnect_reason = (
            "deliberate: token refresh or reconnect"
            if self._deliberate_disconnect
            else str(reason_code)
        )
        self._was_deliberate = self._deliberate_disconnect
        self._deliberate_disconnect = False
        if self._disconnect_loop is not None and self._disconnect_event is not None:
            self._disconnect_loop.call_soon_threadsafe(self._disconnect_event.set)

    def resubscribe_still_unconfirmed(self) -> list[str]:
        """Topics with no SUBACK, re-checked now rather than at the
        moment the wait expired.

        A SUBACK ARRIVING LATE IS STILL A SUBACK. `_confirmed_mids` is
        filled by paho's callback thread and keeps filling after the
        3-second wait gives up, so `last_subscribe_unconfirmed` is a
        snapshot of a deadline, not a verdict.

        @utkjmitch (b7, second household): every reconnect on his
        instance logs `no SUBACK within 3.0s`, on a 55-minute cycle.
        Treating that snapshot as failure would put him into a
        reconnect loop every cycle -- for subscriptions that may well
        have been acknowledged a moment later.

        So the caller asks again before acting. Anything still missing
        here really is missing.
        """
        mids = getattr(self, "_last_subscribe_mids", None) or []
        return [
            self._mid_to_topic.get(m, "?") for m in mids
            if m not in self._confirmed_mids
        ]

    @property
    def last_disconnect_was_deliberate(self) -> bool:
        """True when the last disconnect was our own reconnect or token
        refresh, rather than something the broker or network did."""
        return self._was_deliberate

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
            # A CALLBACK THAT RAISES KILLS PAHO'S NETWORK LOOP THREAD,
            # and the connection then looks alive while delivering
            # nothing: publishes queue and are never sent, subscribes
            # get no SUBACK.
            #
            # That is exactly what two testers reported. @jouwdan's
            # first read listed 21 keys, and every operation after it
            # failed -- write, then read, both with "no SUBACK".
            # @DaRealGuGu's b16 run reported "PUBLISH was queued but
            # never sent", which is the same connection in the same
            # state seen from the other side.
            #
            # This does not prove a callback raised on their accounts.
            # It removes the only way one could take the whole client
            # down without saying so.
            try:
                cb(response)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "roombapy-prime: a shadow callback raised for %s -- "
                    "the message is lost, the connection is not",
                    msg.topic,
                )
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
                    # Same guard, same reason. Persistent subscribers are
                    # the watchers -- mission timeline, live map -- and a
                    # watcher that raises would take down the client that
                    # feeds every other call.
                    try:
                        cb(response)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "roombapy-prime: a watcher raised for %s -- "
                            "the message is lost, the connection is not",
                            msg.topic,
                        )

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

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mqtt_clientcmd_topic
    """
        return f"{irbt_topic_prefix}/things/{self._blid}/cmd"

    def mission_timeline_topic(self, irbt_topic_prefix: str, *, report: bool = True) -> str:
        """NEW (this session). Found via native decompilation
        (libcorebase.so's core::protocol::AssetIotTopicFactory::
        createMissionTimelineTopic(IotTopicType), a sibling method of
        the SAME factory/constructor as createCommandPublishTopic() --
        the already-live-confirmed source of cmd_topic() above.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mqtt_clientmission_timeline_topic
    """
        direction = "report" if report else "request"
        return f"{irbt_topic_prefix}/things/{self._blid}/mission/timeline/{direction}"

    def dock_report_topic(
        self, irbt_topic_prefix: str, report_type: str | None = None
    ) -> str:
        """A dock report topic in the `dock/{reportType}/report` family.

        `dock/paddry/report` is CONFIRMED LIVE (chairstacker) -- it
        fired right after a mission's `start`, carrying the dock's
        lifetime stats rather than a live pad-dry state. The topic name
        implies a family, `paddry` being the one `reportType` seen so
        far; a `charge` or `battery` sibling would be the real find and
        has not been observed.

        With no `report_type`, returns a single-level `+` wildcard so a
        caller can subscribe the whole family without knowing which
        types exist -- which is the only way to discover a sibling.

        `evac/report` sits one level up (`evac`, not `dock/evac`), so it
        is deliberately NOT covered by this builder; use watch_raw_topic
        for that one.
        """
        segment = report_type if report_type else "+"
        return f"{irbt_topic_prefix}/things/{self._blid}/dock/{segment}/report"

    def request_mission_timeline(
        self, irbt_topic_prefix: str, request_id: int
    ) -> bool:
        """Asks the robot to send its mission timeline now.

        THE TIMELINE DOES NOT HAVE TO BE WAITED FOR. This library
        subscribed to `mission/timeline/report` and took whatever
        arrived, which meant a caller wanting the current mission's
        progress waited for the robot to volunteer it.

        **THE REQUEST IS ACCEPTED AND AN IDLE ROBOT DOES NOT ANSWER.**

        @DaRealGuGu confirmed the publish goes through. @jouwdan then
        watched for one: a single MQTT connection carrying both the
        subscription and the request -- so no second client to evict --
        with the phone app closed and Home Assistant stopped. The
        publish was accepted; **no report arrived in 35 seconds** on a
        robot that was idle and stayed idle.

        That is a clean negative, not an inconclusive one. It points at
        reports being tied to a mission: published during one, or after
        it, rather than on demand at rest.

        WHAT IT DOES NOT SETTLE: whether a request during a mission
        pulls a report earlier than the robot would have sent one
        anyway. That needs a watcher running while the robot drives, and
        nobody has done it.

        `MissionTimelineManager.getEncodedRequest()` publishes
        `{"timelineRequestId": <n>}` to the matching `request` topic, and
        the report comes back carrying the same id -- which is what
        `MissionTimelineDto.timelineRequestId` is for.

        **A RUNNING COUNTER, NOT A RANDOM VALUE.** The app starts at 1
        and increments; a caller that reuses an id cannot tell which
        report answered which request.
        """
        topic = self.mission_timeline_topic(irbt_topic_prefix, report=False)
        payload = json.dumps({"timelineRequestId": request_id}).encode()
        info = self._client.publish(topic, payload=payload, qos=1)
        _publish_confirmed(info, topic)
        return True

    #: CHECKED AGAINST APP 3.0.0: the gap is one topic, not nine.
    #:
    #: 3.0.0 uses exactly these:
    #:
    #:     irbt   things/{id}/cmd
    #:            things/{id}/livemap/update
    #:            things/{id}/mission/timeline/{report,request}
    #:            things/{id}/editv3_req + editv3_resp     <- not built
    #:     aws    things/{id}/get/accepted, shadow/...
    #:     other  users/{userId}/event                      <- not built
    #:
    #: Everything in the 1.6.0 SDK log below is absent from 3.0.0:
    #: the four dock reports, filexfer, the old edit_req/_resp,
    #: mapdetails, matter. So the dock live-reports we wanted do not
    #: exist in this app version -- pad wash and evacuation stay
    #: after-the-fact timeline events.
    #:
    #: `users/{userId}/event` is a message centre, and new: a
    #: user-scoped topic rather than a thing-scoped one. Nobody has
    #: asked for it.
    #:
    #: Prefixes come from `TopicResolver`: `{awsPrefix}/{identifier}`
    #: and `{irbtPrefix}/{identifier}`, per deployment.
    #:
    #: THE LOCAL CHANNEL WAS REAL, AND IT IS GONE.
    #:
    #: Three app versions, checked:
    #:
    #:     2.2.4   native C++/Djinni. **46 local-socket serializers**,
    #:             `irobotmcs` x2, port 5678 x24. Authenticate, control,
    #:             drive, get position, set preferences, set suction --
    #:             a complete local API.
    #:     1.6.0   samm-git/irobot-explore implements local MQTT
    #:             control against it.
    #:     3.0.0   Flutter/Dart. Zero hits for any of it.
    #:
    #: So the local path is not something iRobot never had. It existed,
    #: it was thorough, and the APP stopped using it.
    #:
    #: THE ROBOTS DID NOT. Reported August 2026 on firmware
    #: `p25-705+9.3.6+I3.8.149` -- current -- by the author of
    #: samm-git/irobot-explore: the channel still works, and opens by
    #: starting the BLE Wi-Fi provisioning flow and stopping before
    #: sending any values. The robot beeps and local MQTT comes up.
    #:
    #: No physical button and no auto-test mode: it comes up as part of
    #: a flow the app itself runs.
    #:
    #: An earlier version of this comment said the local path "was
    #: removed", from decompiling three app versions. The app evidence
    #: was right and the conclusion overreached -- an app dropping a
    #: path says nothing about the firmware behind it, which is the
    #: exact distinction the verify-local-channel tool was built around
    #: and which this comment then failed to apply to itself.
    #:
    #: AND IT WOULD NOT HAVE SOLVED `async-dependency` ANYWAY.
    #:
    #: samm-git's `--local` still logs in to the cloud once, to fetch
    #: the robot's local password -- `/v2/login` returns it as
    #: `robots[blid].password`, and there is no other way to get it. A
    #: local transport removes the round trip, not the dependency.
    #:
    #: Worth stating plainly because this project described a local
    #: path as "the most interesting answer to async-dependency" more
    #: than once. It is interesting for latency and for working while
    #: the cloud is down mid-session. It is not a cloud-free client.
    #:
    #: We already receive that password on every login
    #: (`RobotLoginEntry.password`) and have never used it.
    #:
    #: 2.2.4 also carries `mission/rrtp/request` and
    #: `mission/rrtp/report/update`, whose symbol names
    #: (`kMessageTopicForLocalRrtpRequest`) mark them LOCAL. Neither
    #: survives into 3.0.0.
    #:
    #: TOPICS FROM THE 1.6.0 RECONSTRUCTION, kept for the record.
    #:
    #: samm-git/irobot-explore's SDK log shows the robot subscribing to
    #: more than we build topics for:
    #:
    #:     /evac/report              bin evacuation
    #:     /dock/refill/report       fresh-water refill
    #:     /dock/padwash/report      pad wash
    #:     /dock/paddry/report       pad dry
    #:     /filexfer_req + _resp     log and map upload
    #:     /edit_req + /edit_resp    map editing (we use the REST path)
    #:     /mapdetails/req + /resp   map details
    #:     /matter/certificate/req   Matter commissioning
    #:     /matter/fabric/req
    #:
    #: The dock ones matter most, and one is no longer a guess.
    #: `dock/paddry/report` is CONFIRMED LIVE (chairstacker) -- it fired
    #: right after a mission's `start`, and its payload is modelled as
    #: DockReport (nee DockPadDryReport). So samm-git's SDK-log list and
    #: our own capture agree on it: the topics are real, not just names
    #: in a decompiled app that never appeared on the wire.
    #:
    #: An earlier version of this comment filed these as "not built" and
    #: elsewhere as settled-dead, on the grounds that no report topic
    #: appears in app 2.2.4 or 3.0.0. That was the wrong test: the app
    #: not carrying a topic says nothing about whether the robot
    #: publishes on it, and this one demonstrably does.
    #:
    #: NOW BUILT: dock_report_topic() constructs this family (with a `+`
    #: wildcard for discovery), and PrimeRobot.watch_dock_reports()
    #: subscribes it. The open question that method exists to answer:
    #: whether a `reportType` other than `paddry` -- a `charge` or
    #: `battery` sibling -- ever arrives. None has been seen yet.
    #:
    #: Still not built: evac/refill/padwash specifically. Their payload
    #: shapes are unseen, and this library has been burned modelling a
    #: response nobody captured (`time_estimates`, replaced wholesale).
    #: But watch_dock_reports() with no argument would catch refill and
    #: padwash too, since both are `dock/{type}/report` -- so a capture
    #: is now one subscription away rather than needing new code.
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
        here now CONFIRMED (same decompiled call-site evidence -- see
        mission_timeline_topic()'s own entry in docs/internal/EVIDENCE_TRAIL.md), payload shape
        unknown."""
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

    def publish_cmd(self, irbt_topic_prefix: str, command: str, initiator: str = "localApp") -> bool:
        """NEW (session 39). Publishes a simple mission command via
        cmd_topic() -- see that method's docstring for the full
        evidence trail. Payload shape {"command": str, "time": int,
        "initiator": str} matches the third-party project's
        documented, reportedly-working format exactly -- "time" is a
        Unix timestamp in SECONDS (not millis).

        CORRECTED (this session): this docstring used to say "initiator"
        defaults to "localApp" here. It does not -- this method adds
        only "time". Callers that want an initiator must put it in the
        payload themselves, and send_simple_command() does. Region
        commands built from a stored favorite do NOT, because a stored
        favorite carries no initiator.

        That distinction may matter a great deal. A field run on a24
        (DaRealGuGu) had stage 1 -- an unchanged favorite, no initiator
        -- do nothing, while stage 1b -- the identical command with
        initiator="rmtApp" added -- started a mission. The APK research
        independently found that the real app's buildJsonCommon() always
        writes initiator. Awaiting his full log to check whether stage 1
        was actually delivered before treating this as settled.

        NOW RETURNS whether the broker confirmed PUBACK receipt (this
        session) -- see publish_cmd_payload()'s own entry in docs/internal/EVIDENCE_TRAIL.md for the
        full reasoning on why this is a genuinely new, useful signal,
        separate from any application-level acknowledgment. Callers
        who want confirmation that the ROBOT itself reacted should
        still poll get_state() afterward -- this only confirms the
        BROKER received the publish, not that the robot acted on it."""
        return self.publish_cmd_payload(irbt_topic_prefix, {"command": command, "initiator": initiator})

    def publish_cmd_payload(
        self, irbt_topic_prefix: str, payload: dict[str, Any], *, confirm_timeout: float = 5.0,
    ) -> bool:
        """NEW (session 46). Lower-level sibling of publish_cmd() --
        publishes an ARBITRARY payload dict to cmd_topic(), adding a
        "time" field (Unix seconds) if not already present. Exists for
        prime_robot.py's send_routine_command_via_cmd_topic() -- see
        that method's docstring for why a richer payload than
        publish_cmd()'s simple {command, time, initiator} might also
        be accepted here, and for the significant, elevated risk
        caveat that comes with sending anything richer than the basic
        confirmed-working case to this topic.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mqtt_clientpublish_cmd_payload
    """
        if self._client is None:
            # Same class of problem as reconnect()'s old assertion: a
            # message for whoever wrote the calling code, surfacing to
            # whoever ran a diagnostic script.
            raise ShadowError(
                "Not connected. connect() must have been called before publishing a command "
                "-- commands go over MQTT. If you are running a diagnostic script, this is a "
                "bug in the script rather than anything you did."
            )
        # Revive a dead connection before publishing, exactly as
        # get_shadow() already does.
        #
        # FIELD EVIDENCE (DaRealGuGu, three consecutive sessions): the
        # FIRST send of every session got no PUBACK, while later sends
        # in the same session succeeded. The give-away is the ordering
        # in his logs -- the ro-currentstate GET times out FIRST, then
        # the publish gets no PUBACK, and only afterwards does paho
        # report the drops. In other words the connection was already
        # dead before the send, not killed by it.
        #
        # What kills it is the interactive pause: this tool prints a
        # large payload and waits for a human to read it and type y.
        # The connection sits idle through that, and with keepalive=300
        # a dead one is not noticed for up to 450 seconds. get_shadow()
        # survived this because it reconnects; publish did not, because
        # it only ever checked whether a client object existed at all.
        #
        # Publishing into a dead connection is the worst possible
        # failure here: it returns without error, produces no PUBACK,
        # and the script then reports "no delivery confirmation" as
        # though it were a finding about the payload.
        with self._client_lock:
            if not self._connected:
                _LOGGER.info(
                    "roombapy-prime: connection was not alive before publish -- reconnecting"
                )
                self.reconnect(timeout=confirm_timeout)

        topic = self.cmd_topic(irbt_topic_prefix)
        full_payload = {**payload}
        full_payload.setdefault("time", int(time.time()))
        msg_info = self._client.publish(topic, payload=json.dumps(full_payload), qos=1)
        try:
            msg_info.wait_for_publish(timeout=confirm_timeout)
            return msg_info.is_published()
        except (RuntimeError, ValueError):
            return False

    def subscribe(self, topic: str, callback: Callable[[ShadowResponse], None]) -> None:
        """Register a callback that fires on EVERY message on this topic,
        indefinitely (until unsubscribe() removes it) -- for continuous
        dispatch (shadow deltas, live-map/-position streams), as opposed
        to get_shadow()/update_shadow()'s one-shot wait-for-one-response
        pattern.

        Multiple callbacks on the same topic coexist fine (each gets
        every message) -- the broker-level subscribe only happens once,
        the first time this topic is used.

        Revives a dead connection first, like every other operation in
        this module -- a silently-failed subscribe means the caller
        watches nothing and reports a real robot reaction as
        "nothing happened"."""
        if self._client is None or not self._connected:
            self.reconnect()
        is_new_topic = topic not in self._persistent
        # SUBSCRIBE FIRST, REGISTER SECOND (@jouwdan, PR #62).
        #
        # The callback used to be appended before the broker-level
        # subscribe was attempted, so a failing subscribe left the topic
        # in `_persistent` with a callback and no subscription. The next
        # subscribe() for that topic then read `is_new_topic = False` and
        # skipped the broker call entirely -- permanently unsubscribed,
        # permanently registered, and indistinguishable from a robot
        # that simply says nothing.
        #
        # A session could not recover from that, which is why his
        # Max 705 stayed broken across retries rather than failing once.
        #
        # NOTE WHAT THIS IS NOT. Both his PR summary and the report
        # behind it describe a missing SUBACK as fatal. It is not, and
        # has not been since b3: an unconfirmed subscription is recorded
        # and warned about, never raised. What his broker hit was a real
        # rejection or a local paho failure -- and then this ordering
        # turned one failure into a dead session.
        if is_new_topic:
            # NEW (session 33): same confirmation as get_shadow()/
            # update_shadow(), for consistency -- the risk here is
            # milder (only a very early first message could be missed
            # in the brief gap, not "the one expected response" like
            # with get_shadow()), but it's worth not having the same
            # bug type in two places just because the symptoms show up
            # differently.
            self._subscribe_and_wait([topic])
        self._persistent.setdefault(topic, []).append(callback)

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
            if self._client is None:
                # Teardown path: with no client there is nothing to
                # unsubscribe from. Deliberately NOT a reconnect --
                # rebuilding a connection purely to tear it down again
                # would be absurd, and this runs from finally-blocks
                # where raising would mask the original error.
                return
            self._client.unsubscribe(topic)

    def get_shadow(self, named: str | None = None, timeout: float = 8.0) -> ShadowResponse:
        """Fetch current shadow state. named=None for the classic/unnamed
        shadow (confirmed working on all tested tiers so far); pass a
        specific name (e.g. "rw-settings") to try a named shadow — only
        confirmed to respond on SMART-tier robots, silent on EPHEMERAL.
        A ShadowError on timeout does not distinguish "doesn't exist for
        this tier" from "transient failure" — callers on EPHEMERAL-like
        devices should expect named-shadow timeouts as normal, not a bug.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mqtt_clientget_shadow
    """
        with self._client_lock:
            if not self._connected:
                self.reconnect(timeout=timeout)
            base = _shadow_base(self._blid, named)
            result: list[ShadowResponse] = []

            def _capture(resp: ShadowResponse) -> None:
                result.append(resp)

            if self._client is None:  # pragma: no cover - reconnect() guarantees this
                raise ShadowError("Connection unavailable after reconnect")
            topics = []
            for suffix in ("get/accepted", "get/rejected"):
                topic = f"{base}/{suffix}"
                self._pending.setdefault(topic, []).append(_capture)
                topics.append(topic)

            # ONLY SUBSCRIBE TO WHAT IS NOT ALREADY SUBSCRIBED.
            #
            # This subscribed on EVERY call and never unsubscribed, so a
            # second read of the same shadow in one session re-subscribed
            # to topics the broker had already granted -- work that
            # contributes nothing and can still fail.
            #
            # It did fail: @DaRealGuGu's second `rw-settings` read got no
            # SUBACK within three seconds and then no response within
            # eight, while the first read in the same session had worked.
            #
            # Removing the redundant call is not a retry and not a
            # heuristic -- it deletes a step rather than catching it. And
            # it keeps the SUBACK guard meaningful for the first
            # subscription, where an unacknowledged one really does mean
            # a topic that will deliver nothing.
            fresh = [t for t in topics if t not in self._subscribed_topics]
            if fresh:
                self._subscribe_and_wait(fresh)
                self._subscribed_topics.update(fresh)
            # THE PUBLISH IS CONFIRMED, not fired and forgotten.
            #
            # `publish()` returns a result code and a handle, and this
            # ignored both. A queued-but-unsent request produces exactly
            # the symptom @DaRealGuGu reported: no answer within eight
            # seconds, no error, nothing to distinguish "the robot has no
            # such shadow" from "we never asked".
            #
            # This is the same class of gap b12 closed for `subscribe`.
            # It was closed there and left open here, three lines apart.
            _publish_confirmed(
                self._client.publish(f"{base}/get", payload=b"", qos=1),
                f"{base}/get",
                disconnect_reason=self._disconnect_reason,
            )

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
        reasoning as get_shadow()'s own entry in docs/internal/EVIDENCE_TRAIL.md."""
        with self._client_lock:
            if not self._connected:
                self.reconnect(timeout=timeout)
            base = _shadow_base(self._blid, named)
            result: list[ShadowResponse] = []

            def _capture(resp: ShadowResponse) -> None:
                result.append(resp)

            if self._client is None:  # pragma: no cover - reconnect() guarantees this
                raise ShadowError("Connection unavailable after reconnect")
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
