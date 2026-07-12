"""p2maps REST-Client: Kartenmetadaten, Editierbefehle, Live-Stream-Init.

STATUS: Draft. Endpunkte/Payload-Formen aus Java-Quellcode-Analyse
bestaetigt (siehe docs/FINDINGS_2026-07-11.md), NICHT live gegen einen
echten Server getestet -- weder Classic noch V4/Prime. Kein einziger
dieser Aufrufe ist bisher tatsaechlich ausgefuehrt worden.

Seit heute: AWS-SigV4-Signierung (siehe aws_sigv4.py) und `http_base_auth`
statt `http_base` -- beides uebernommen aus ha_roomba_plus's bereits
produktiv laufender cloud_api.py (dritte, unabhaengige
Bestaetigungsquelle neben Live-Tests und APK-Analyse). Vorher war
`auth_headers` hier ein vages, nie befuelltes Passthrough-Dict -- das
war schlicht falsch modelliert, nicht nur unvollstaendig.

Offene, bewusst nicht geratene Fragen:
  - Ob p2maps ueberhaupt SigV4-Signierung braucht (vs. z.B. eines der
    Login-Tokens als Bearer-Header) ist eine Analogie-Annahme aus
    anderen /v1/-Endpunkten derselben Cloud-API-Familie, keine fuer
    p2maps selbst bestaetigte Tatsache.
  - SigV4 fuer POST-mit-Body ist MEINE Erweiterung des Originals (das
    nur GET signiert) -- siehe aws_sigv4.py's Docstring.
  - 403 -> Reauth-Retry ist 1:1 aus cloud_api.py's _aws_get() uebernommen
    (dort fuer Classic-REST-Endpunkte bestaetigt), hier zum ersten Mal
    auf p2maps angewendet.
"""
from __future__ import annotations

import json
import logging
import math
import urllib.parse
from collections.abc import Awaitable, Callable
from json.decoder import JSONDecodeError
from typing import Any

import aiohttp

from .auth import CloudCredentials, LoginResult
from .aws_sigv4 import AwsSigV4Signer
from .models import (
    FavoriteV1,
    HouseholdSchedule,
    LiveMapStreamInit,
    MapEditCommand,
    MapEditCommandV1,
    MissionCommandType,
    RoutineCommand,
    ScheduleOptions,
)

_LOGGER = logging.getLogger(__name__)

Relogin = Callable[[], Awaitable[LoginResult]]


class RestError(Exception):
    """Raised for any non-2xx response or unparseable body, with the
    raw response text attached where available."""

    def __init__(self, message: str, status: int | None = None, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.raw_response = raw_response


class PrimeRestClient:
    """Thin wrapper around the p2maps REST surface. Takes an existing
    aiohttp.ClientSession (same one used for auth.login(), so cookies/
    connection pooling are shared) rather than owning its own.

    credentials: AWS Cognito credentials (see auth.CloudCredentials) --
    every request is SigV4-signed with these, replacing the earlier
    (never-populated) generic auth_headers passthrough.

    relogin: optionaler async Callback, der bei HTTP 403 genau einmal
    aufgerufen wird, um neue credentials zu holen und den Aufruf zu
    wiederholen (siehe cloud_api.py's _aws_get() fuer das Original
    dieses Musters). None (Default) -- kein automatischer Retry, ein
    403 wird als RestError durchgereicht."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        http_base_auth: str,
        credentials: CloudCredentials,
        relogin: Relogin | None = None,
    ) -> None:
        self._session = session
        self._http_base_auth = http_base_auth.rstrip("/")
        self._credentials = credentials
        self._relogin = relogin

    async def get_map_metadata(self, p2map_id: str) -> dict[str, Any]:
        """GET /v1/p2maps/{p2mapId}. Response shape not modeled yet
        (P2MapMetadata's real fields weren't captured in the analysis
        session) -- returns the raw parsed JSON for now."""
        url = f"{self._http_base_auth}/v1/p2maps/{p2map_id}"
        return await self._request("GET", url)

    async def get_active_map_versions(self, blid: str) -> list[dict[str, Any]]:
        """GET /v1/p2maps?robotId={blid}&visible=true -- NEU (11. Juli),
        bestaetigt aus P2MapAPIFetching$fetchActiveVersions$2 (diese
        innere Coroutine-Klasse dekompilierte sauber, anders als die
        drei fetchPersistentMap/fetchLatestPersistentMap/fetchMissionMap
        -Aequivalente, siehe PRIME_APP_GAP_ANALYSIS Punkt C2).

        Rueckgabe ist eine Liste von Eintraegen mit mindestens "mapId"
        und "mapVersionId" (P2MapData -> P2MapIdentifier im Original) --
        hier als rohes JSON durchgereicht, kein eigenes Modell noch
        gebaut."""
        url = f"{self._http_base_auth}/v1/p2maps"
        data = await self._request("GET", url, query={"robotId": blid, "visible": "true"})
        return data if isinstance(data, list) else []

    async def get_map_geojson_link(self, map_id: str, map_version: str) -> dict[str, Any]:
        """NEU (11. Juli, dritte Sitzung -- nach erneutem, gezieltem
        Nachsuchen). Loest endlich auf, wie fetchPersistentMap/
        fetchLatestPersistentMap/fetchMissionMap an ihr tar.gz-
        Kartenbuendel kommen (siehe PRIME_APP_GAP_ANALYSIS Punkt C2,
        vorher als "nicht wirtschaftlich weiter aufloesbar" markiert --
        das war zu frueh aufgegeben, eine breitere Quellcode-Suche nach
        "/versions/" fand P2MapGeoJSONRequest.java direkt):

            GET /v1/p2maps/{map_id}/versions/{map_version}/geojson
                ?response_type=link

        Bestaetigt aus P2MapGeoJSONRequest.java: `response_type` ist ein
        Enum mit @SerialName("link")/@SerialName("binary") -- "link"
        (Default im Original) fragt eine vorsignierte Download-URL an
        (Accept: application/json, passt zufaellig zum ohnehin
        gesetzten Standard-Header). "binary" (direktes gzip, Accept:
        application/gzip,application/json) wird hier NICHT unterstuetzt
        -- braeuchte einen parametrisierbaren Accept-Header, den
        aws_sigv4.py aktuell nicht anbietet.

        Antwortform (welcher JSON-Schluessel die eigentliche URL
        traegt) bleibt UNBESTAETIGT -- keine eigene Response-Klasse im
        Quellcode gefunden, nur die Anfrage selbst. Rohes JSON
        durchgereicht."""
        url = f"{self._http_base_auth}/v1/p2maps/{map_id}/versions/{map_version}/geojson"
        return await self._request("GET", url, query={"response_type": "link"})

    async def download_map_bundle(self, url: str) -> bytes:
        """NEU (11. Juli, fuenfte Sitzung). Laedt das rohe tar.gz-
        Kartenbuendel von einer VORSIGNIERTEN URL (siehe
        get_map_geojson_link()) herunter.

        BEWUSST OHNE SigV4-Signierung -- bestaetigt aus P2MapAPI.
        MapUnpacker.fetchMapBundleContentHolder(P2MapIdentifier, URL):
        die App oeffnet die vorsignierte URL direkt
        (`mapURL.openConnection()`), ohne eigene Auth-Header. Vorsignierte
        URLs (S3-Stil) tragen ihre Authentifizierung typischerweise in
        eigenen Query-Parametern -- zusaetzliches Signieren waere nicht
        nur unnoetig, sondern wuerde die vom Server erwartete Signatur
        ueberschreiben/verfaelschen.

        Gibt die rohen Bytes zurueck (tar.gz-Archiv) -- Entpacken und
        Parsen siehe models.py::parse_map_bundle(). Getrennt von
        _request(), da diese URL nicht unter self._http_base_auth liegt
        (typischerweise ein S3-Bucket oder aehnlicher CDN-Host) und daher
        nicht das SigV4-Signierungsschema dieser Klasse durchlaufen
        sollte."""
        async with self._session.get(url) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise RestError(
                    f"HTTP {resp.status} downloading map bundle from {url}",
                    status=resp.status,
                    raw_response=text,
                )
            return await resp.read()

    async def set_map_name(self, p2map_id: str, name: str) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/settings, body {"type": name}."""
        return await self._post_settings(p2map_id, {"type": name})

    async def set_map_orientation(self, p2map_id: str, orientation_rad: float) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/settings, body {"user_orientation_rad": ...}.

        Original clamps the angle into (-pi, pi] before sending (see
        EditMapSettingsRequest$Command$SetUserPreferredOrientation$Companion
        .clampRadians in FINDINGS) -- replicated here rather than trusting
        the caller to have already done it.
        """
        two_pi = 6.283185307179586
        pi = 3.141592653589793
        clamped = orientation_rad - (math.ceil((orientation_rad + pi) / two_pi) - 1) * 2 * pi
        return await self._post_settings(p2map_id, {"user_orientation_rad": clamped})

    async def delete_map(self, p2map_id: str) -> dict[str, Any]:
        """NEU (11. Juli, dritte Sitzung) -- bestaetigt aus
        DeleteMapRequest.java: trotz des Namens KEIN HTTP-DELETE,
        sondern ein "soft delete" ueber denselben Settings-Endpunkt wie
        set_map_name()/set_map_orientation():

            POST /v1/p2maps/{p2mapId}/settings?trigger_fast_updates=true
            Body: {"visible": false}

        Feldname "visible" ohne @SerialName im Original gefunden --
        serialisiert vermutlich unter dem Property-Namen direkt, nicht
        geraten aber auch nicht durch eine explizite Annotation
        zusaetzlich abgesichert wie bei den uebrigen Feldern."""
        return await self._post_settings(p2map_id, {"visible": False})

    async def _post_settings(self, p2map_id: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._http_base_auth}/v1/p2maps/{p2map_id}/settings"
        return await self._request("POST", url, query={"trigger_fast_updates": "true"}, body=body)

    async def edit_map_v2(self, p2map_id: str, command: MapEditCommand) -> dict[str, Any]:
        """POST /v2/p2maps/{p2mapId}/versions -- ACHTUNG (11. Juli, vierte
        Sitzung): nach voller Neu-Dekompilierung der App bestaetigt, dass
        requestEditV2() im GESAMTEN App-Code NIE aufgerufen wird. Dieser
        Pfad existiert serverseitig vermutlich noch (der Endpunkt selbst
        ist keine Erfindung), wird aber von der aktuellen App-Version
        (2.2.4) nirgends genutzt. edit_map() (V1) ist der tatsaechlich
        aktive Pfad -- siehe dort. Bleibt hier verfuegbar, umbenannt von
        edit_map() zu edit_map_v2(), damit der Name nicht mehr suggeriert,
        dies sei der Standardweg.

        Response shape (die aktualisierte P2PersistentMap, laut den
        Kotlin-Repository-Interfaces) nicht modelliert -- rohes JSON."""
        url = f"{self._http_base_auth}/v2/p2maps/{p2map_id}/versions"
        return await self._request("POST", url, body=command.to_command_body())

    async def edit_map(self, p2map_id: str, command: MapEditCommandV1) -> dict[str, Any]:
        """POST /v1/p2maps/{p2mapId}/versions -- NEU (11. Juli, vierte
        Sitzung), der TATSAECHLICH AKTIVE Editier-Pfad (siehe
        models.py's V1-Abschnitt und PRIME_APP_GAP_ANALYSIS): jede
        einzelne Editier-Operation im App-Code ruft requestEditV1() auf,
        nie requestEditV2(). Ersetzt die vorherige Standardannahme
        (edit_map() = V2) -- der alte Pfad ist jetzt separat unter
        edit_map_v2() verfuegbar, mit einer Warnung, dass er unbenutzter
        Code ist.

        Response shape nicht modelliert -- rohes JSON. Das genaue
        Envelope-Format des Kommandos selbst ist eine Analogie-Annahme,
        siehe MapEditCommandV1's Modul-Docstring in models.py."""
        url = f"{self._http_base_auth}/v1/p2maps/{p2map_id}/versions"
        return await self._request("POST", url, body=command.to_v1_command_body())

    async def get_live_map_stream(self, blid: str) -> LiveMapStreamInit:
        """GET /v1/p2maps/livemap?robotId={blid} -> the MQTT topic to
        subscribe on the already-open AWS IoT connection (see
        mqtt_client.py's docstring and FINDINGS section 2)."""
        url = f"{self._http_base_auth}/v1/p2maps/livemap"
        data = await self._request("GET", url, query={"robotId": blid})
        return LiveMapStreamInit.from_json(data)

    # --- Favoriten (FavoriteV1) -----------------------------------------
    #
    # NEU (11. Juli, vierte Sitzung). Basis-URL und app_edition-Query-Param
    # bestaetigt aus FavoriteCommonRequest.java, siehe models.py's
    # Favoriten-Abschnitt fuer die vollstaendige Herleitung inkl. welche
    # HTTP-Methoden bestaetigt vs. angenommen sind.

    _FAVORITES_QUERY = {"app_edition": "1"}

    async def get_favorites(self) -> list[FavoriteV1]:
        """GET /v1/user/favorites?app_edition=1 -- BESTAETIGT (FetchFavoriteRequest,
        httpMethod = "GET")."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        data = await self._request("GET", url, query=self._FAVORITES_QUERY)
        raw_list = data if isinstance(data, list) else []
        return [self._favorite_from_json(item) for item in raw_list]

    async def create_favorite(self, favorite: FavoriteV1) -> dict[str, Any]:
        """POST /v1/user/favorites?app_edition=1 -- BESTAETIGT (achte
        Sitzung: CreateFavoriteRequest.<init> setzt httpMethod = "POST"
        direkt, androguard-Bytecode-Inspektion -- vorher nur angenommen,
        da jadx die Lambda-Klasse dafuer stillschweigend uebersprungen
        hatte). Antwort laut Kotlin-Interface ein FavoriteIdResponse --
        hier als rohes JSON durchgereicht."""
        url = f"{self._http_base_auth}/v1/user/favorites"
        return await self._request(
            "POST", url, query=self._FAVORITES_QUERY, body=favorite.to_json()
        )

    async def update_favorite(self, favorite_id: str, favorite: FavoriteV1) -> dict[str, Any]:
        """PUT /v1/user/favorites/{favoriteId}?app_edition=1 --
        BESTAETIGT (achte Sitzung: UpdateFavoriteRequest.<init> setzt
        httpMethod = "PUT" direkt -- vorher nur angenommen)."""
        url = f"{self._http_base_auth}/v1/user/favorites/{favorite_id}"
        return await self._request(
            "PUT", url, query=self._FAVORITES_QUERY, body=favorite.to_json()
        )

    async def delete_favorite(self, favorite_id: str) -> dict[str, Any]:
        """DELETE /v1/user/favorites/{favoriteId}?app_edition=1 --
        BESTAETIGT (DeleteFavoriteRequest, httpMethod = "DELETE")."""
        url = f"{self._http_base_auth}/v1/user/favorites/{favorite_id}"
        return await self._request("DELETE", url, query=self._FAVORITES_QUERY)

    async def order_favorite(
        self,
        favorite_id: str,
        *,
        insert_at: int | None = None,
        insert_before: str | None = None,
        insert_after: str | None = None,
    ) -> dict[str, Any]:
        """PUT /v1/user/favorites/{favoriteId}/order?app_edition=1 --
        BESTAETIGT (OrderFavoriteRequest, httpMethod = "PUT"). KORRIGIERT:
        insert_at/insert_before/insert_after sind QUERY-PARAMETER
        (snake_case: insert_at/insert_before/insert_after), nicht Body-
        Felder -- bytecode-bestaetigt aus OrderFavoriteRequest.
        getQueryParams() (via androguard/jadx: r0.put("insert_at", ...),
        r0.put("insert_before", ...), r0.put("insert_after", ...)). Kein
        httpBody bei diesem Request gefunden. Genau eine der drei wird
        vermutlich erwartet -- welche Kombination(en) der Server
        tatsaechlich akzeptiert, nicht bestaetigt."""
        url = f"{self._http_base_auth}/v1/user/favorites/{favorite_id}/order"
        query = dict(self._FAVORITES_QUERY)
        if insert_at is not None:
            query["insert_at"] = str(insert_at)
        if insert_before is not None:
            query["insert_before"] = insert_before
        if insert_after is not None:
            query["insert_after"] = insert_after
        return await self._request("PUT", url, query=query)

    async def get_mission_history(
        self,
        blid: str,
        *,
        max_reports: int | None = None,
        max_age: int | None = None,
        filter_type: str | None = None,
        exclusive_start_timestamp: int | None = None,
        supported_done_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """GET /v1/{blid}/missionhistory -- NEU (11. Juli, sechste
        Sitzung). BESTAETIGT aus FetchMissionHistoryRequest.java
        (httpMethod = "GET", urlString = "/v1/" + robotId +
        "/missionhistory"). Deckt sich mit dem gleichnamigen Endpunkt in
        ha_roomba_plus' cloud_api.py fuer Classic-Geraete -- Prime nutzt
        dasselbe URL-Muster.

        Query-Parameter alle bestaetigt (camelCase, Kotlin-Property-Name
        = Wire-Name, kein @SerialName gefunden): maxReports, maxAge,
        filterType, exclusiveStartTimestamp, supportedDoneCodes (Liste
        wird mit Komma verbunden -- bestaetigt aus
        ProvisioningErrorConstants.LAST_ERROR_INTERNAL_LINE_DELIMITER =
        ","). Response-Form JETZT modelliert (neunte Sitzung) --
        models.py::parse_mission_history() wandelt das Ergebnis dieser
        Methode in eine Liste typisierter MissionHistoryEntry-Objekte
        um (analog zu parse_map_bundle() -- getrennter, optionaler
        Schritt statt automatischer Umwandlung hier)."""
        url = f"{self._http_base_auth}/v1/{blid}/missionhistory"
        query: dict[str, str] = {}
        if max_reports is not None:
            query["maxReports"] = str(max_reports)
        if max_age is not None:
            query["maxAge"] = str(max_age)
        if filter_type is not None:
            query["filterType"] = filter_type
        if exclusive_start_timestamp is not None:
            query["exclusiveStartTimestamp"] = str(exclusive_start_timestamp)
        if supported_done_codes:
            query["supportedDoneCodes"] = ",".join(supported_done_codes)
        return await self._request("GET", url, query=query)

    async def get_schedules(self, household_id: str) -> dict[str, Any]:
        """GET /v1/households/{householdId}/settings/schedule -- NEU (11.
        Juli, sechste Sitzung). BESTAETIGT aus SchedulesCommonRequest/
        FetchSchedulesRequest (httpMethod = "GET", urlString ohne
        householdScheduleId-Suffix). Response-Form (SchedulesList) nicht
        modelliert -- rohes JSON."""
        url = f"{self._http_base_auth}/v1/households/{household_id}/settings/schedule"
        return await self._request("GET", url)

    async def delete_schedule(self, household_id: str, household_schedule_id: str) -> dict[str, Any]:
        """DELETE /v1/households/{householdId}/settings/schedule/{id} --
        BESTAETIGT aus DeleteSchedulesRequest (httpMethod = "DELETE")."""
        url = f"{self._http_base_auth}/v1/households/{household_id}/settings/schedule/{household_schedule_id}"
        return await self._request("DELETE", url)

    async def create_schedules(self, household_id: str, schedules: list[ScheduleOptions]) -> dict[str, Any]:
        """POST /v1/households/{householdId}/settings/schedule --
        BESTAETIGT (achte Sitzung: CreateSchedulesRequest.<init> setzt
        httpMethod = "POST" direkt, androguard-Bytecode-Inspektion --
        vorher nur angenommen). Feldstruktur ebenfalls bestaetigt, siehe
        models.py::ScheduleOptions."""
        url = f"{self._http_base_auth}/v1/households/{household_id}/settings/schedule"
        return await self._request("POST", url, body={"schedules": [s.to_json() for s in schedules]})

    async def update_schedules(
        self, household_id: str, household_schedule_id: str, schedules: list[HouseholdSchedule]
    ) -> dict[str, Any]:
        """PUT /v1/households/{householdId}/settings/schedule/{id} --
        BESTAETIGT (achte Sitzung: UpdateSchedulesRequest.<init> setzt
        httpMethod = "PUT" direkt -- vorher nur angenommen). Feldstruktur
        bestaetigt, siehe models.py::HouseholdSchedule."""
        url = f"{self._http_base_auth}/v1/households/{household_id}/settings/schedule/{household_schedule_id}"
        return await self._request("PUT", url, body={"schedules": [s.to_json() for s in schedules]})

    async def get_user_households(self) -> dict[str, Any]:
        """GET (angenommen, NICHT bestaetigt) /v1/user/households -- NEU
        (11. Juli, siebte Sitzung). WICHTIG: dieser Endpunkt wird von der
        aktuellen App-Version (2.2.4) NIRGENDS aufgerufen -- die
        Konstante HOUSEHOLDS_TEMPLATE existiert in NetworkConstants.java,
        aber keine einzige Request-Klasse nutzt sie. Trotzdem
        implementiert: eine unbenutzte App-interne Referenz heisst nicht,
        dass der Endpunkt serverseitig nicht existiert -- nur, dass diese
        App-Version ihn (noch) nicht braucht. HTTP-Methode und Antwortform
        daher reine REST-Konvention, nicht aus einer Request-Klasse
        bestaetigt wie bei den anderen Endpunkten hier."""
        url = f"{self._http_base_auth}/v1/user/households"
        return await self._request("GET", url)

    async def get_dnd_settings(self, household_id: str) -> dict[str, Any]:
        """GET /v1/households/{householdId}/settings/dnd -- NEU (11.
        Juli, sechste Sitzung). BESTAETIGT aus DNDGetRequest (httpMethod
        = "GET"). Response-Form JETZT modelliert (neunte Sitzung) --
        models.py::DNDStatusResponse.from_json(). WICHTIG: siehe
        DNDStatusResponse's Docstring zur Unterscheidung von der
        separaten DNDSchedule-Klassenfamilie."""
        url = f"{self._http_base_auth}/v1/households/{household_id}/settings/dnd"
        return await self._request("GET", url)

    async def set_dnd_settings(self, household_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """PUT /v1/households/{householdId}/settings/dnd -- BESTAETIGT
        aus DNDPutRequest (httpMethod = "PUT"). Genaues Body-Format
        (Zeitfenster-Felder) nicht weiter untersucht -- rohes JSON
        durchgereicht."""
        url = f"{self._http_base_auth}/v1/households/{household_id}/settings/dnd"
        return await self._request("PUT", url, body=settings)

    async def get_cleaning_profiles(self, asset_id: str, p2map_id: str) -> dict[str, Any]:
        """GET /v1/profiles -- NEU (11. Juli, sechste Sitzung). BESTAETIGT
        aus CleaningProfileRequest (httpMethod = "GET", Query-Parameter
        assetId + p2mapId -- Namen aus Kotlin-Property, nicht
        @SerialName-verifiziert). Liefert vermutlich die fuer diese
        Karte/dieses Geraet verfuegbaren Reinigungsprofile. Response-Form
        JETZT modelliert (neunte Sitzung) --
        models.py::CleaningProfile.from_json() pro Eintrag."""
        url = f"{self._http_base_auth}/v1/profiles"
        return await self._request("GET", url, query={"assetId": asset_id, "p2mapId": p2map_id})

    async def get_default_routines(self, p2map_id: str) -> dict[str, Any]:
        """GET /v1/p2maps/{p2mapId}/routines/defaults -- NEU (11. Juli,
        sechste Sitzung). Automatisch generierte Reinigungsvorschlaege
        pro Karte (z.B. "ganze Wohnung", "nur Kueche"). Response-Form
        JETZT modelliert (neunte Sitzung) --
        models.py::parse_default_routines()."""
        url = f"{self._http_base_auth}/v1/p2maps/{p2map_id}/routines/defaults"
        return await self._request("GET", url)

    async def get_robot_parts(self, blid: str) -> dict[str, Any]:
        """GET /v1/robots/{blid}/parts -- NEU (15. Sitzung). BESTAETIGT
        aus der tatsaechlichen APK-Konfigurationsdatei
        (res/raw/base_roomba_config.json, commandId "GetRobotParts":
        httpMethod=GET, urlPath="/v1/robots/%s/parts",
        networkList=["awsApiGateway"]) -- eine Primaerquelle, keine
        Bytecode-Interpretation. Liefert vermutlich Verschleissteil-
        Zustaende (Filter/Buerste/Akku-Nutzungsdauer o.ae.) -- Response-
        Form nicht weiter untersucht, rohes JSON."""
        url = f"{self._http_base_auth}/v1/robots/{blid}/parts"
        return await self._request("GET", url)

    async def reset_robot_parts(self, blid: str) -> dict[str, Any]:
        """POST /v1/robots/{blid}/parts -- NEU (15. Sitzung). BESTAETIGT
        aus derselben Konfigurationsdatei (commandId "ResetRobotParts",
        httpMethod=POST, identischer urlPath wie get_robot_parts()).
        Setzt vermutlich Verschleissteil-Zaehler zurueck (z.B. nach
        Teiletausch) -- Body-Form nicht untersucht, rohes JSON
        durchgereicht."""
        url = f"{self._http_base_auth}/v1/robots/{blid}/parts"
        return await self._request("POST", url)

    async def get_serial_number_data(self, blid: str) -> dict[str, Any]:
        """GET /v1/robots?robot_id={blid} -- NEU (15. Sitzung). BESTAETIGT
        aus derselben Konfigurationsdatei (commandId "GetSerialNumberData",
        httpMethod=GET, urlPath="/v1/robots?robot_id=%s"). Liefert
        vermutlich Seriennummer/Hardware-Identifikationsdaten -- Response-
        Form nicht weiter untersucht, rohes JSON."""
        url = f"{self._http_base_auth}/v1/robots"
        return await self._request("GET", url, query={"robot_id": blid})

    async def poll_echo_value(self, blid: str) -> dict[str, Any]:
        """POST /v1/robots/{blid}/echo -- NEU (16. Sitzung). BESTAETIGT
        aus base_roomba_config.json (commandId "PollEchoValueCommand,Set",
        httpMethod=POST, urlPath="/v1/robots/%s/echo"). Passt zur
        "Echo"-Funktion ("finde meinen Roboter" -- Signalton/Ansage) --
        deckt sich mit dem SetRoombaEchoAwsIotSerializer-Fund aus der
        nativen Analyse. Body-Form unbekannt -- vermutlich leer oder
        ein einfacher Trigger, kein Payload noetig fuer den einfachsten
        Fall. Kein Body mitgegeben, bis Gegenteil bestaetigt ist."""
        url = f"{self._http_base_auth}/v1/robots/{blid}/echo"
        return await self._request("POST", url)

    async def get_time_estimates(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/time-estimates -- NEU (16. Sitzung). BESTAETIGT aus
        base_roomba_config.json (commandId "GetTimeEstimates",
        httpMethod=POST trotz "read": true -- vermutlich POST, weil die
        Anfrage einen Body braucht, um zu wissen, WELCHE Mission/Raeume
        geschaetzt werden sollen, nicht weil sie schreibend waere).
        Body-Form nicht untersucht -- rohes dict durchgereicht, vom
        Aufrufer selbst zu befuellen (vermutlich robot_id + Regionen/
        Kommandotyp, in Analogie zu RoutineCommand)."""
        url = f"{self._http_base_auth}/v1/time-estimates"
        return await self._request("POST", url, body=body)

    async def reset_robot(self, blid: str) -> dict[str, Any]:
        """POST /v1/{blid}/reset -- NEU (16. Sitzung). BESTAETIGT aus
        base_roomba_config.json (commandId "ResetRobotCommand",
        httpMethod=POST, networkList enthaelt sowohl awsApiGateway als
        auch lss -- existiert also fuer Classic UND Prime). WARNUNG:
        vermutlich ein Werksreset oder zumindest ein signifikanter
        Zuruecksetzungsvorgang -- Name und "write": true legen nahe,
        dass dies eine ECHTE, moeglicherweise folgenreiche Aktion am
        Geraet ausloest. Nie live getestet. Nicht leichtfertig aufrufen."""
        url = f"{self._http_base_auth}/v1/{blid}/reset"
        return await self._request("POST", url)

    async def get_notifications(self, blid: str, app_version: str = "1.0") -> dict[str, Any]:
        """GET /v1/robots/{blid}/timeline -- NEU (16. Sitzung). BESTAETIGT
        aus base_roomba_config.json (commandId "GetNotifications",
        urlPath="/v1/robots/%s/timeline?event_type=HKC&
        details_type_filter=all&app_version=%s&limit=50"). "HKC" als
        event_type-Wert nicht aufgeloest (Abkuerzung unbekannt) --
        1:1 aus der Konfigurationsdatei uebernommen, nicht geraten.
        app_version wird von der echten App mitgeschickt, vermutlich
        fuer Kompatibilitaets-/Feature-Flags server-seitig -- ein
        Platzhalterwert sollte funktionieren, aber unbestaetigt."""
        url = f"{self._http_base_auth}/v1/robots/{blid}/timeline"
        return await self._request(
            "GET",
            url,
            query={
                "event_type": "HKC",
                "details_type_filter": "all",
                "app_version": app_version,
                "limit": "50",
            },
        )

    @staticmethod
    def _favorite_from_json(data: dict[str, Any]) -> FavoriteV1:
        """Baut ein FavoriteV1 aus rohem JSON. Bewusst tolerant (.get()
        ueberall) -- keine echte Serverantwort je gesehen, um zu wissen,
        welche Felder wirklich immer vorhanden sind."""
        command_defs_raw = data.get("commanddefs") or []
        return FavoriteV1(
            favorite_id=data.get("favorite_id"),
            name=data.get("name"),
            color=data.get("color"),
            icon=data.get("icon"),
            order=data.get("order"),
            display_order=data.get("display_order"),
            is_default=bool(data.get("default", False)),
            is_deleted=bool(data.get("deleted", False)),
            is_hidden=bool(data.get("hidden", False)),
            modification_secs=data.get("modification_secs"),
            version=data.get("version"),
            command_defs=[
                RoutineCommand(
                    command_type=MissionCommandType(c["command"]),
                    asset_id=c.get("robot_id", ""),
                    map_id=c.get("p2map_id"),
                    ordered=c.get("ordered", 0),
                    id_multipolys=c.get("id_multipolys"),
                    params=c.get("params"),
                    regions=c.get("regions"),
                    pmap_version_id=c.get("user_p2mapv_id"),
                    clean_all=bool(c.get("select_all", False)),
                    spot_geometry=c.get("geom"),
                    favorite_id=c.get("favorite_id"),
                )
                for c in command_defs_raw
            ],
            creation_timestamp=data.get("creation_timestamp"),
            last_user_modified=data.get("last_user_modified"),
            last_modified=data.get("last_modified"),
            time_estimates=None,
        )

    def _signer(self) -> AwsSigV4Signer:
        return AwsSigV4Signer(
            self._credentials.access_key_id,
            self._credentials.secret_key,
            self._credentials.session_token,
        )

    async def _request(
        self,
        method: str,
        url: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        _retry: bool = True,
    ) -> Any:
        parsed = urllib.parse.urlparse(url)
        body_str = json.dumps(body) if body is not None else ""
        headers = self._signer().signed_headers(
            method=method,
            service="execute-api",
            region=self._credentials.region,
            host=parsed.netloc,
            path=parsed.path,
            query_params=query or {},
            body=body_str,
        )

        request_kwargs: dict[str, Any] = {"params": query, "headers": headers}
        if body is not None:
            # NOTE: must send the EXACT same bytes we hashed for the
            # signature -- aiohttp's json= would re-serialize
            # independently (possibly different key order/whitespace)
            # and invalidate the signature. data= sends our own string
            # verbatim.
            request_kwargs["data"] = body_str.encode()

        method_fn = getattr(self._session, method.lower())
        async with method_fn(url, **request_kwargs) as resp:
            if resp.status == 403 and _retry and self._relogin is not None:
                _LOGGER.debug("roombapy-prime REST: 403 -- reauthenticating")
                login_result = await self._relogin()
                self._credentials = login_result.credentials
                return await self._request(method, url, query, body, _retry=False)
            return await self._parse_response(resp)

    async def _parse_response(self, resp: aiohttp.ClientResponse) -> Any:
        text = await resp.text()
        if resp.status >= 400:
            raise RestError(f"HTTP {resp.status} from {resp.url}", status=resp.status, raw_response=text)
        if not text:
            return {}
        try:
            return json.loads(text)
        except JSONDecodeError as exc:
            raise RestError(
                f"Non-JSON response from {resp.url}: {text[:300]}", status=resp.status, raw_response=text
            ) from exc
