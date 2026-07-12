"""Oeffentliche Roboter-Klasse (Analog zu roombapy.roomba.Roomba).

STATUS: Draft. Verbindet auth.LoginResult, mqtt_client.PrimeMqttClient
und rest_client.PrimeRestClient. NICHT gegen ein echtes V4-Konto
getestet -- die einzelnen Bausteine sind unterschiedlich weit bestaetigt
(siehe deren jeweilige Docstrings), diese Klasse selbst ist reine
Verdrahtung, ungetestet als Ganzes.

Seit heute (siehe watch_state()/watch_live_map() unten) Teil dieses
Drafts: kontinuierliche Dispatch-Schleifen fuer Shadow-Deltas und
Live-Map/-Position-Nachrichten -- vorher bewusst ausgeklammert (siehe
docs/ROOMBAPY_COMPARISON.md Abschnitt 3). Bruecke von paho's
Hintergrund-Thread (treibt mqtt_client.py's subscribe()-Callbacks an)
in die asyncio-Welt: eine asyncio.Queue PRO watch_*()-Aufruf,
befuellt via loop.call_soon_threadsafe(). Kein Lock noetig -- jeder
Watcher bekommt seine eigene Queue, mqtt_client.py's subscribe()/
unsubscribe() sind bereits referenzgezaehlt fuer den Fall, dass zwei
Watcher dasselbe Topic beobachten (siehe dessen Docstring).

Ebenfalls seit heute: proaktiver Token-Refresh (siehe _refresh_loop()
unten). PrimeFactory verdrahtet dafuer standardmaessig einen relogin-
Callback -- ohne den (relogin=None) verhaelt sich diese Klasse wie
vorher: Tokens laufen nach ~1h ab, laufende watch_*()-Generatoren
liefern dann einfach keine Nachrichten mehr, kein Fehler.

WICHTIGER TRADEOFF, nicht versteckt: automatischer Refresh bedeutet,
dass Zugangsdaten (ueber den relogin-Callback) fuer die gesamte
Lebensdauer der PrimeRobot-Instanz im Speicher bleiben muessen, nicht
nur fuer den einmaligen Login-Moment wie vorher. Wer das nicht will,
laesst relogin weg und nimmt das ~1h-Verfallslimit in Kauf.

Weiterhin NICHT Teil dieses Drafts:
  - Kein Backpressure-Handling -- die interne Queue ist unbegrenzt.
    Ein Consumer, der nicht mitkommt, laesst sie unbegrenzt wachsen.
  - replace_token() (siehe mqtt_client.py) ist NICHT sicher gegenueber
    einem gleichzeitig laufenden get_shadow()/update_shadow()-Aufruf --
    bekannte, akzeptierte Einschraenkung, kein Lock vorhanden.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from .auth import LoginResult
from .mqtt_client import PrimeMqttClient, ShadowResponse
from .rest_client import PrimeRestClient
from .models import (
    FavoriteV1,
    HouseholdSchedule,
    LiveMapStreamInit,
    MapEditCommand,
    MapEditCommandV1,
    MapUpdateMessage,
    PositionUpdateMessage,
    RoutineCommand,
    ScheduleOptions,
    parse_livemap_message_data,
)

_LOGGER = logging.getLogger(__name__)

Relogin = Callable[[], Awaitable[LoginResult]]

DEFAULT_WATCH_QUEUE_MAXSIZE = 100
# Willkuerlich gewaehlt (kein empirischer Wert) -- gross genug, um kurze
# Verarbeitungs-Verzoegerungen beim Aufrufer abzufedern, klein genug, um
# nicht unbegrenzt Speicher zu binden, falls der Consumer dauerhaft
# hinterherhinkt.


def _put_with_backpressure(queue: "asyncio.Queue[object]", item: object, topic: str) -> None:
    """Laeuft auf dem Event-Loop-Thread (aufgerufen via
    loop.call_soon_threadsafe aus watch_state()/watch_live_map()). Ist
    die Queue voll, wird der AELTESTE Eintrag verworfen, um Platz fuer
    den neuen zu schaffen -- Aktualitaet vor Vollstaendigkeit, passend
    fuer Status-/Positions-Stroeme, wo ein veralteter Wert weniger nuetzt
    als ein aktueller. Jeder Drop wird geloggt, damit ein
    hinterherhinkender Consumer nicht unbemerkt Nachrichten verliert.

    NEU: wird ausgerechnet ein Exception-Eintrag verworfen (watch_live_map()
    legt Fehler mit in dieselbe Queue, siehe dortigen Docstring), wird das
    als ERROR statt WARNING geloggt -- ein verlorener Fehler ist ernster
    als eine verlorene Routine-Nachricht. Verhindert den Verlust NICHT
    (dafuer bräuchte es eine Prioritaets-Queue statt einer einfachen
    FIFO), macht ihn aber sichtbarer, statt in der Masse gewoehnlicher
    Drops unterzugehen."""
    if queue.full():
        try:
            dropped = queue.get_nowait()
            if isinstance(dropped, Exception):
                _LOGGER.error(
                    "watch_*()-Queue fuer Topic %s voll -- ein FEHLER wurde beim "
                    "Verwerfen des aeltesten Eintrags verworfen (nicht nur eine "
                    "Routine-Nachricht): %r. Der Aufrufer hat dieses Fehlersignal "
                    "verpasst.",
                    topic,
                    dropped,
                )
            else:
                _LOGGER.warning(
                    "watch_*()-Queue fuer Topic %s voll -- aeltester Eintrag "
                    "verworfen, um Platz zu schaffen (Consumer kommt nicht hinterher)",
                    topic,
                )
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(item)


class PrimeRobot:
    """Ein Roboter, identifiziert durch blid. Haelt keine eigene
    Login-Session -- die kommt fertig verdrahtet aus prime_factory.py.

    relogin: optionaler async Callback ohne Argumente, der einen neuen
    LoginResult liefert (siehe prime_factory.py). Nur noetig fuer
    proaktiven Token-Refresh -- ohne ihn laeuft alles wie gehabt, nur
    ohne automatischen Refresh (siehe Modul-Docstring, Tradeoff).

    irbt_topic_prefix: NEU, UNSICHER (siehe auth.py's LoginResult-
    Docstring und mqtt_client.py's livemap_topic()). Noetig fuer
    watch_live_map() -- ohne ihn wirft watch_live_map() sofort einen
    klaren Fehler, statt still auf ein falsches Topic zu warten."""

    def __init__(
        self,
        blid: str,
        mqtt_client: PrimeMqttClient,
        rest_client: PrimeRestClient,
        relogin: Relogin | None = None,
        irbt_topic_prefix: str | None = None,
    ) -> None:
        self.blid = blid
        self._mqtt = mqtt_client
        self._rest = rest_client
        self._relogin = relogin
        self._irbt_topic_prefix = irbt_topic_prefix
        self._refresh_task: asyncio.Task[None] | None = None

    async def connect(self, timeout: float = 10.0) -> None:
        """Blockierender paho-Verbindungsaufbau in einem Worker-Thread,
        damit der Rest der App async bleiben kann (siehe mqtt_client.py
        -- der Client selbst ist absichtlich nicht umgebaut worden).
        Startet zusaetzlich die Refresh-Schleife im Hintergrund, falls
        relogin uebergeben wurde (siehe Klassen-Docstring)."""
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
        """Loggt proaktiv neu ein und tauscht den MQTT-Token kurz vor
        Ablauf (siehe mqtt_client.py's seconds_until_token_refresh_due()/
        replace_token()) -- damit laufende watch_*()-Generatoren und
        zukuenftige Anfrage/Antwort-Aufrufe die ~1h-Token-Lebensdauer
        ueberleben. Kehrt endgueltig zurueck (kein weiterer Refresh),
        sobald keine Ablaufzeit mehr bekannt ist -- siehe
        seconds_until_token_refresh_due()'s Docstring, warum das eine
        bekannte Einschraenkung ist, kein stiller Fehler."""
        while True:
            wait_seconds = self._mqtt.seconds_until_token_refresh_due()
            if wait_seconds is None:
                return
            await asyncio.sleep(wait_seconds)
            assert self._relogin is not None  # invariant: nur gestartet, wenn gesetzt
            login_result = await self._relogin()
            new_token = login_result.token_for_blid(self.blid)
            await asyncio.to_thread(self._mqtt.replace_token, new_token)

    # --- Shadow-basierte Operationen (ueber mqtt_client.py) -----------

    async def get_state(self, timeout: float = 8.0) -> ShadowResponse:
        """Klassischer/unbenannter Shadow -- Identitaet, Capabilities,
        laufender Missionsstatus. Antwortet zuverlaessig auf beiden
        bisher getesteten Tiers (EPHEMERAL + SMART)."""
        return await asyncio.to_thread(self._mqtt.get_shadow, None, timeout)

    async def get_settings(self, timeout: float = 8.0) -> ShadowResponse:
        """Benannter "rw-settings"-Shadow -- antwortet nur auf
        SMART-Tier, laeuft auf EPHEMERAL in den Timeout (kein Fehler,
        siehe mqtt_client.py get_shadow-Docstring)."""
        return await asyncio.to_thread(self._mqtt.get_shadow, "rw-settings", timeout)

    async def set_setting(self, key: str, value: object, timeout: float = 8.0) -> ShadowResponse:
        """Schreibt ins "rw-settings"-Shadow. Nur sinnvoll auf
        SMART-Tier -- auf EPHEMERAL vermutlich derselbe Timeout wie bei
        get_settings(), nie getestet."""
        return await asyncio.to_thread(self._mqtt.update_shadow, {key: value}, "rw-settings", timeout)

    async def send_mission_command(self, command: RoutineCommand, timeout: float = 8.0) -> ShadowResponse:
        """BESTAETIGT (15. Sitzung) -- siehe models.py's Missionssteuerungs-
        Abschnitt fuer die Payload-Herleitung.

        Sendet ueber den KLASSISCHEN (unbenannten) Shadow. Das ist jetzt
        definitiv bestaetigt -- nicht mehr durch Bytecode-Interpretation,
        sondern durch die tatsaechliche, in der APK mitgelieferte
        Konfigurationsdatei (res/raw/base_roomba_config.json,
        "commandList"-Eintrag fuer commandId "Control"):

            {"commandId": "Control", "topic": "cmd", "namedShadow": ""}

        Zum Vergleich, im selben JSON (bestaetigt "rw-settings" fuer
        Settings, unabhaengig von der Kotlin/nativen Herleitung):

            {"commandId": "SetBinPause", "topic": "delta",
             "namedShadow": "rw-settings"}
            {"commandId": "AssetScheduleCommand,Set", "topic": "delta",
             "namedShadow": "rw-schedule"}

        Das "namedShadow": "" fuer "Control" ist der entscheidende
        Unterschied -- Missionsbefehle nutzen keinen benannten Shadow,
        im Gegensatz zu Settings ("rw-settings") und Zeitplaenen
        ("rw-schedule"). Diese Datei wurde erst gefunden, nachdem eine
        vielversprechende, aber falsche native Kette (Vtable-Slot-Zaehl-
        fehler, siehe Versionsgeschichte dieser Methode) den Weg dorthin
        aufgezeigt hatte (PMIAssetServiceImpl::getProtocolConfig() ->
        ProtocolConfig::ProtocolConfig(string) -> genau diese JSON-Datei
        als Konstruktor-Eingabe).

        NIE gegen einen echten Server gesendet -- Transport (jetzt aus
        der echten Konfigurationsdatei bestaetigt) und Payload-Form
        (RoutineCommand-Feldnamen) sind weiterhin nie GEMEINSAM live
        getestet."""
        return await asyncio.to_thread(
            self._mqtt.update_shadow, command.to_shadow_desired(), None, timeout
        )

    # --- REST-basierte p2maps-Operationen (bereits nativ async) -------

    async def get_active_map_versions(self) -> list[dict]:
        """NEU (11. Juli, elfte Sitzung) -- fehlte bisher als Wrapper,
        obwohl rest_client.py's Version schon lange existierte."""
        return await self._rest.get_active_map_versions(self.blid)

    async def get_map_metadata(self, p2map_id: str) -> dict:
        return await self._rest.get_map_metadata(p2map_id)

    async def set_map_name(self, p2map_id: str, name: str) -> dict:
        return await self._rest.set_map_name(p2map_id, name)

    async def set_map_orientation(self, p2map_id: str, orientation_rad: float) -> dict:
        return await self._rest.set_map_orientation(p2map_id, orientation_rad)

    async def delete_map(self, p2map_id: str) -> dict:
        """NEU (dreizehnte Sitzung) -- fehlte bisher als Wrapper trotz
        laengst existierender rest_client.py-Version (systematischer
        Review-Fund)."""
        return await self._rest.delete_map(p2map_id)

    async def get_map_geojson_link(self, map_id: str, map_version: str) -> dict:
        """NEU (dreizehnte Sitzung) -- fehlte bisher als Wrapper. Liefert
        die vorsignierte Download-URL fuer download_map_bundle() (siehe
        dort). Antwortform/Schluesselname der URL unbestaetigt -- siehe
        rest_client.py's Docstring."""
        return await self._rest.get_map_geojson_link(map_id, map_version)

    async def download_map_bundle(self, url: str) -> bytes:
        """NEU (dreizehnte Sitzung) -- fehlte bisher als Wrapper, obwohl
        das Diagnoseskript und parse_map_bundle() darauf angewiesen
        sind. Bewusst OHNE SigV4-Signierung -- siehe rest_client.py's
        Docstring."""
        return await self._rest.download_map_bundle(url)

    async def edit_map(self, p2map_id: str, command: MapEditCommandV1) -> dict:
        """NEU (11. Juli, vierte Sitzung) -- command ist jetzt eine der
        9 V1-Kommando-Dataclasses aus models.py (RenameRoomV1,
        SplitRoomV1, MergeRoomsV1, ...) -- der tatsaechlich aktive Pfad
        (siehe rest_client.py's Docstring, PRIME_APP_GAP_ANALYSIS).
        Fuer den unbenutzten V2-Pfad siehe edit_map_v2()."""
        return await self._rest.edit_map(p2map_id, command)

    async def edit_map_v2(self, p2map_id: str, command: MapEditCommand) -> dict:
        """Der von der App selbst nie aufgerufene V2-Pfad -- siehe
        edit_map()'s Docstring und rest_client.py::edit_map_v2()."""
        return await self._rest.edit_map_v2(p2map_id, command)

    async def get_live_map_stream(self) -> LiveMapStreamInit:
        """KORRIGIERTES VERSTAENDNIS (11. Juli, siehe
        docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md Punkt B1): Dieser
        REST-Aufruf ist vermutlich ein KEEP-ALIVE-Ping, kein "gib mir
        das Topic"-Aufruf -- in der echten App wird die Antwort
        (LiveMapStreamResponse.mqtt_topic) nirgends gelesen, nur
        geparst. watch_live_map() nutzt diese Methode entsprechend NICHT
        mehr zur Topic-Ermittlung, sondern nur noch als periodischen
        Keep-Alive im Hintergrund. Bleibt trotzdem oeffentlich fuer
        Aufrufer, die den rohen REST-Aufruf selbst brauchen."""
        return await self._rest.get_live_map_stream(self.blid)

    # --- Favoriten (FavoriteV1) ------------------------------------------

    async def get_favorites(self) -> list[FavoriteV1]:
        """Siehe rest_client.py::get_favorites() -- einziger der fuenf
        Favoriten-Endpunkte, dessen HTTP-Methode UND Antwortform
        vollstaendig bestaetigt sind."""
        return await self._rest.get_favorites()

    async def create_favorite(self, favorite: FavoriteV1) -> dict:
        """Siehe rest_client.py::create_favorite() -- HTTP-Methode
        (POST) bestaetigt (achte Sitzung)."""
        return await self._rest.create_favorite(favorite)

    async def update_favorite(self, favorite_id: str, favorite: FavoriteV1) -> dict:
        """Siehe rest_client.py::update_favorite() -- HTTP-Methode
        (PUT) bestaetigt (achte Sitzung)."""
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
        """Siehe rest_client.py::get_mission_history() -- vollstaendig
        bestaetigt aus FetchMissionHistoryRequest.java."""
        return await self._rest.get_mission_history(
            blid,
            max_reports=max_reports,
            max_age=max_age,
            filter_type=filter_type,
            exclusive_start_timestamp=exclusive_start_timestamp,
            supported_done_codes=supported_done_codes,
        )

    async def get_schedules(self, household_id: str) -> dict:
        return await self._rest.get_schedules(household_id)

    async def create_schedules(self, household_id: str, schedules: list[ScheduleOptions]) -> dict:
        """HTTP-Methode (POST) bestaetigt (achte Sitzung), siehe
        rest_client.py::create_schedules()."""
        return await self._rest.create_schedules(household_id, schedules)

    async def update_schedules(
        self, household_id: str, household_schedule_id: str, schedules: list[HouseholdSchedule]
    ) -> dict:
        """HTTP-Methode (PUT) bestaetigt (achte Sitzung)."""
        return await self._rest.update_schedules(household_id, household_schedule_id, schedules)

    async def delete_schedule(self, household_id: str, household_schedule_id: str) -> dict:
        return await self._rest.delete_schedule(household_id, household_schedule_id)

    async def get_user_households(self) -> dict:
        """Von der aktuellen App-Version nicht genutzt -- siehe
        rest_client.py::get_user_households()'s Docstring."""
        return await self._rest.get_user_households()

    async def get_dnd_settings(self, household_id: str) -> dict:
        return await self._rest.get_dnd_settings(household_id)

    async def set_dnd_settings(self, household_id: str, settings: dict) -> dict:
        return await self._rest.set_dnd_settings(household_id, settings)

    async def get_cleaning_profiles(self, asset_id: str, p2map_id: str) -> dict:
        return await self._rest.get_cleaning_profiles(asset_id, p2map_id)

    async def get_default_routines(self, p2map_id: str) -> dict:
        return await self._rest.get_default_routines(p2map_id)

    async def get_robot_parts(self) -> dict:
        """NEU (15. Sitzung) -- siehe rest_client.py::get_robot_parts()."""
        return await self._rest.get_robot_parts(self.blid)

    async def reset_robot_parts(self) -> dict:
        """NEU (15. Sitzung) -- siehe rest_client.py::reset_robot_parts()."""
        return await self._rest.reset_robot_parts(self.blid)

    async def get_serial_number_data(self) -> dict:
        """NEU (15. Sitzung) -- siehe rest_client.py::get_serial_number_data()."""
        return await self._rest.get_serial_number_data(self.blid)

    async def poll_echo_value(self) -> dict:
        """NEU (16. Sitzung) -- "finde meinen Roboter"-Funktion, siehe
        rest_client.py::poll_echo_value()."""
        return await self._rest.poll_echo_value(self.blid)

    async def get_time_estimates(self, body: dict) -> dict:
        """NEU (16. Sitzung) -- siehe rest_client.py::get_time_estimates()
        fuer den Hinweis zur unbestaetigten Body-Form."""
        return await self._rest.get_time_estimates(body)

    async def reset_robot(self) -> dict:
        """NEU (16. Sitzung) -- WARNUNG: vermutlich folgenreiche Aktion,
        siehe rest_client.py::reset_robot()."""
        return await self._rest.reset_robot(self.blid)

    async def get_notifications(self, app_version: str = "1.0") -> dict:
        """NEU (16. Sitzung) -- siehe rest_client.py::get_notifications()."""
        return await self._rest.get_notifications(self.blid, app_version)

    # --- Kontinuierliche Dispatch-Schleifen ----------------------------

    async def watch_state(
        self, named: str | None = None, *, queue_maxsize: int = DEFAULT_WATCH_QUEUE_MAXSIZE
    ) -> AsyncIterator[ShadowResponse]:
        """Liefert jedes Shadow-Delta, sobald es eintrifft -- bis der
        Aufrufer die Iteration abbricht (break/return aus einem
        `async for`, oder .aclose()).

        named=None -> klassischer Shadow-Delta (funktioniert auf beiden
        bisher getesteten Tiers). named="rw-settings" -> benannter
        Shadow-Delta, nur auf SMART-Tier erwartet zu funktionieren --
        auf EPHEMERAL liefert dieser Iterator dann vermutlich nie etwas
        (kein Fehler, einfach Stille, analog zu get_shadow()'s
        Timeout-Verhalten -- hier gibt es aber keinen Timeout, da
        "warten auf die naechste Aenderung" der ganze Sinn ist).

        queue_maxsize: begrenzt den internen Puffer (siehe
        DEFAULT_WATCH_QUEUE_MAXSIZE). Bei vollem Puffer wird der
        AELTESTE Eintrag verworfen (nicht der neueste) -- ein
        hinterherhinkender Consumer bekommt so den aktuellsten Stand,
        nicht die laengste Warteschlange. Jeder Drop wird als WARNING
        geloggt.

        WICHTIG: Das Delta-Topic selbst (.../update/delta) ist Teil des
        AWS-IoT-Shadow-Standardverhaltens (liefert sofort eine Nachricht
        beim Abonnieren, falls desired/reported voneinander abweichen,
        danach bei jeder Aenderung) -- diese Standard-Semantik wird hier
        angenommen, nicht speziell fuer Classic/Prime verifiziert.
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
        """KORRIGIERT (11. Juli, siehe
        docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md Punkt B1) -- fruehere
        Version rief get_live_map_stream() auf und abonnierte das darin
        zurueckgegebene Topic. Das war falsch verstanden: in der echten
        App (P2MapAPIFetching.observeLiveMap()) wird sofort ein FESTES
        Topic abonniert (siehe mqtt_client.py's livemap_topic()), und
        get_live_map_stream() laeuft nur als periodischer Keep-Alive im
        Hintergrund weiter, solange beobachtet wird.

        Braucht irbt_topic_prefix (siehe __init__/auth.py's LoginResult)
        -- wenn der None ist (Feldname aus der Discovery-Antwort nicht
        bestaetigt, siehe dort), wirft diese Methode sofort einen
        RuntimeError, statt still auf ein falsch konstruiertes Topic zu
        warten.

        keep_alive_interval: wie oft der Keep-Alive-Ping gesendet wird,
        waehrend beobachtet wird. Die echte App nutzt ein komplexeres
        Schema (Timer relativ zu einer expiration/refreshWindowMillis,
        siehe LiveMapKeepAlivePublisher) -- hier bewusst vereinfacht zu
        einem festen Intervall, da die genaue Nachschlage-/Ausloese-
        Logik des Originals nicht vollstaendig rekonstruiert wurde.
        Scheitert ein einzelner Keep-Alive-Ping, wird das als WARNING
        geloggt, aber die Beobachtung laeuft weiter (ein Ping-Fehlschlag
        soll nicht den ganzen Watcher abbrechen).

        queue_maxsize: siehe watch_state() -- dieselbe Drop-Oldest-
        Backpressure-Politik. WICHTIGE EINSCHRAENKUNG hier: Fehler
        (siehe naechster Absatz) durchlaufen dieselbe Queue wie normale
        Nachrichten und sind damit NICHT von der Drop-Oldest-Politik
        ausgenommen -- ein Fehler koennte theoretisch verworfen werden,
        wenn die Queue zum Zeitpunkt seines Eintreffens voll ist.
        Akzeptierte Einschraenkung fuer diesen Draft, kein Sonderfall
        fuer Fehler eingebaut.

        Nachrichten mit unbekannter Form (weder pos_update noch
        map_update, siehe parse_livemap_message_data) werden NICHT
        stillschweigend uebersprungen -- der Fehler propagiert durch
        den Generator, der Aufrufer sieht ihn im naechsten `async for`-
        Schritt. Das ist eine bewusste Entscheidung: ein unbekanntes
        Nachrichtenformat auf einem noch nie live getesteten Kanal ist
        etwas, das auffallen sollte, nicht etwas, das man stillschweigend
        verwirft.
        """
        if self._irbt_topic_prefix is None:
            msg = (
                "watch_live_map() braucht irbt_topic_prefix (aus LoginResult) -- "
                "None bedeutet: Discovery-Antwort enthielt das (unsicher benannte) "
                "Feld nicht, oder der Feldname war falsch geraten. Siehe "
                "auth.py's LoginResult-Docstring."
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
