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
        # NEU: schliesst eine bisher dokumentierte Luecke (siehe README) --
        # replace_token() trennt/verbindet self._client neu; ohne Schutz
        # koennte ein GLEICHZEITIG (via asyncio.to_thread, also echter
        # OS-Thread) laufender get_shadow()/update_shadow()-Aufruf mitten in
        # dieser Umschaltung auf ein bereits getrenntes oder noch nicht
        # fertig verbundenes self._client zugreifen. threading.Lock, nicht
        # asyncio.Lock -- diese Methoden laufen in echten Threads
        # (to_thread), nicht als Coroutinen auf demselben Event-Loop.
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
        return client

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

    # --- Proaktiver Token-Refresh --------------------------------------
    #
    # Es gibt keinen Refresh-Endpunkt (siehe auth.py) -- "Refresh" heisst
    # hier: mit einem neu eingeloggten Token neu verbinden, WAEHREND
    # laufende subscribe()-Watcher transparent weiterlaufen.

    REFRESH_MARGIN_SECONDS = 300  # 5 Minuten vor Ablauf -- willkuerlich
    # gewaehlt, um Zeit fuer den Re-Login-Roundtrip selbst zu lassen,
    # nicht empirisch gegen die echte ~1h-Token-Lebensdauer getestet.

    def seconds_until_token_refresh_due(self) -> float | None:
        """None, wenn der Token kein expires-Feld hat (siehe
        ConnectionToken.seconds_until_expiry) -- dann kann nicht
        proaktiv geplant werden, das ist eine bekannte Einschraenkung,
        kein stiller Fehler."""
        remaining = self._token.seconds_until_expiry()
        if remaining is None:
            return None
        return max(remaining - self.REFRESH_MARGIN_SECONDS, 0.0)

    def replace_token(self, new_token: ConnectionToken, timeout: float = 10.0) -> None:
        """Tauscht den Token, trennt, verbindet neu, stellt alle
        laufenden persistenten Subscriptions wieder her (siehe
        subscribe()) -- damit laufende watch_*()-Generatoren
        transparent weiterlaufen, ohne dass der Aufrufer neu abonnieren
        muss.

        NICHT wiederhergestellt: offene _pending-Eintraege (laufende
        get_shadow()/update_shadow()-Aufrufe). Faellt ein Refresh
        zufaellig mitten in so einen Aufruf, laeuft der schlicht in
        seinen Timeout und wirft ShadowError -- akzeptierter Randfall,
        da Refreshs mit Vorlauf geplant werden (siehe
        REFRESH_MARGIN_SECONDS), keine Garantie gegen Ueberschneidung.

        NEU: laeuft jetzt unter self._client_lock -- schliesst die
        vorher hier dokumentierte Luecke ("nicht thread-/aufrufsicher
        gegenueber get_shadow()/update_shadow()"). Ein gleichzeitiger
        get_shadow()/update_shadow()-Aufruf wartet jetzt, bis
        replace_token() fertig ist, statt auf einen halb-getrennten
        Client zuzugreifen. Der oben beschriebene _pending-Randfall
        bleibt trotzdem bestehen -- der Lock verhindert nur den
        gleichzeitigen ZUGRIFF auf self._client, nicht das inhaltliche
        Problem "Refresh faellt in ein laufendes get/update"."""
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

            # _persistent selbst ist Zustand auf self, nicht auf dem
            # paho-Client-Objekt -- ueberlebt disconnect()/connect() also
            # automatisch. Der BROKER kennt die Subscriptions nach einem
            # frischen connect() aber nicht mehr -- direkt am neuen
            # paho-Client resubscriben, NICHT ueber subscribe() (das wuerde
            # doppelte Callback-Eintraege anhaengen).
            for topic in topics_to_restore:
                self._client.subscribe(topic, qos=1)

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
        """NEU, UNSICHER. Baut das feste Live-Map-Topic-Muster, wie es
        die echte App verwendet (core::MQTTTopicResolverAdapter.resolve()
        -> "{prefix}/{identifier}", mqttClient.subscribe(irbt,
        "livemap/update", assetId) in P2MapAPIFetching.observeLiveMap())
        -- KEIN Shadow-Topic, komplett unabhaengig von get_shadow()/
        update_shadow(). Die exakte Verkettungsreihenfolge von blid und
        "livemap/update" zu einem einzigen "identifier" ist nicht
        letztgueltig bestaetigt -- hier als "{blid}/livemap/update"
        angenommen (naheliegendste Lesart der drei subscribe()-Argumente),
        nicht aus dem Wire-Format direkt abgelesen."""
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
            self._client.subscribe(topic, qos=1)

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

        NEU: laeuft jetzt unter self._client_lock -- serialisiert gegen
        ein gleichzeitig laufendes replace_token(). Bewusster Tradeoff:
        ist replace_token() gerade aktiv, wartet dieser Aufruf, bis es
        fertig ist, statt auf einen halb-getrennten Client zuzugreifen --
        kann im ungluecklichsten Fall die Antwortzeit um die Dauer eines
        Token-Wechsels verlaengern, nie um mehr als `timeout` selbst."""
        with self._client_lock:
            base = _shadow_base(self._blid, named)
            result: list[ShadowResponse] = []

            def _capture(resp: ShadowResponse) -> None:
                result.append(resp)

            assert self._client is not None, "call connect() first"
            for suffix in ("get/accepted", "get/rejected"):
                topic = f"{base}/{suffix}"
                self._pending.setdefault(topic, []).append(_capture)
                self._client.subscribe(topic, qos=1)
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

        NEU: laeuft jetzt unter self._client_lock, siehe get_shadow()'s
        Docstring fuer den Tradeoff."""
        with self._client_lock:
            base = _shadow_base(self._blid, named)
            result: list[ShadowResponse] = []

            def _capture(resp: ShadowResponse) -> None:
                result.append(resp)

            assert self._client is not None, "call connect() first"
            for suffix in ("update/accepted", "update/rejected", "update/delta"):
                topic = f"{base}/{suffix}"
                self._pending.setdefault(topic, []).append(_capture)
                self._client.subscribe(topic, qos=1)
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
