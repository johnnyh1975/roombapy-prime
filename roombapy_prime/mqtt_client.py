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
  - Never let the MQTT client reconnect on its own (aiomqtt does not;
    paho's _reconnect_on_failure was switched off in 0.4.x) -- setup
    logic re-running on every reconnect became an effectively infinite
    reconnect loop.

Transport: aiomqtt since 0.5.0, on the event loop; paho-mqtt's own
network thread up to 0.4.x. See PrimeMqttClient's docstring.

Confirmed on EPHEMERAL (900-series), SMART-tier (i7-series) AND
Prime/V4 robots — the last of those by a dozen testers running region
cleans, schedule writes and map edits through this client. The native
strings that suggested the same shadow topic conventions apply
(ClassicThingShadowTopicFactory / NamedThingShadowTopicFactory in the
shared core) turned out to be right, which is worth recording: the
inference held, and the note claiming it was unverified outlived its
own confirmation.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import math
import ssl
import time
from dataclasses import dataclass
from json.decoder import JSONDecodeError
from typing import Any, NoReturn
from collections.abc import Callable

import aiomqtt
import paho.mqtt.client as mqtt
from aiomqtt import MqttCodeError, MqttError
from aiomqtt.exceptions import MqttConnectError

from .errors import CloudError, CloudErrorReason

from .auth import ConnectionToken, _ssl_diagnosis

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


class ShadowError(CloudError):
    """Raised when a shadow operation is rejected or times out.

    Subclassed below (this session, ha_roomba_plus translation-key
    prep) -- see auth.py's AuthError docstring for the same reasoning:
    callers that only care about "something failed" keep catching
    ShadowError itself, callers that need to distinguish categories
    for translation-key mapping catch the specific subclass.

    NO REASON OF ITS OWN. A plain ShadowError covers a refused publish,
    a missing connection, a timeout and a rejected shadow write alike,
    so every raise names its `reason` -- a test holds the library to
    that."""


class SubscriptionRejectedError(CloudError):
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
    shadow-specific exception type for a subscribe-level problem).

    `reason` is SUBSCRIPTION_REJECTED when the broker refused, and
    SUBSCRIPTION_NOT_SENT when the request never reached it."""

    reason: CloudErrorReason = CloudErrorReason.SUBSCRIPTION_REJECTED


class ShadowSSLError(ShadowError):
    """TLS/certificate verification failure -- see
    _raise_clear_ssl_error(). `reason` says which of three causes."""

    reason: CloudErrorReason = CloudErrorReason.SSL_UNVERIFIED


class ShadowConnectionError(ShadowError):
    """Could not establish the connection at all -- DNS failure,
    connection refused, or a socket-level timeout (paho-mqtt's connect()
    raises all of these as plain OSError subclasses, indistinguishable
    from each other in a way that would justify a more specific message).
    A CONNACK that never arrives is a separate case since 0.5.0: a
    ShadowError with reason TIMEOUT. Deliberately does NOT claim to know whether this is
    iRobot's fault or the caller's own network, same as
    AuthConnectionError/RestConnectionError."""

    reason: CloudErrorReason = CloudErrorReason.CONNECTION_FAILED


def _raise_clear_ssl_error(exc: ssl.SSLError) -> NoReturn:
    """Re-raise a TLS/certificate failure as a clear ShadowSSLError
    instead of letting the raw ssl module exception bubble up as an
    opaque error.

    NEW (V4/Prime prep, following the same fix in auth.py/rest_client.py
    -- but a genuinely different mechanism here, not just a copy-paste).
    The TLS handshake happens inside paho-mqtt's connect(), not in
    aiohttp, so a failure here never surfaces as aiohttp.ClientSSLError:
    connect() raises ssl.SSLError (or a subclass, e.g.
    SSLCertVerificationError) before any CONNACK. Since 0.5.0 aiomqtt
    runs that connect() and wraps the error; _raise_connect_error()
    unwraps it and hands it here.
    UNLIKE the aiohttp fix, this one is NOT based on a real captured
    failure in this project -- it's based on paho-mqtt's documented,
    stable connect() behavior, not a reverse-engineered assumption.
    Treat this path itself as reasoned-through, not live-confirmed,
    until an actual iRobot cert incident is caught here.

    SINCE 0.4.0 THE SAME DIAGNOSIS AS LOGIN AND REST
    (auth._ssl_diagnosis), replacing a fixed "almost always temporary,
    not your setup" that was wrong for a machine without a usable trust
    store. ssl.SSLCertVerificationError carries the same
    verify_message the diagnosis reads."""
    reason, message = _ssl_diagnosis(exc)
    raise ShadowSSLError(message, reason=reason) from exc


def _raise_clear_connection_error(exc: OSError) -> NoReturn:
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


def _abandon(client: aiomqtt.Client) -> None:
    """Closes what a failed connect may have left open.

    A CONNACK that arrives after aiomqtt stopped waiting still completes
    the connection -- and then nobody owns it, pinging the broker under
    the robot's client id (review finding; 0.4.x had it too). aiomqtt
    offers no close for a client whose __aenter__ failed, so this asks
    the paho client underneath directly. Best effort: it must not turn
    one failure into another."""
    # A CONNACK wait cancelled with its caller leaves aiomqtt's
    # `_connected` future cancelled, and paho's disconnect callback then
    # asks it for its exception -- raising CancelledError inside paho,
    # before the socket is closed (review finding). A fresh future makes
    # that callback return early, as it does for any connect that never
    # completed.
    connected = getattr(client, "_connected", None)
    if isinstance(connected, asyncio.Future) and connected.cancelled():
        with contextlib.suppress(RuntimeError):
            client._connected = asyncio.get_running_loop().create_future()  # noqa: SLF001
    paho_client = getattr(client, "_client", None)
    if paho_client is None:
        return
    try:
        paho_client.disconnect()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roombapy-prime: could not close an abandoned connection", exc_info=True)


def _raise_connect_error(exc: MqttError, timeout: float) -> NoReturn:
    """A failed connect, as the ShadowError it has always been.

    aiomqtt wraps every connect failure in MqttError. For a socket-level
    failure it does so `from None`, which hides the original exception
    from the traceback but keeps it as `__context__` -- and the original
    is what says whether this was a certificate problem, and which one.
    """
    original = exc.__cause__ or exc.__context__
    # FIRST: no CONNACK in time. aiomqtt raises it `from None` inside its
    # `except asyncio.TimeoutError`, so the context is a TimeoutError --
    # an OSError subclass, which the connection branch below would take
    # (review finding). A socket-level timeout carries its own text.
    if str(exc) == "Operation timed out":
        raise ShadowError(
            f"Connect timed out after {timeout}s", reason=CloudErrorReason.TIMEOUT
        ) from exc
    if isinstance(original, ssl.SSLError):
        _raise_clear_ssl_error(original)
    if isinstance(original, OSError):
        _raise_clear_connection_error(original)
    if isinstance(exc, MqttConnectError):
        raise ShadowError(
            f"Connect failed: {exc}", reason=CloudErrorReason.CONNECT_REFUSED
        ) from exc
    raise ShadowError(
        f"Connect failed: {exc}", reason=CloudErrorReason.CONNECTION_FAILED
    ) from exc


def _describe_disconnect(exc: BaseException | None) -> str | None:
    """The broker's or the socket's reason for a dropped connection, as
    text -- what `_disconnect_reason` has always carried."""
    if exc is None:
        return None
    return str(exc) or type(exc).__name__


class _DebugOnlyLogger(logging.Logger):
    """Hands everything paho and aiomqtt log to `target`, at DEBUG.

    aiomqtt always switches paho's logging on, and gives it the logger
    it is handed (review finding). paho then logs "failed to receive on
    socket" at ERROR on every connection reset, and a line per packet at
    DEBUG; aiomqtt adds "Unexpected message ID" with a traceback for a
    SUBACK nobody waits for any more. 0.4.0 never enabled paho's logger,
    and this library reports drops itself, with the broker's reason. So
    all of it goes to DEBUG: silent by default, and there for anyone who
    turns `roombapy_prime` up to debug."""

    def __init__(self, target: logging.Logger) -> None:
        super().__init__(target.name)
        self._target = target

    def isEnabledFor(self, level: int) -> bool:  # noqa: N802 - logging's name
        return self._target.isEnabledFor(logging.DEBUG)

    def _log(
        self,
        level: int,
        msg: object,
        args: Any,
        exc_info: Any = None,
        extra: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
    ) -> None:
        if self._target.isEnabledFor(logging.DEBUG):
            self._target._log(  # noqa: SLF001
                logging.DEBUG, msg, args, exc_info=exc_info, extra=extra,
                stack_info=stack_info, stacklevel=stacklevel + 1,
            )


#: Clean-up tasks nobody awaits, kept referenced until they finish.
_BACKGROUND: set[asyncio.Task[Any]] = set()


async def _close_quietly(client: aiomqtt.Client) -> None:
    try:
        await client.__aexit__(None, None, None)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roombapy-prime: could not close a discarded connection", exc_info=True)


def _discard_late_connection(
    client: aiomqtt.Client, enter: asyncio.Future[Any], closings: list[asyncio.Task[Any]]
) -> None:
    """A connect whose caller was cancelled, once it has finished anyway.

    aiomqtt runs paho's blocking connect in a worker thread, and a
    cancelled await does not stop that thread: the socket, TLS and
    WebSocket handshake complete, and the connection comes up with
    nobody holding it -- pinging the broker under the robot's client id
    (review finding, new in 0.5: `asyncio.to_thread` used to let a
    connect finish instead). So the connect is shielded, and whatever it
    produced is closed here."""
    if enter.cancelled():
        _abandon(client)
        return
    if enter.exception() is not None:
        _abandon(client)
        return
    task = asyncio.ensure_future(_close_quietly(client))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    closings.append(task)


class PrimeMqttClient:
    """One connection, one blid.

    ON AIOMQTT SINCE 0.5.0. Up to 0.4.x this class drove paho-mqtt's own
    network thread, and every public method was synchronous -- PrimeRobot
    called each one through `asyncio.to_thread`. The consequences are
    the long comments further down: a `threading.Lock` around the
    client, `call_soon_threadsafe` bridges for every event that had to
    reach the event loop, and callbacks that must never raise because an
    exception inside paho's thread took the whole connection down with
    it (`_suback_is_failure`'s docstring has the field report).

    aiomqtt runs the same paho client on the event loop instead: no
    thread, awaited SUBACKs and PUBACKs, and one task here that reads
    incoming messages and dispatches them. What goes over the wire is
    unchanged -- the WebSocket path, the three authorizer headers, TLS,
    MQTT 3.1.1, keepalive 60, the in-flight window of 1000 -- because
    aiomqtt hands all of it to the same paho code as before.

    Every method that talks to the broker is a coroutine now. The topic
    builders and the token timing stay plain methods.

    Disconnect detection: `wait_for_disconnect()` resolves when the
    message task ends, with the broker's reason. The reconnect-with-
    backoff loop lives one level up, in prime_robot.py's watch_state()
    -- this class detects and reports a drop, it does not retry on its
    own."""

    def __init__(self, token: ConnectionToken, endpoint: str, blid: str) -> None:
        self._token = token
        self._endpoint = endpoint
        self._blid = blid
        self._client: aiomqtt.Client | None = None
        self._connected = False
        #: Reads incoming messages and dispatches them; its end is the
        #: disconnect signal.
        self._pump_task: asyncio.Task[None] | None = None
        #: One-shot waiters (get_shadow/update_shadow), popped by the
        #: first message on their topic.
        self._pending: dict[str, list[Callable[[ShadowResponse], None]]] = {}
        # Separate from _pending: _pending is one-shot (popped on first
        # matching message, used by get_shadow/update_shadow). _persistent
        # is for continuous dispatch (see subscribe()/unsubscribe() below)
        # -- callbacks stay registered until explicitly removed, and
        # multiple callbacks per topic can coexist (reference-counted at
        # the broker-subscribe level, see unsubscribe()).
        self._persistent: dict[str, list[Callable[[ShadowResponse], None]]] = {}
        #: Topics this client has subscribed to and not released, so a
        #: repeat read does not re-subscribe to what the broker already
        #: granted. Cleared on disconnect, because a new session starts
        #: with none of them.
        self._subscribed_topics: set[str] = set()
        #: SUBSCRIBEs still waiting for their SUBACK, by topic. A SUBACK
        #: arriving after `_subscribe_and_wait()` stopped waiting still
        #: counts -- see resubscribe_still_unconfirmed(). Cancelled on
        #: disconnect: a new session answers none of them.
        self._subscribe_tasks: dict[str, asyncio.Task[Any]] = {}
        self._last_subscribe_topics: list[str] = []
        #: Topics of the last subscribe that had no SUBACK when the wait
        #: ended, and a running count across the session.
        self.last_subscribe_unconfirmed: list[str] = []
        self.subscribe_unconfirmed_count = 0
        # A shadow read or write registers waiters, subscribes, publishes
        # and waits, and a token swap tears the connection down and
        # builds a new one. Neither may run in the middle of the other.
        # An asyncio.Lock since 0.5.0: everything here runs on the event
        # loop now, where 0.4.x needed a threading.Lock for callers in
        # to_thread workers.
        self._lock = asyncio.Lock()
        #: One event per wait_for_disconnect() call, all set when the
        #: connection ends. 0.4.x kept ONE event, replaced by every call,
        #: so with several watchers only the last one armed ever heard
        #: of a drop (review finding).
        self._disconnect_waiters: list[asyncio.Event] = []
        #: Serialises reconnects: a lazy reconnect inside subscribe()
        #: and a token swap must not build two connections with the same
        #: client id -- AWS IoT then evicts one of them (review finding).
        self._reconnect_lock = asyncio.Lock()
        #: Counts successful connects. A watcher remembers the generation
        #: it watches, so a drop it did not see happen is still a drop
        #: (see wait_for_disconnect()), and a reconnect that waited for
        #: another can tell it already happened.
        self._generation = 0
        #: How each recent connection ended: (reason, deliberate), by
        #: generation.
        self._ends: dict[int, tuple[str, bool]] = {}
        #: Set by disconnect(), cleared by connect(). A closed client does
        #: not reconnect on its own: robot.disconnect() used to be undone
        #: at once by watch_live_map()'s re-subscribe, leaving a
        #: connection nobody owned under the robot's client id (review
        #: finding; 0.4.x too).
        self._closed = False
        #: Counts disconnect() calls: a connect() reopens the client only
        #: if none came after it.
        self._close_requests = 0
        self._reopened = asyncio.Event()
        #: Replaced and set whenever the connection comes up or the
        #: client is closed -- see wait_for_state_change().
        self._state_changed = asyncio.Event()
        #: A connect whose caller was cancelled and whose handshake may
        #: still be running, with the close scheduled for it. The next
        #: connect or close waits for it: two handshakes with one client
        #: id in flight at once end with AWS evicting one of them
        #: (review finding).
        self._late: tuple[asyncio.Future[Any], list[asyncio.Task[Any]], float] | None = None
        #: Every SUBSCRIBE of this connection still waiting for its
        #: SUBACK. Cancelled when the connection ends and never before:
        #: aiomqtt forgets a cancelled one, and its SUBACK arriving later
        #: is then logged as an error with a traceback (review finding).
        self._inflight: set[asyncio.Task[Any]] = set()

        #: Set while WE are taking the connection down on purpose.
        #:
        #: @ratpic83 (2026-08-16) logged 26 disconnects in one day, each
        #: exactly 55 minutes after the last, and the ordering shows the
        #: cause: authenticate, reconnect, THEN the drop is reported.
        #: That drop was ours, from reconnect()'s own disconnect().
        #:
        #: Without this flag the watcher in prime_robot.py sees a
        #: planned disconnect as an unexplained drop, warns about it,
        #: and starts a SECOND reconnect racing the one already running.
        self._deliberate_disconnect = False
        self._was_deliberate = False
        self._disconnect_reason: str | None = None
        #: Counts reconnects (logging only; the client id stays the
        #: server-issued one -- see reconnect()).
        self._reconnects = 1

    def _build_client(self, timeout: float) -> aiomqtt.Client:
        try:
            import certifi
            ca_certs: str | None = certifi.where()
        except ImportError:  # pragma: no cover - certifi is a dependency
            ca_certs = None
        return aiomqtt.Client(
            self._endpoint,
            443,
            identifier=self._token.client_id,
            protocol=aiomqtt.ProtocolVersion.V311,
            transport="websockets",
            websocket_path="/mqtt",
            websocket_headers={
                "x-amz-customauthorizer-name": self._token.iot_authorizer_name,
                "x-amz-customauthorizer-signature": self._token.iot_signature,
                "x-irobot-auth": self._token.iot_token,
                # NO User-Agent, deliberately. One was added in a22 as an
                # experiment, on a third-party project's claim that AWS
                # IoT's authorizer inspects it. The parallel APK research
                # then DISPROVED that: the real app sends exactly the three
                # headers above and no fourth.
            },
            # Applied by aiomqtt inside its connect executor, so loading
            # the certificate bundle never blocks the event loop.
            tls_params=aiomqtt.TLSParameters(
                ca_certs=ca_certs, tls_version=ssl.PROTOCOL_TLS_CLIENT
            ),
            # keepalive=60, LOWERED FROM 300 in 0.3. MQTT declares a
            # connection dead after 1.5x the keepalive interval, so 300
            # meant a broken connection went unnoticed for up to 450
            # seconds -- and during that window a publish looked queued
            # while nothing reached the broker. AWS IoT accepts 30 upward.
            keepalive=60,
            # The iRobot app's in-flight window; paho's default is 20. A
            # restore carrying more than twenty topics would otherwise
            # queue behind the window. Found in samm-git/irobot-explore's
            # reconstruction of the app's connection parameters.
            max_inflight_messages=1000,
            # How long the CONNACK may take. Every other call passes its
            # own limit.
            timeout=timeout,
            logger=_DebugOnlyLogger(_LOGGER.getChild("paho")),
        )

    async def connect(self, timeout: float = 10.0) -> None:
        """Opens the connection. On a client that disconnect() closed,
        this opens it again and restores the running watchers'
        subscriptions. On a client that is already connected it does
        nothing: a second connection with the same client id would
        evict the first."""
        # THE LATER CALL DECIDES. A disconnect() that comes while this
        # connect is running or queued must not be undone by it.
        close_requests = self._close_requests
        async with self._reconnect_lock:
            if self._connected:
                # NEVER A SECOND CONNECTION OVER A LIVE ONE. A connect()
                # queued behind a rebuild, with a disconnect() queued
                # behind it, used to open a new connection over the live
                # one and leave the old one running (review finding).
                _LOGGER.debug("roombapy-prime: connect() on a connected client -- nothing to do")
                if self._close_requests == close_requests:
                    self._set_closed(False)
                return
            await self._open(timeout)
            # Open only once it IS open: a connect that fails leaves a
            # closed client closed, and its watchers waiting.
            if self._close_requests == close_requests:
                self._set_closed(False)
            if self._persistent:
                await self._subscribe_and_wait(list(self._persistent), revive=False)

    async def _open(self, timeout: float) -> None:
        # Logged because a same-client_id collision is invisible
        # otherwise, and its symptoms look like anything but what they
        # are: AWS IoT disconnects the OLDER connection when a second
        # one arrives using the same client_id. Two consumers of one
        # robot -- Home Assistant and a diagnostic script, say -- then
        # take turns evicting each other, and each side sees an
        # unexplained drop. Run a check with the phone app closed and
        # Home Assistant stopped to rule it out.
        _LOGGER.debug(
            "roombapy-prime: connecting blid=%s with client_id=%s", self._blid, self._token.client_id
        )
        await self._settle_late_connection()
        client = self._build_client(timeout)
        # A NEW SESSION GRANTS NOTHING -- whatever a request recorded
        # while the last connection was dying (review finding: a read
        # that lost its connection mid-SUBACK recorded its topics as
        # subscribed, and every later read of that shadow timed out).
        self._subscribed_topics.clear()
        # SHIELDED, so a cancelled caller cannot abandon a handshake
        # halfway -- see _discard_late_connection().
        enter = asyncio.ensure_future(client.__aenter__())
        try:
            await asyncio.shield(enter)
        except asyncio.CancelledError:
            closings: list[asyncio.Task[Any]] = []
            if enter.done():
                _discard_late_connection(client, enter, closings)
            else:
                enter.add_done_callback(
                    functools.partial(_discard_late_connection, client, closings=closings)
                )
            self._late = (enter, closings, timeout)
            raise
        except MqttError as exc:
            _abandon(client)
            _raise_connect_error(exc, timeout)
        except BaseException:
            _abandon(client)
            raise
        self._client = client
        self._connected = True
        self._generation += 1
        self._pump_task = asyncio.get_running_loop().create_task(self._pump(client))
        self._signal_state_change()

    async def _settle_late_connection(self) -> None:
        """Waits for a cancelled connect's handshake, and for the close
        of whatever it produced -- bounded by that connect's own
        timeout."""
        late, self._late = self._late, None
        if late is None:
            return
        enter, closings, timeout = late
        if not enter.done():
            await asyncio.wait({enter}, timeout=timeout + 5.0)
        await asyncio.sleep(0)  # the done-callback schedules the close
        if closings:
            await asyncio.wait(closings, timeout=5.0)

    def _set_closed(self, closed: bool) -> None:
        self._closed = closed
        if closed:
            self._reopened.clear()
        else:
            self._reopened.set()
        self._signal_state_change()

    def _signal_state_change(self) -> None:
        event, self._state_changed = self._state_changed, asyncio.Event()
        event.set()

    async def _pump(self, client: aiomqtt.Client) -> None:
        """Dispatches every incoming message until the connection ends,
        then reports the end. The only reader of `client.messages`."""
        reason: str | None = None
        try:
            async for message in client.messages:
                self._dispatch(str(message.topic), message.payload)
        except MqttError as exc:
            # aiomqtt raises "Disconnected during message iteration" FROM
            # the cause: the broker's reason code, or the socket error.
            # A clean close of our own has no cause.
            reason = _describe_disconnect(exc.__cause__)
        finally:
            # Only the current connection reports, and only once: a
            # disconnect() that gave up waiting has reported already.
            if client is self._client and self._connected:
                self._connection_lost(reason)

    def _connection_lost(self, reason: str | None) -> None:
        self._connected = False
        # A NEW SESSION GRANTS NOTHING. Keeping the set across a
        # disconnect would make the next read skip a subscription it no
        # longer has -- the exact silence this set exists to avoid.
        self._subscribed_topics.clear()
        # Nothing on a dead connection answers these any more.
        inflight, self._inflight = self._inflight, set()
        for task in inflight:
            task.cancel()
        self._subscribe_tasks.clear()
        self._disconnect_reason = (
            "deliberate: token refresh or reconnect"
            if self._deliberate_disconnect
            else (reason or "Normal disconnection")
        )
        self._was_deliberate = self._deliberate_disconnect
        self._deliberate_disconnect = False
        self._ends[self._generation] = (self._disconnect_reason, self._was_deliberate)
        for old in [g for g in self._ends if g < self._generation - 8]:
            del self._ends[old]
        self._wake_waiters()

    def _wake_waiters(self) -> None:
        waiters, self._disconnect_waiters = self._disconnect_waiters, []
        for event in waiters:
            event.set()

    async def disconnect(self, deliberate: bool = True) -> None:
        """Closes the connection for good: nothing reconnects it until
        connect() is called again. A reconnect in progress finishes
        first and is then closed, so no connection outlives this call.

        Watchers see the close, and wait -- they neither report it as a
        drop nor reconnect. Anything that needs the connection raises
        NOT_CONNECTED.

        `deliberate=False` is kept for callers that want the close
        reported as a drop; it is not used by this library."""
        self._close_requests += 1
        self._set_closed(True)
        async with self._reconnect_lock:
            # AGAIN, under the lock: a connect() that was queued ahead of
            # this one has cleared it meanwhile (review finding).
            self._set_closed(True)
            await self._close(deliberate)
            await self._settle_late_connection()

    async def _close(self, deliberate: bool) -> None:
        """`deliberate` marks this as our own close, so the watcher does
        not report it as a drop and does not start a competing
        reconnect."""
        client, pump = self._client, self._pump_task
        if client is None or not self._connected:
            # Nothing is open, so nothing will report a drop: the flag
            # must not be set (review finding -- it survived into the
            # first real connection).
            return
        self._deliberate_disconnect = deliberate
        try:
            try:
                await client.__aexit__(None, None, None)
            except MqttError as exc:
                _LOGGER.debug("roombapy-prime: disconnect of a dead connection -- %s", exc)
            if pump is not None and not pump.done():
                # The message task ends on its own once aiomqtt reports
                # the disconnect; waiting for it means the drop is
                # recorded -- as deliberate -- before anything
                # reconnects. asyncio.wait, not wait_for: the task can
                # end cancelled when aiomqtt gave up on its own DISCONNECT,
                # and wait_for would raise that here as if WE were
                # cancelled (review finding).
                await asyncio.wait({pump}, timeout=5.0)
                if not pump.done():
                    pump.cancel()
            if self._connected and self._client is client:
                # The task could not report in time.
                self._connection_lost(None)
        finally:
            # CLEARED HERE, not only where the drop is reported. A
            # connection that had already dropped reports nothing more,
            # so the flag would survive into the NEXT connection and
            # label its first real drop "deliberate" -- which the watcher
            # answers by not reconnecting (0.4.x too).
            self._deliberate_disconnect = False

    @property
    def generation(self) -> int:
        """Counts successful connects: a watcher remembers the one it
        watches."""
        return self._generation

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def closed(self) -> bool:
        """True from disconnect() until the next connect()."""
        return self._closed

    @property
    def disconnect_reason(self) -> str | None:
        """Why the last connection ended, or why the last reconnect
        failed."""
        return self._disconnect_reason

    def ended(self, generation: int) -> tuple[str, bool] | None:
        """How connection `generation` ended: (reason, deliberate), or
        None while it is up or once it is too old to be remembered."""
        return self._ends.get(generation)

    async def wait_until_settled(self) -> None:
        """Returns once no connect, reconnect or close is in progress."""
        async with self._reconnect_lock:
            pass

    async def wait_for_state_change(self, timeout: float) -> None:
        """Returns once the connection comes up or the client is closed,
        or after `timeout` -- a backoff that ends early when there is
        nothing left to wait for (review finding: other watchers sat
        behind one watcher's backoff on a connection a shadow read had
        already rebuilt)."""
        if self._connected or self._closed:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._state_changed.wait(), timeout)

    async def wait_until_open(self) -> None:
        """Returns once the client is not closed -- at once, unless
        disconnect() closed it."""
        while self._closed:
            await self._reopened.wait()

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

    async def replace_token(self, new_token: ConnectionToken, timeout: float = 10.0) -> None:
        """Swaps the token, disconnects, reconnects, restores all
        running persistent subscriptions (see subscribe()) -- so
        running watch_*() generators keep going transparently, without
        the caller needing to re-subscribe.

        NOT restored: open _pending entries (in-flight get_shadow()/
        update_shadow() calls) -- but none can be open: both hold the
        same lock this does, so a swap waits for a running read or write
        and a read or write waits for the swap."""
        async with self._lock:
            self._token = new_token
            await self.reconnect(timeout=timeout, force=True)

    async def reconnect(
        self, timeout: float = 10.0, *, force: bool = False, if_generation: int | None = None
    ) -> None:
        """Disconnects, connects again with the same, server-issued
        client id, and restores every persistent subscription.

        THE CLIENT ID IS NOT OURS TO CHOOSE. Tried in 0.3.0b2, reverted
        in b3: a derived id (`...-r1-r2-...`) did not connect AT ALL --
        "Connect timed out" every time. The id comes from iRobot's login
        response and the broker's policy expects that one.

        Not under the shadow lock -- replace_token() takes that before
        calling this, and watch_state()'s reconnect loop deliberately
        does not hold it through a backoff wait. Reconnects themselves
        are serialised: one that waited for another which succeeded
        returns without building a second connection, unless `force`
        (a token swap, which must connect with the new token).

        `if_generation` is the connection the caller saw: if a newer one
        is already up, there is nothing to do. Without it, the
        generation at the time of the call is used -- which is too late
        for a caller that noticed a drop a while ago and has been
        backing off since (review finding: a watcher tore down a
        connection a shadow read had just rebuilt, and the read failed).

        A reconnect that fails -- or is cancelled -- after its own
        disconnect is reported like a drop, so the watchers take over
        instead of waiting for a drop that can no longer come (review
        findings).

        Raises NOT_CONNECTED on a client that was never connected or
        that disconnect() closed."""
        if self._client is None:
            raise ShadowError(
                "Not connected. This client needs connect() to have been called at least "
                "once before any shadow read -- named shadows travel over MQTT, not REST. "
                "If you are running one of the diagnostic scripts, this is a bug in the "
                "script rather than anything you did: it asked for shadow data without "
                "opening the connection first.",
                reason=CloudErrorReason.NOT_CONNECTED,
            )
        generation = self._generation if if_generation is None else if_generation
        async with self._reconnect_lock:
            if self._closed:
                raise ShadowError(
                    "The connection was closed by disconnect(); call connect() to open it again.",
                    reason=CloudErrorReason.NOT_CONNECTED,
                )
            if not force and self._connected and self._generation != generation:
                return  # another caller reconnected while this one waited
            _LOGGER.info(
                "roombapy-prime MQTT: reconnecting (%d persistent subscription(s) to restore)",
                len(self._persistent),
            )
            await self._close(deliberate=True)
            self._connected = False
            self._reconnects += 1
            try:
                await self._open(timeout)
            except BaseException as exc:
                self._disconnect_reason = (
                    f"reconnect failed: {exc}"
                    if isinstance(exc, ShadowError)
                    else "reconnect cancelled"
                )
                self._was_deliberate = False
                self._wake_waiters()
                raise
            # _persistent is state on self, so it survives the reconnect;
            # the BROKER no longer knows the subscriptions. Re-subscribe
            # directly, NOT via subscribe() (that would add callbacks).
            #
            # THE LIST IS TAKEN NOW, not before the close: a subscribe()
            # whose SUBACK wait this reconnect cut short registers its
            # topic in between, and a list from before would leave it
            # out (review finding; 0.4.x too).
            await self._subscribe_and_wait(list(self._persistent), revive=False)

    SUBACK_TIMEOUT_SECONDS = 3.0
    """How long a subscribe waits for its SUBACK before carrying on
    unconfirmed (see _subscribe_and_wait())."""

    async def _subscribe_and_wait(
        self, topics: list[str], timeout: float | None = None, *, revive: bool = True
    ) -> None:
        """Subscribes to every topic and waits up to `timeout` for their
        SUBACKs, so a publish that follows cannot outrun its own
        subscription (session 33: responses arriving before the SUBACK
        were lost -- chairstacker's "get_settings() sometimes answers").

        THREE OUTCOMES PER TOPIC, kept apart on purpose:

        - NOT SENT: the client refused the SUBSCRIBE before anything
          left. Raises SubscriptionRejectedError, SUBSCRIPTION_NOT_SENT.
        - REJECTED: the SUBACK carries a failure code (0x80, an AWS IoT
          policy denial). Raises SubscriptionRejectedError. A rejected
          subscription and a quiet topic look identical to the caller
          otherwise (chairstacker's empty wildcard capture).
        - UNCONFIRMED: no SUBACK within `timeout`. Warned about, NOT
          raised: some Prime sessions deliver traffic without a visible
          SUBACK (@utkjmitch logs one on every 55-minute reconnect). The
          SUBSCRIBE keeps waiting in the background; a late SUBACK still
          counts, see resubscribe_still_unconfirmed().

        Revives a dead connection first: subscribing to a dead
        connection fails silently, and the watcher then reports a real
        robot reaction as "nothing happened". Not when called from inside
        a connect or reconnect (`revive=False`): those hold the lock a
        revival would wait for.

        A caller that is cancelled leaves its SUBSCRIBEs running: they
        belong to the connection, which cancels them when it ends."""
        if timeout is None:
            timeout = self.SUBACK_TIMEOUT_SECONDS
        if revive and (self._client is None or not self._connected):
            await self.reconnect(timeout=timeout)
        client = self._client
        if client is None or not self._connected:
            raise ShadowError(
                "SUBSCRIBE has no connection to go out on.",
                reason=CloudErrorReason.CONNECTION_FAILED,
            )
        tasks: dict[str, asyncio.Task[Any]] = {}
        for topic in topics:
            task = asyncio.ensure_future(client.subscribe(topic, qos=1, timeout=math.inf))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
            # A SUBSCRIBE still waiting from an earlier call is left to
            # finish: cancelled, its SUBACK would be logged as an error.
            self._subscribe_tasks[topic] = task
            tasks[topic] = task
        try:
            if tasks:
                await asyncio.wait(tasks.values(), timeout=timeout)
        except BaseException:
            for topic, task in tasks.items():
                if not task.done():
                    task.add_done_callback(functools.partial(self._late_suback, topic))
            raise
        not_sent: dict[str, Any] = {}
        rejected: dict[str, Any] = {}
        unconfirmed: list[str] = []
        for topic, task in tasks.items():
            if not task.done() or task.cancelled():
                unconfirmed.append(topic)
                if not task.done():
                    task.add_done_callback(functools.partial(self._late_suback, topic))
                continue
            exc = task.exception()
            if isinstance(exc, MqttCodeError):
                not_sent[topic] = exc.rc
                continue
            if exc is not None:
                not_sent[topic] = str(exc)
                continue
            failed = [rc for rc in task.result() or () if _suback_is_failure(rc)]
            if failed:
                rejected[topic] = failed

        self._last_subscribe_topics = list(tasks)
        self.last_subscribe_unconfirmed = unconfirmed
        self.subscribe_unconfirmed_count += len(unconfirmed)
        if unconfirmed:
            # THE BROKER'S REASON, IF IT GAVE ONE. A broker that drops a
            # client for an unauthorised subscribe says why on
            # disconnect, and this warning is where anyone looks first.
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
        if not_sent:
            raise SubscriptionRejectedError(
                f"SUBSCRIBE was never sent for {not_sent} (paho error codes) -- the "
                "client reported a failure before anything reached the broker. "
                "Distinct from a rejection: the broker never saw this.",
                reason=CloudErrorReason.SUBSCRIPTION_NOT_SENT,
            )
        if rejected:
            raise SubscriptionRejectedError(
                f"Broker REJECTED subscription (SUBACK failure code) for: {rejected}. "
                "This is a different, more specific finding than 'nothing arrived' -- "
                "the broker's own IoT policy denied this topic outright, not a silent "
                "absence of traffic on it."
            )

    def _late_suback(self, topic: str, task: asyncio.Task[Any]) -> None:
        """A SUBACK that came after the wait. Logged; a late rejection
        is worth knowing about even though nobody is waiting for it."""
        if task.cancelled() or task.exception() is not None:
            return
        if any(_suback_is_failure(rc) for rc in task.result() or ()):
            _LOGGER.warning(
                "roombapy-prime: late SUBACK REJECTED the subscription to %s", topic
            )
        else:
            _LOGGER.debug("roombapy-prime: late SUBACK for %s", topic)

    def resubscribe_still_unconfirmed(self) -> list[str]:
        """Topics of the last subscribe with no SUBACK, re-checked now
        rather than at the moment the wait expired.

        A SUBACK ARRIVING LATE IS STILL A SUBACK. The SUBSCRIBE keeps
        waiting after `_subscribe_and_wait()` gives up, so
        `last_subscribe_unconfirmed` is a snapshot of a deadline, not a
        verdict. @utkjmitch (b7): every reconnect logs `no SUBACK within
        3.0s`; treating that snapshot as failure would put him into a
        reconnect loop every 55 minutes.

        THE WATCHERS' TOPICS, not those of whatever subscribed last: a
        shadow read in the second after a reconnect used to replace the
        list this checked (review finding)."""
        return [
            topic for topic in self._persistent
            if (task := self._subscribe_tasks.get(topic)) is not None and not task.done()
        ]

    @property
    def last_disconnect_was_deliberate(self) -> bool:
        """True when the last disconnect was our own reconnect or token
        refresh, rather than something the broker or network did."""
        return self._was_deliberate

    async def wait_for_disconnect(self, generation: int | None = None) -> str:
        """Resolves with the disconnect reason once connection
        `generation` has ended -- at once if it already has.

        BY GENERATION, NOT BY EVENT. Up to 0.5.0b1 this only heard of a
        drop that happened while it was waiting. A watcher is not always
        waiting: it hands a message to its consumer, sleeps a second
        after a reconnect, backs off. A drop in any of those gaps woke
        nobody, and the watcher then waited on a dead connection for good
        while the log said "watch resumed" (review finding; 0.4.x too).
        So a watcher remembers the generation it watches, and asking
        about one that has already ended answers at once.

        Without `generation`, this waits for the next end of any
        connection, as it always has -- a caller that loops on it after a
        drop must not find it answering at once, or the loop would spin
        without ever yielding."""
        if generation is None:
            event = asyncio.Event()
            self._disconnect_waiters.append(event)
            try:
                await event.wait()
            finally:
                if event in self._disconnect_waiters:
                    self._disconnect_waiters.remove(event)
            return self._disconnect_reason or "unknown"
        if self._connected and self._generation == generation:
            event = asyncio.Event()
            self._disconnect_waiters.append(event)
            try:
                await event.wait()
            finally:
                if event in self._disconnect_waiters:
                    self._disconnect_waiters.remove(event)
        end = self._ends.get(generation)
        if end is not None:
            return end[0]
        return self._disconnect_reason or "unknown"

    def _dispatch(self, topic: str, raw: Any) -> None:
        """One incoming message to its waiters and watchers."""
        payload: dict[str, Any] | str
        try:
            payload = json.loads(raw)
        except (JSONDecodeError, TypeError, ValueError):
            if isinstance(raw, (bytes, bytearray)):
                payload = bytes(raw).decode(errors="replace")
            else:
                payload = "" if raw is None else str(raw)
        response = ShadowResponse(topic=topic, payload=payload)
        for cb in self._pending.pop(topic, []):
            # A CALLBACK THAT RAISES MUST NOT END THE DISPATCH. In 0.4.x
            # it killed paho's network thread, and the connection then
            # looked alive while delivering nothing (@jouwdan's 21 keys,
            # then silence; @DaRealGuGu's "queued but never sent"). Here
            # it would end the message task -- the same silence.
            try:
                cb(response)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "roombapy-prime: a shadow callback raised for %s -- "
                    "the message is lost, the connection is not",
                    topic,
                )
        # PERSISTENT SUBSCRIBERS ARE MATCHED BY PATTERN, not by an exact
        # key: a registration can be a wildcard filter (watch_raw_topic's
        # "{prefix}/things/{blid}/#"), and a message always arrives on a
        # concrete topic (chairstacker's empty wildcard capture). A
        # snapshot of the registrations, so a watcher that starts or
        # stops inside a callback does not change what is being walked.
        for pattern, cbs in list(self._persistent.items()):
            if mqtt.topic_matches_sub(pattern, topic):
                for cb in list(cbs):
                    try:
                        cb(response)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "roombapy-prime: a watcher raised for %s -- "
                            "the message is lost, the connection is not",
                            topic,
                        )

    async def _publish(
        self, topic: str, payload: str | bytes, timeout: float = 5.0
    ) -> None:
        """Publishes at QoS 1 and raises unless the broker acknowledged
        it (PUBACK) within `timeout`.

        A publish that never leaves and a robot that never answers look
        the same from the caller: silence, then a timeout. The two are
        told apart here.
        """
        client = self._client
        if client is None or not self._connected:
            raise ShadowError(
                f"PUBLISH to {topic} has no connection to go out on.",
                reason=CloudErrorReason.PUBLISH_NOT_DELIVERED,
            )
        try:
            await client.publish(topic, payload=payload, qos=1, timeout=timeout)
        except MqttCodeError as exc:
            # WHY THE SOCKET DIED, when we know it. rc=4 (no connection)
            # says the connection was gone, not why; the broker's reason
            # arrived earlier, on disconnect (@utkjmitch: subscribes
            # dead, publishes alive -- a broker dropping a client for an
            # unauthorised subscribe looks exactly like that).
            why = (
                f" The broker's last disconnect reason was: {self._disconnect_reason}."
                if self._disconnect_reason else ""
            )
            raise ShadowError(
                f"PUBLISH to {topic} was refused by the client (paho rc={exc.rc}) -- "
                f"the request never left, so a timeout below would mean nothing.{why}",
                reason=CloudErrorReason.PUBLISH_NOT_DELIVERED,
            ) from exc
        except MqttError as exc:
            raise ShadowError(
                f"PUBLISH to {topic} was not acknowledged within {timeout}s -- "
                "the connection accepts messages and is not delivering them.",
                reason=CloudErrorReason.PUBLISH_NOT_DELIVERED,
            ) from exc


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
        lifetime stats rather than a live pad-dry state.

        THE FAMILY IS CLOSED, AND THIS QUESTION IS ANSWERED. It read
        here for a while that a `charge` or `battery` sibling "would be
        the real find and has not been observed" -- an open question no
        field tester could ever have closed, because a tester's silence
        is ambiguous.

        Firmware 3.8.126 settles it. `cloud-state-mqtt-connection.cpp::
        configure_mqtt_topics` builds the complete local subscription
        list as one contiguous literal, and the report topics in it are
        exactly:

            /evac/report  /dock/refill/report
            /dock/padwash/report  /dock/paddry/report

        Four, and no more. No `charge`, no `battery`. That is a
        subscribe-list construction rather than a pattern match across
        the image, which is why it supersedes an earlier grep-based
        version of the same finding.

        With no `report_type`, returns a single-level `+` wildcard so a
        caller can subscribe the whole family without knowing which
        types exist -- which is the only way to discover a sibling.

        `evac/report` sits one level up (`evac`, not `dock/evac`), so it
        is deliberately NOT covered by this builder; use watch_raw_topic
        for that one.
        """
        segment = report_type if report_type else "+"
        return f"{irbt_topic_prefix}/things/{self._blid}/dock/{segment}/report"

    async def request_mission_timeline(
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
        if self._client is None:
            # This path had no check at all, unlike publish_cmd_payload()
            # below -- calling it before connect() raised AttributeError
            # on None rather than saying what was wrong.
            raise ShadowError(
                "Not connected. connect() must have been called before requesting a "
                "mission timeline -- the request goes over MQTT.",
                reason=CloudErrorReason.NOT_CONNECTED,
            )
        await self._publish(topic, payload)
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

    async def publish_cmd(
        self, irbt_topic_prefix: str, command: str, initiator: str = "localApp"
    ) -> bool:
        """NEW (session 39). Publishes a simple mission command via
        cmd_topic() -- see that method's docstring for the full
        evidence trail. Payload shape {"command": str, "time": int,
        "initiator": str}; "time" is a Unix timestamp in SECONDS.

        Returns whether the broker confirmed receipt (PUBACK) -- the
        broker, not the robot. Callers who want to know the ROBOT reacted
        should still read its state afterwards."""
        return await self.publish_cmd_payload(
            irbt_topic_prefix, {"command": command, "initiator": initiator}
        )

    async def publish_cmd_payload(
        self, irbt_topic_prefix: str, payload: dict[str, Any], *, confirm_timeout: float = 5.0,
    ) -> bool:
        """NEW (session 46). Lower-level sibling of publish_cmd() --
        publishes an ARBITRARY payload dict to cmd_topic(), adding a
        "time" field (Unix seconds) if not already present. Returns
        whether the broker acknowledged it within `confirm_timeout`.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#mqtt_clientpublish_cmd_payload
    """
        if self._client is None:
            # A message for whoever wrote the calling code, surfacing to
            # whoever ran a diagnostic script.
            raise ShadowError(
                "Not connected. connect() must have been called before publishing a command "
                "-- commands go over MQTT. If you are running a diagnostic script, this is a "
                "bug in the script rather than anything you did.",
                reason=CloudErrorReason.NOT_CONNECTED,
            )
        # REVIVE A DEAD CONNECTION BEFORE PUBLISHING. @DaRealGuGu, three
        # sessions: the first send of every session got no PUBACK, and
        # his logs show the connection was already dead before the send
        # -- killed by the interactive pause while a human read a large
        # payload. Publishing into a dead connection returns no error and
        # no PUBACK, which then reads like a finding about the payload.
        async with self._lock:
            if not self._connected:
                _LOGGER.info(
                    "roombapy-prime: connection was not alive before publish -- reconnecting"
                )
                await self.reconnect(timeout=confirm_timeout)

        topic = self.cmd_topic(irbt_topic_prefix)
        full_payload = {**payload}
        full_payload.setdefault("time", int(time.time()))
        try:
            await self._publish(topic, json.dumps(full_payload), timeout=confirm_timeout)
        except ShadowError as exc:
            _LOGGER.debug("roombapy-prime: command publish not confirmed -- %s", exc)
            return False
        return True

    async def subscribe(self, topic: str, callback: Callable[[ShadowResponse], None]) -> None:
        """Register a callback that fires on EVERY message on this topic,
        indefinitely (until unsubscribe() removes it) -- for continuous
        dispatch (shadow deltas, live-map/-position streams), as opposed
        to get_shadow()/update_shadow()'s one-shot wait-for-one-response
        pattern.

        Multiple callbacks on the same topic coexist fine (each gets
        every message) -- the broker-level subscribe only happens once,
        the first time this topic is used.

        Callbacks run on the event loop, inside the message task, and
        must not block. One that raises is logged and skipped.

        Revives a dead connection first, like every other operation in
        this module -- a silently-failed subscribe means the caller
        watches nothing and reports a real robot reaction as
        "nothing happened"."""
        if self._client is None or not self._connected:
            await self.reconnect()
        is_new_topic = topic not in self._persistent
        # SUBSCRIBE FIRST, REGISTER SECOND (@jouwdan, PR #62). Registered
        # first, a failing subscribe left the topic in `_persistent` with
        # no subscription, and every later subscribe() skipped the broker
        # call -- permanently registered, permanently silent.
        #
        # AGAIN IF THE CONNECTION WAS REPLACED MEANWHILE. A reconnect
        # during the SUBACK wait restores the topics registered when it
        # started -- not this one, which registers only afterwards. The
        # new connection then never heard of it (review finding; 0.4.x
        # too). A drop without a new connection needs nothing here: the
        # topic registers below, and the next connection restores it.
        if is_new_topic:
            while True:
                generation = self._generation
                await self._subscribe_and_wait([topic])
                if not self._connected or self._generation == generation:
                    break
        callbacks = self._persistent.setdefault(topic, [])
        # ONCE PER CALLBACK. watch_live_map() subscribes again after every
        # drop, with the same callback; each re-subscribe used to add a
        # copy, so after N drops every message arrived N+1 times and
        # unsubscribe() removed only one (review finding; 0.4.x too).
        if callback not in callbacks:
            callbacks.append(callback)

    async def unsubscribe(self, topic: str, callback: Callable[[ShadowResponse], None]) -> None:
        """Removes exactly this callback. Reference-counted: only
        unsubscribes at the broker level once no callbacks remain for
        this topic, so two concurrent watchers on the same topic don't
        kill each other's subscription when one of them stops.

        Never raises: this runs from finally-blocks, where an exception
        would mask the original error."""
        callbacks = self._persistent.get(topic)
        if callbacks is None:
            return
        if callback in callbacks:
            callbacks.remove(callback)
        if callbacks:
            return
        self._persistent.pop(topic, None)
        # A SUBSCRIBE still waiting is left to finish (see _inflight).
        self._subscribe_tasks.pop(topic, None)
        if self._client is None or not self._connected:
            # Nothing to unsubscribe from. Deliberately NOT a reconnect --
            # rebuilding a connection purely to tear it down again would
            # be absurd.
            return
        try:
            await self._client.unsubscribe(topic, timeout=3.0)
        except MqttError as exc:
            _LOGGER.debug("roombapy-prime: unsubscribe from %s not confirmed -- %s", topic, exc)

    async def _request(
        self,
        base: str,
        request: str,
        payload: str | bytes,
        answers: tuple[str, ...],
        timeout: float,
        *,
        subscribe_every_time: bool,
    ) -> ShadowResponse:
        """One shadow request: register for the answers, subscribe,
        publish, wait for the first answer. Caller holds the lock."""
        if not self._connected:
            await self.reconnect(timeout=timeout)
        loop = asyncio.get_running_loop()
        answered: asyncio.Future[ShadowResponse] = loop.create_future()

        def _capture(resp: ShadowResponse) -> None:
            if not answered.done():
                answered.set_result(resp)

        topics = []
        for suffix in answers:
            topic = f"{base}/{suffix}"
            self._pending.setdefault(topic, []).append(_capture)
            topics.append(topic)
        try:
            if subscribe_every_time:
                await self._subscribe_and_wait(topics)
            else:
                # ONLY SUBSCRIBE TO WHAT IS NOT ALREADY SUBSCRIBED.
                # @DaRealGuGu's second `rw-settings` read in one session
                # re-subscribed to topics the broker had already granted,
                # got no SUBACK, and then no answer -- while the first read
                # had worked. Deleting the redundant step, not retrying it.
                fresh = [t for t in topics if t not in self._subscribed_topics]
                if fresh:
                    session = self._client
                    await self._subscribe_and_wait(fresh)
                    # Only for the connection that granted them.
                    if self._client is session and self._connected:
                        self._subscribed_topics.update(fresh)
            # THE PUBLISH IS CONFIRMED, not fired and forgotten: a request
            # that never left and a robot with no such shadow otherwise
            # look the same. An answer that arrived anyway wins.
            try:
                await self._publish(f"{base}/{request}", payload)
            except ShadowError:
                if not answered.done():
                    raise
            try:
                return await asyncio.wait_for(asyncio.shield(answered), timeout=timeout)
            except TimeoutError:
                raise ShadowError(
                    f"No response to {request.upper()} on {base} within {timeout}s",
                    reason=CloudErrorReason.TIMEOUT,
                ) from None
        finally:
            # Whatever did not answer stops waiting. The other answer
            # topics of this request keep no stale waiter behind.
            for topic in topics:
                waiters = self._pending.get(topic)
                if waiters and _capture in waiters:
                    waiters.remove(_capture)
                    if not waiters:
                        self._pending.pop(topic, None)

    async def get_shadow(self, named: str | None = None, timeout: float = 8.0) -> ShadowResponse:
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
        async with self._lock:
            base = _shadow_base(self._blid, named)
            response = await self._request(
                base, "get", b"", ("get/accepted", "get/rejected"), timeout,
                subscribe_every_time=False,
            )
            if response.topic.endswith("/get/rejected"):
                raise ShadowError(
                    f"GET rejected: {response.payload}", reason=CloudErrorReason.SHADOW_REJECTED
                )
            return response

    async def update_shadow(
        self, desired: dict[str, Any], named: str | None = None, timeout: float = 8.0
    ) -> ShadowResponse:
        """Set desired state. Confirmed to actually propagate to the
        physical robot, not just the shadow document — verified via a
        real, observable value change with exact timing correlation in
        the local MQTT log (see CLOUD_SHADOW_PUSH_FINDINGS.md section 5).
        A no-op write (value unchanged) will still get update/accepted
        but gives you no way to confirm actual delivery — use a genuinely
        different, restorable value if you need to verify delivery.

        Reconnects first if the connection is known to be down, and
        runs under the same lock as get_shadow() and replace_token()."""
        async with self._lock:
            base = _shadow_base(self._blid, named)
            response = await self._request(
                base, "update", json.dumps({"state": {"desired": desired}}),
                ("update/accepted", "update/rejected", "update/delta"), timeout,
                subscribe_every_time=True,
            )
            if response.topic.endswith("/update/rejected"):
                raise ShadowError(
                    f"UPDATE rejected: {response.payload}", reason=CloudErrorReason.SHADOW_REJECTED
                )
            return response
