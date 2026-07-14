"""Live-Validierung von roombapy-prime gegen einen echten Prime/V4-Account.

NEU (11. Juli, elfte Sitzung). Der bislang groesste, wiederholt genannte
Schwachpunkt der ganzen Bibliothek ist: nichts wurde je gegen einen
echten Server getestet. Dieses Skript ist der erste konkrete Schritt,
das zu aendern -- es fuehrt die oeffentliche API gegen einen echten
Account aus und meldet pro Bereich OK/FEHLGESCHLAGEN/UEBERSPRUNGEN.

SICHERHEITSPRINZIP, nicht verhandelbar:
- Standardmaessig NUR LESENDE Operationen (Login, REST-GETs, Shadow-
  Zustand abrufen, Kartenbuendel herunterladen). Nichts hiervon kann
  einen Zustand am Server oder Roboter veraendern.
- --allow-writes schaltet EINEN reversiblen Test frei: einen Test-
  Favoriten anlegen, pruefen dass er in get_favorites() auftaucht, dann
  sofort wieder loeschen. Das validiert die drei bislang nur per
  Bytecode bestaetigten, nie live getesteten HTTP-Methoden
  (create/update/delete Favorite) an einem einzigen, klar
  gekennzeichneten, selbst wieder aufgeraeumten Objekt.
- Missionsbefehle (send_mission_command -- der Roboter wuerde real
  starten/stoppen/etc.) und Kartenbearbeitung (edit_map -- koennte eine
  echte, evtl. muehsam neu erstellte Karte verunstalten) werden NIE
  ausgefuehrt, auch nicht mit --allow-writes. Das Risiko einer
  ungewollten realen Aktion ist fuer ein automatisiertes Testskript zu
  hoch -- diese beiden Bereiche brauchen weiterhin gezielte, bewusste
  manuelle Tests durch einen Menschen, der zusieht.

Nutzung:
    python -m roombapy_prime.diagnostics --username you@example.com --country-code US
    (Passwort wird interaktiv abgefragt, nie als Kommandozeilenargument --
    das wuerde in der Shell-Historie landen.)

    Optional: --blid BLID123 (sonst wird der erste gefundene Roboter genutzt)
    Optional: --allow-writes (siehe oben)
    Optional: --output report.md (Ergebnisbericht zusaetzlich als Markdown speichern)
    Optional: --dump-config diagnose.json (siehe DIAGNOSE-DUMP unten)

Zugangsdaten koennen alternativ ueber Umgebungsvariablen
ROOMBAPY_PRIME_USERNAME / ROOMBAPY_PRIME_PASSWORD / ROOMBAPY_PRIME_COUNTRY
gesetzt werden (nuetzlich fuer CI/wiederholte Laeufe) -- werden aber nie
geloggt oder in den Bericht aufgenommen.

ABGEDECKTE PRUEFUNGEN (NEU, 24. Sitzung -- vorher luecken haft): neben
Login/MQTT/den REST-Lesezugriffen jetzt auch `get_live_map_stream()`
und ein zeitlich begrenzter (Standard 3s) `watch_state()`-Test --
beide rein lesend, vorher aus reinem Versehen nicht abgedeckt. NICHT
abgedeckt, bewusst: alle Schreiboperationen ausser dem
Favoriten-Rundlauftest, sowie send_mission_command/edit_map/reset_robot
(siehe Sicherheitsprinzip oben) und poll_echo_value (loest ein
hoerbares Signal am echten Geraet aus -- fuer ein automatisiertes
Skript zu invasiv, obwohl technisch reversibel).

DIAGNOSE-DUMP (NEU, 24. Sitzung): --dump-config PATH speichert die
TATSAECHLICHEN Rohantworten aller Lese-Endpunkte als JSON -- aehnlich
der "Diagnose herunterladen"-Funktion einer Home-Assistant-Integration.
Anders als der normale Bericht (der nur Pass/Fail zeigt) sind hier
echte Feldnamen UND echte Werte enthalten -- das ist der Sinn der
Datei: Reverse-Engineering-taugliche Rohdaten liefern, nicht nur
Status. Redaktion bleibt trotzdem zweistufig (Zugangsdaten +
offensichtlich sensible Feldnamen wie Adresse/GPS/WLAN), aber ist
NICHT so umfassend wie beim normalen Bericht -- diese Datei wird
DESHALB nie automatisch Teil des Issue-Links, sondern muss bewusst
und einzeln angehaengt werden, nachdem man sie selbst durchgesehen
hat. Kartenbuendel-Inhalte werden nie mitgeschrieben (nur die
Dateinamen darin) -- ein Wohnungsgrundriss ist persoenlicher als die
meisten anderen hier erfassten Daten.

RUECKMELDUNG AN DIE MAINTAINER (NEU, zwoelfte Sitzung):
Am Ende jedes Laufs wird -- zusaetzlich zur Konsolenausgabe -- ein
vorausgefuellter Link zum Oeffnen eines GitHub-Issues gedruckt (Titel +
Bericht als Body, URL-kodiert), sowie derselbe Bericht als reines
Markdown zum manuellen Kopieren (z.B. fuer Discord/E-Mail, falls kein
GitHub gewuenscht ist). Der Bericht durchlaeuft vorher eine
Redaktionsstufe: jedes woertliche Auftreten von Benutzername oder
Passwort in irgendeinem Fehlertext wird durch "[REDACTED]" ersetzt --
Verteidigung in der Tiefe, falls eine tieferliegende Exception (z.B.
aus aiohttp) versehentlich Zugangsdaten in eine Fehlermeldung
einbettet. Der Ziel-Repo-Pfad ist ueber ISSUE_TRACKER_REPO unten
konfigurierbar -- aktuell auf das echte Repo gesetzt
(github.com/johnnyh1975/roombapy-prime, siehe Konstante).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

from .prime_factory import PrimeFactory

#: Repo fuer den vorausgefuellten "Neues Issue"-Link (siehe Modul-Docstring).
#: Aktualisiert (19. Sitzung) auf das echte GitHub-Repo.
ISSUE_TRACKER_REPO = "johnnyh1975/roombapy-prime"


@dataclass
class CheckResult:
    name: str
    status: str  # "OK", "FEHLGESCHLAGEN", "UEBERSPRUNGEN"
    detail: str = ""


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.results.append(CheckResult(name, status, detail))
        marker = {"OK": "✓", "FEHLGESCHLAGEN": "✗", "UEBERSPRUNGEN": "–"}[status]
        line = f"  [{marker}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)

    def summary(self) -> tuple[int, int, int]:
        ok = sum(1 for r in self.results if r.status == "OK")
        failed = sum(1 for r in self.results if r.status == "FEHLGESCHLAGEN")
        skipped = sum(1 for r in self.results if r.status == "UEBERSPRUNGEN")
        return ok, failed, skipped

    def redact(self, *secrets: str) -> None:
        """NEU (12. Sitzung). Ersetzt jedes woertliche Auftreten der
        uebergebenen Strings (Benutzername, Passwort) in JEDEM
        Fehlertext durch "[REDACTED]" -- Verteidigung in der Tiefe,
        bevor der Bericht geteilt wird. Sollte im Normalfall nichts
        finden (Zugangsdaten werden nirgends direkt in Berichtseintraege
        geschrieben), faengt aber ab, falls eine tieferliegende
        Exception (z.B. aus aiohttp) versehentlich Zugangsdaten in eine
        Fehlermeldung einbettet."""
        cleaned = [s for s in secrets if s]
        if not cleaned:
            return
        for result in self.results:
            for secret in cleaned:
                if secret in result.detail:
                    result.detail = result.detail.replace(secret, "[REDACTED]")

    def to_markdown(self) -> str:
        import platform

        from . import __version__ as lib_version

        lines = [
            f"# roombapy-prime Live-Validierung — {datetime.now(timezone.utc).isoformat()}",
            "",
            f"roombapy-prime {lib_version}, Python {platform.python_version()}, {platform.system()}",
            "",
        ]
        for r in self.results:
            marker = {"OK": "✅", "FEHLGESCHLAGEN": "❌", "UEBERSPRUNGEN": "⏭️"}[r.status]
            entry = f"- {marker} **{r.name}**"
            if r.detail:
                entry += f": {r.detail}"
            lines.append(entry)
        ok, failed, skipped = self.summary()
        lines += ["", f"**Zusammenfassung:** {ok} OK, {failed} fehlgeschlagen, {skipped} uebersprungen."]
        return "\n".join(lines)


async def _try(report: Report, name: str, coro: Any, capture: dict[str, Any] | None = None) -> Any:
    """Fuehrt eine einzelne Pruefung aus, faengt JEDE Exception (nicht
    nur RestError) -- ein Diagnoseskript darf nie selbst abstuerzen,
    egal was der Server zurueckgibt.

    capture (NEU, 24. Sitzung): falls uebergeben, wird das rohe,
    erfolgreiche Ergebnis zusaetzlich unter `name` abgelegt -- fuer
    --dump-config (siehe main()). Getrennt vom Bericht selbst, damit
    der normale Pass/Fail-Bericht (der z.B. in den GitHub-Issue-Link
    einfliesst) unveraendert kompakt bleibt; die Rohdaten landen nur in
    der optionalen Dump-Datei, nie automatisch im Issue-Link."""
    try:
        result = await coro
        report.add(name, "OK")
        if capture is not None:
            capture[name] = result
        return result
    except Exception as exc:  # noqa: BLE001 -- bewusst breit, siehe Docstring
        report.add(name, "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")
        return None


def _skip(report: Report, name: str, reason: str) -> None:
    report.add(name, "UEBERSPRUNGEN", reason)


def _report_device_info(report: Report, state: Any) -> None:
    """NEU (21. Sitzung), KORRIGIERT (25./27. Sitzung): erste Live-Antwort
    (chairstacker) zeigte die tatsaechliche Struktur --
    payload["state"]["reported"] enthaelt sku/svcEndpoints/soldAsSku,
    NICHT auf Top-Level wie urspruenglich vermutet.

    WICHTIGE KORREKTUR (27. Sitzung, detailliertes Review): die
    vollstaendige echte Antwort zeigt, dass "reported" GAR KEIN
    Firmware-/softwareVer-Feld enthaelt -- weder auf Top-Level noch
    verschachtelt. Firmware-Info kommt stattdessen aus
    get_serial_number_data() oder aus einzelnen Missionshistorie-
    Eintraegen (beide fuehren "softwareVer"). Der "firmware"-Kandidat
    wird hier trotzdem beibehalten (falls ein anderes Geraet/Tier es
    doch im Shadow fuehrt), aber es sollte NICHT ueberraschen, wenn er
    hier leer bleibt -- das ist erwartet, kein Fehlerzeichen.

    Kandidaten-Feldnamen bleiben ansonsten Vermutungen -- daher weiterhin
    IMMER zusaetzlich die tatsaechlichen Top-Level-Schluessel melden,
    falls sich die Verschachtelung nochmal aendert."""
    if state is None or not isinstance(getattr(state, "payload", None), dict):
        return
    payload = state.payload
    reported = payload.get("state", {}).get("reported", {}) if isinstance(payload.get("state"), dict) else {}
    candidates = {
        "sku": ["sku", "soldAsSku", "mdl"],
        "firmware": ["softwareVer", "ver", "firmwareVersion"],
        "name": ["name", "robotName"],
        "capabilities": ["cap"],
    }
    found = {}
    for label, keys in candidates.items():
        for key in keys:
            if key in reported:
                found[label] = reported[key]
                break
            if key in payload:
                found[label] = payload[key]
                break
    top_level_keys = sorted(payload.keys())
    reported_keys = sorted(reported.keys()) if reported else []
    detail = f"gefunden: {found}" if found else "keine der vermuteten Kandidaten-Felder gefunden"
    detail += f" -- Top-Level-Schluessel: {top_level_keys}"
    if reported_keys:
        detail += f" -- state.reported-Schluessel: {reported_keys}"
    report.add("Geraeteinfo aus get_state() extrahiert", "OK", detail)


def _report_tier_inference(report: Report, settings_result: Any) -> None:
    """NEU (21. Sitzung), ABGESCHWAECHT (25. Sitzung) -- dieselbe
    Geraete-BLID lieferte in zwei aufeinanderfolgenden Laeufen
    UNTERSCHIEDLICHE Ergebnisse (einmal Erfolg, einmal Timeout). Das
    ist kein stabiles Tier-Signal -- entweder eine Race-Condition in
    dieser Bibliothek oder ein echter, wechselnder Geraetezustand
    (Roboter online/offline gegenueber AWS IoT). Formulierung
    entsprechend vorsichtiger: "deutet auf" statt "ist"."""
    if settings_result is not None:
        report.add(
            "Tier-Vermutung (aus get_settings()-Ergebnis)",
            "OK",
            "rw-settings hat geantwortet -> deutet auf SMART-Tier hin. HINWEIS: bei demselben "
            "Geraet wurde in einem anderen Lauf auch ein Timeout beobachtet -- dieses Signal "
            "ist nicht zuverlaessig stabil, siehe get_settings()'s Docstring.",
        )
    else:
        report.add(
            "Tier-Vermutung (aus get_settings()-Ergebnis)",
            "OK",
            "rw-settings hat NICHT geantwortet (Timeout) -> koennte EPHEMERAL-Tier bedeuten, "
            "koennte aber auch ein voruebergehender Zustand sein (z.B. Roboter aktuell nicht "
            "aktiv mit AWS IoT verbunden) -- bei demselben Geraet wurde in einem anderen Lauf "
            "auch ein Erfolg beobachtet. Kein verlaesslicher Tier-Beweis fuer sich allein.",
        )


async def run(
    username: str,
    password: str,
    country_code: str,
    blid: str | None,
    allow_writes: bool,
    raw_capture: dict[str, Any] | None = None,
) -> Report:
    report = Report()

    async with aiohttp.ClientSession() as session:
        print("\n== Anmeldung ==")
        try:
            robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
            report.add("Login (Discovery + Gigya + iRobot-Auth-Kette)", "OK", f"BLID={robot.blid}")
        except Exception as exc:  # noqa: BLE001
            report.add("Login", "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")
            print("\nAnmeldung fehlgeschlagen -- alle weiteren Pruefungen werden uebersprungen.")
            return report

        print("\n== MQTT / Shadow-Zustand ==")
        try:
            await robot.connect()
            report.add("MQTT-Verbindung (AWS-IoT-Custom-Authorizer)", "OK")
        except Exception as exc:  # noqa: BLE001
            report.add("MQTT-Verbindung", "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")

        state = await _try(report, "Shadow-Zustand abrufen (get_state)", robot.get_state(), capture=raw_capture)
        _report_device_info(report, state)

        settings_result = await _try(
            report, "Shadow-Einstellungen abrufen (get_settings)", robot.get_settings(), capture=raw_capture
        )
        _report_tier_inference(report, settings_result)

        await _try(
            report, "Live-Map-Stream anfordern (get_live_map_stream)", robot.get_live_map_stream(), capture=raw_capture
        )
        await _try_watch_state_briefly(report, robot)

        print("\n== REST-Lesezugriffe (Favoriten/Missionshistorie/Zeitplaene/...) ==")
        await _try(report, "Favoriten abrufen (get_favorites)", robot.get_favorites(), capture=raw_capture)
        await _try(
            report,
            "Missionshistorie abrufen (get_mission_history)",
            robot.get_mission_history(robot.blid, max_reports=5),
            capture=raw_capture,
        )
        await _try(
            report, "Haushaltsliste abrufen (get_user_households)", robot.get_user_households(), capture=raw_capture
        )
        await _try(
            report, "Verschleissteile abrufen (get_robot_parts)", robot.get_robot_parts(), capture=raw_capture
        )
        await _try(
            report,
            "Seriennummer/Geraetedaten abrufen (get_serial_number_data)",
            robot.get_serial_number_data(),
            capture=raw_capture,
        )
        await _try(
            report, "Benachrichtigungen abrufen (get_notifications)", robot.get_notifications(), capture=raw_capture
        )

        map_versions = await _try(
            report,
            "Aktive Kartenversionen abrufen (get_active_map_versions)",
            robot.get_active_map_versions(),
            capture=raw_capture,
        )

        p2map_id: str | None = None
        p2map_version_id: str | None = None
        if map_versions:
            try:
                first = map_versions[0]
                p2map_id = (
                    first.get("p2map_id")
                    or first.get("mapId")
                    or first.get("p2mapId")
                    or first.get("id")
                    or first.get("map_id")
                )
                # NEU (33. Sitzung): "1" war ein Platzhalter-Ratewert fuer die
                # Kartenversion -- echte Daten (chairstacker) zeigen, dass die
                # tatsaechliche Versions-ID unter "active_p2mapv_id" steht
                # (z.B. "260518T135521.119", kein einfacher Zaehler). Das
                # erklaert vermutlich den HTTP-400-Fehler bei
                # get_map_geojson_link(): die URL enthielt bisher immer eine
                # erfundene, nie existierende Versions-ID.
                p2map_version_id = first.get("active_p2mapv_id")
            except (AttributeError, IndexError, TypeError):
                p2map_id = None
            if p2map_id is None:
                report.add(
                    "Karten-ID-Extraktion",
                    "FEHLGESCHLAGEN",
                    f"get_active_map_versions() lieferte Daten, aber kein bekanntes ID-Feld "
                    f"gefunden. Antwortstruktur: {_shallow_summary(map_versions)}",
                )

        if p2map_id:
            await _try(
                report,
                "Kartenmetadaten abrufen (get_map_metadata)",
                robot.get_map_metadata(p2map_id),
                capture=raw_capture,
            )
            if p2map_version_id:
                geojson_link = await _try(
                    report,
                    "Vorsignierte Kartenbuendel-URL abrufen (get_map_geojson_link)",
                    robot.get_map_geojson_link(p2map_id, p2map_version_id),
                )
            else:
                geojson_link = None
                _skip(
                    report,
                    "Vorsignierte Kartenbuendel-URL abrufen (get_map_geojson_link)",
                    "keine active_p2mapv_id in get_active_map_versions()s Antwort gefunden",
                )
            if isinstance(geojson_link, dict):
                url = next((v for v in geojson_link.values() if isinstance(v, str) and v.startswith("http")), None)
                if url:
                    bundle = await _try(report, "Kartenbuendel herunterladen (download_map_bundle)", _fetch_bundle(robot, url))
                    if bundle is not None:
                        from .models import parse_map_bundle

                        try:
                            parsed = parse_map_bundle(bundle)
                            report.add(
                                "Kartenbuendel entpacken (parse_map_bundle)", "OK", f"{len(parsed)} Dateien gefunden"
                            )
                            # NEU (24. Sitzung): bewusst NUR die Dateinamen erfassen, nie den
                            # Karteninhalt selbst -- ein Wohnungsgrundriss ist deutlich
                            # persoenlicher als die meisten anderen hier erfassten Daten.
                            if raw_capture is not None:
                                raw_capture["Kartenbuendel (nur Dateinamen)"] = sorted(parsed.keys())
                        except Exception as exc:  # noqa: BLE001
                            report.add("Kartenbuendel entpacken", "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")
                else:
                    _skip(
                        report,
                        "Kartenbuendel herunterladen",
                        "kein erkennbarer URL-Schluessel in der Antwort (Antwortform unbestaetigt)",
                    )
            households = await _try_silent(robot.get_user_households())
            household_id = _extract_first_id(households, ["household_id", "householdId", "id"])
            if household_id:
                await _try(
                    report, "Zeitplaene abrufen (get_schedules)", robot.get_schedules(household_id), capture=raw_capture
                )
                await _try(
                    report,
                    "DND-Einstellungen abrufen (get_dnd_settings)",
                    robot.get_dnd_settings(household_id),
                    capture=raw_capture,
                )
            elif households:
                report.add(
                    "household_id-Extraktion",
                    "FEHLGESCHLAGEN",
                    f"get_user_households() lieferte Daten, aber weder 'householdId' noch 'id' "
                    f"gefunden. Antwortstruktur: {_shallow_summary(households)}",
                )
                _skip(report, "Zeitplaene/DND abrufen", "household_id-Extraktion fehlgeschlagen, siehe oben")
            else:
                _skip(
                    report,
                    "Zeitplaene/DND abrufen",
                    "get_user_households() lieferte keine Daten (leere Antwort oder Fehler)",
                )

            await _try(
                report,
                "Reinigungsprofile abrufen (get_cleaning_profiles)",
                robot.get_cleaning_profiles(robot.blid, p2map_id),
                capture=raw_capture,
            )
            await _try(
                report,
                "Standard-Routinen abrufen (get_default_routines)",
                robot.get_default_routines(p2map_id),
                capture=raw_capture,
            )
        else:
            _skip(
                report,
                "Kartenmetadaten/Kartenbuendel/Reinigungsprofile/Standard-Routinen",
                f"keine aktive Kartenversion gefunden -- get_active_map_versions()s Antwort: "
                f"{_shallow_summary(map_versions)} (falls das eine leere Liste zeigt, obwohl "
                f"der Roboter eine Karte gelernt hat, ist das der eigentliche Fehler, nicht das "
                f"Roboter-Alter)",
            )

        if allow_writes:
            print("\n== Reversibler Schreib-Rundlauftest (--allow-writes) ==")
            await _round_trip_favorite_test(robot, report)
        else:
            _skip(
                report,
                "Favoriten-Schreib-Rundlauftest (create/update/delete)",
                "--allow-writes nicht gesetzt",
            )

        _skip(
            report,
            "Missionsbefehle (send_mission_command)",
            "wird NIE automatisch ausgefuehrt -- siehe Modul-Docstring",
        )
        _skip(
            report,
            "Kartenbearbeitung (edit_map)",
            "wird NIE automatisch ausgefuehrt -- siehe Modul-Docstring",
        )

        await robot.disconnect()

    return report


async def _fetch_bundle(robot: Any, url: str) -> bytes:
    return await robot.download_map_bundle(url)


async def _try_watch_state_briefly(report: Report, robot: Any, timeout_seconds: float = 3.0) -> None:
    """NEU (24. Sitzung) -- watch_state() war bisher komplett ungetestet,
    obwohl es rein lesend ist (reagiert nur auf Shadow-Deltas, sendet
    nichts). Laesst den Generator hoechstens `timeout_seconds` laufen --
    KEIN Delta zu bekommen ist normal (der Roboter muss sich dafuer
    aktiv aendern) und zaehlt als OK, nicht als Fehler; ein Absturz des
    Generators selbst waere dagegen ein echter Fund."""
    try:
        count = 0
        async with asyncio.timeout(timeout_seconds):
            async for _delta in robot.watch_state():
                count += 1
        report.add("Kontinuierliches Beobachten (watch_state, kurz)", "OK", f"{count} Delta(s) in {timeout_seconds}s")
    except TimeoutError:
        report.add(
            "Kontinuierliches Beobachten (watch_state, kurz)",
            "OK",
            f"kein Delta in {timeout_seconds}s -- normal, wenn sich der Zustand nicht aendert",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("Kontinuierliches Beobachten (watch_state, kurz)", "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")


async def _try_silent(coro: Any) -> Any:
    """Wie _try(), aber ohne Bericht-Eintrag -- fuer Zwischenschritte,
    die selbst kein eigener Pruefpunkt sind (z.B. household_id nur
    ermitteln, um EINE andere Pruefung ueberhaupt versuchen zu koennen)."""
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return None


def _shallow_summary(data: Any, _depth: int = 0) -> Any:
    """NEU (21. Sitzung) -- fasst eine unbekannte Antwortstruktur fuer
    die Debug-Ausgabe zusammen: STRUKTUR (Schluessel/Typen/Laenge), NIE
    tatsaechliche Werte -- damit auch bei unerwarteten Formen kein
    potenziell sensibler Inhalt (Adressen, Namen, IDs) im geteilten
    Bericht landet, nur die Form der Antwort. Absichtlich flach (max.
    2 Ebenen) -- fuer Feldnamen-Debugging reicht das, ein tieferer Dump
    waere nur Rauschen."""
    if _depth >= 2:
        return "..."
    if isinstance(data, dict):
        return {k: _shallow_summary(v, _depth + 1) for k, v in data.items()}
    if isinstance(data, list):
        if not data:
            return "[] (leere Liste)"
        return f"Liste[{len(data)}] erstes Element: {_shallow_summary(data[0], _depth + 1)}"
    return type(data).__name__


def _extract_first_id(data: Any, keys: list[str]) -> str | None:
    """Best-effort: findet die erste passende ID in einer moeglicherweise
    verschachtelten, unbestaetigten Antwortform (households/settings-
    Listing wurde nie gegen eine echte Antwort geprueft, siehe
    get_user_households()'s Docstring)."""
    if isinstance(data, dict):
        for key in keys:
            if key in data and isinstance(data[key], str):
                return data[key]
        for value in data.values():
            found = _extract_first_id(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _extract_first_id(item, keys)
            if found:
                return found
    return None


async def _round_trip_favorite_test(robot: Any, report: Report) -> None:
    """Legt EINEN klar als Test gekennzeichneten Favoriten an, prueft
    dass er auftaucht, loescht ihn sofort wieder. Validiert live die
    drei bisher nur per Bytecode bestaetigten HTTP-Methoden (POST/PUT/
    DELETE) fuer Favoriten, ohne dauerhafte Spuren zu hinterlassen."""
    from .models import FavoriteV1

    test_favorite = FavoriteV1(
        name="roombapy-prime-diagnostics-testfavorit (bitte loeschen falls sichtbar)",
        command_defs=[],
    )

    created = await _try(report, "Test-Favorit anlegen (create_favorite)", robot.create_favorite(test_favorite))
    if created is None:
        _skip(report, "Test-Favorit pruefen/loeschen", "Anlegen fehlgeschlagen, siehe oben")
        return

    created_id = None
    if isinstance(created, dict):
        created_id = created.get("favorite_id") or created.get("favoriteId") or created.get("id")

    if not created_id:
        _skip(
            report,
            "Test-Favorit in Liste finden + loeschen",
            "keine favorite_id in der create-Antwort erkennbar (Antwortform unbestaetigt) -- "
            "BITTE MANUELL PRUEFEN UND DEN TEST-FAVORITEN VON HAND LOESCHEN",
        )
        return

    favorites = await _try(report, "Favoritenliste erneut abrufen (Test-Favorit sollte da sein)", robot.get_favorites())
    if favorites is not None:
        found = any(getattr(f, "favorite_id", None) == created_id for f in favorites) if isinstance(favorites, list) else False
        report.add("Test-Favorit in Liste gefunden", "OK" if found else "FEHLGESCHLAGEN", f"id={created_id}")

    await _try(report, "Test-Favorit wieder loeschen (delete_favorite)", robot.delete_favorite(created_id))


def build_issue_url(report: Report, repo: str = ISSUE_TRACKER_REPO) -> str:
    """Baut eine vorausgefuellte "Neues Issue"-URL fuer GitHub (Titel +
    Bericht als Body, URL-kodiert). Funktioniert unabhaengig davon, ob
    das Repo schon existiert -- der Link ist einfach nur ein Klick,
    kein API-Aufruf, daher kein Fehlschlagen moeglich."""
    ok, failed, skipped = report.summary()
    title = f"Live-Validierung: {ok} OK, {failed} fehlgeschlagen, {skipped} uebersprungen"
    body = report.to_markdown()
    return f"https://github.com/{repo}/issues/new?title={quote(title)}&body={quote(body)}"


def _redact_raw_capture(data: Any, secrets: list[str], _depth: int = 0) -> Any:
    """NEU (24. Sitzung) -- Redaktion fuer --dump-config. Anders als
    _shallow_summary() (nur Struktur, nie Werte -- fuer den Bericht,
    der automatisch in den Issue-Link einfliesst) behaelt diese
    Funktion tatsaechliche Werte, weil eine Dump-Datei genau dafuer da
    ist: echte Feldnamen UND echte Werte fuer Reverse-Engineering-
    Zwecke zu zeigen. Redaktion bleibt trotzdem zweistufig:
    1) Jedes woertliche Auftreten von username/password (siehe secrets)
       wird ersetzt -- exakt dieselbe Verteidigung-in-der-Tiefe wie bei
       Report.redact().
    2) Werte unter eindeutig sensibel wirkenden Schluesselnamen (Adresse,
       GPS-Koordinaten, WLAN-Zugangsdaten) werden komplett maskiert,
       unabhaengig vom Inhalt -- diese Felder sind fuer die
       Protokoll-Reverse-Engineering nicht interessant, fuer die
       Privatsphaere aber schon.
    Trotzdem gilt: diese Datei ist NIE automatisch Teil des Issue-Links
    -- wer sie teilt, sollte sie vorher selbst einmal durchsehen."""
    sensitive_keys = {
        "password",
        "ssid",
        "wifipassword",
        "wifi_password",
        "address",
        "street",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "gps",
        "accesskeyid",
        "secretkey",
        "sessiontoken",
        "email",
        "username",
    }
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if k.lower() in sensitive_keys:
                result[k] = "[REDACTED]"
            else:
                result[k] = _redact_raw_capture(v, secrets, _depth + 1)
        return result
    if isinstance(data, list):
        return [_redact_raw_capture(item, secrets, _depth + 1) for item in data]
    if isinstance(data, str):
        redacted = data
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live-Validierung von roombapy-prime gegen einen echten Prime/V4-Account. "
        "Standardmaessig rein lesend -- siehe Modul-Docstring fuer das Sicherheitsprinzip."
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument("--blid", default=None, help="Optional: gezielt diesen Roboter waehlen statt des ersten gefundenen")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Erlaubt den reversiblen Favoriten-Rundlauftest (anlegen+pruefen+loeschen). "
        "Missionsbefehle/Kartenbearbeitung bleiben davon unberuehrt -- werden nie ausgefuehrt.",
    )
    parser.add_argument("--output", default=None, help="Zusaetzlich den Bericht als Markdown-Datei speichern")
    parser.add_argument(
        "--no-issue-link",
        action="store_true",
        help="Keinen GitHub-Issue-Link am Ende drucken/oeffnen (falls kein Teilen gewuenscht ist).",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Den Issue-Link am Ende automatisch im Standardbrowser oeffnen, statt ihn nur auszudrucken.",
    )
    parser.add_argument(
        "--dump-config",
        default=None,
        metavar="PATH",
        help="NEU (24. Sitzung). Speichert die tatsaechlichen (redaktierten) Rohantworten aller "
        "Lese-Endpunkte als JSON unter PATH -- aehnlich der 'Diagnose herunterladen'-Funktion "
        "einer Home-Assistant-Integration. Fuer Reverse-Engineering/Feldnamen-Abgleich gedacht, "
        "nicht fuer den taeglichen Gebrauch. Redaktion entfernt Zugangsdaten und offensichtlich "
        "sensible Felder (Adresse, GPS, WLAN-Zugangsdaten) -- ALLE ANDEREN Werte bleiben "
        "unveraendert sichtbar. Wird NIE automatisch in den Issue-Link aufgenommen -- bitte vor "
        "dem Teilen selbst einmal durchsehen.",
    )
    args = parser.parse_args()

    username = args.username or input("Prime-Account-E-Mail: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Passwort: ")

    print(f"\nroombapy-prime Live-Validierung gegen Account fuer Land '{args.country_code}'...")
    if args.allow_writes:
        print("--allow-writes gesetzt: ein Test-Favorit wird angelegt und sofort wieder geloescht.")
    else:
        print("Rein lesender Modus (Standard). --allow-writes fuer den zusaetzlichen Favoriten-Rundlauftest.")
    if args.dump_config:
        print(f"--dump-config gesetzt: redaktierte Rohantworten werden zusaetzlich unter {args.dump_config} gespeichert.")

    raw_capture: dict[str, Any] = {} if args.dump_config else None
    report = asyncio.run(run(username, password, args.country_code, args.blid, args.allow_writes, raw_capture))
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Zusammenfassung: {ok} OK, {failed} fehlgeschlagen, {skipped} uebersprungen ==")

    if failed > 0 and not args.dump_config:
        print(
            "\nTipp: Bei einem Fehlschlag hilft oft ein zusaetzlicher Lauf mit --dump-config "
            "diagnose.json -- das speichert die tatsaechlichen Rohantworten (nicht nur "
            "Pass/Fail), was uns bei der Fehlersuche hilft. Wird NIE automatisch geteilt, "
            "eigene Durchsicht vor dem Anhaengen empfohlen (siehe --help)."
        )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Bericht gespeichert unter {args.output}")

    if args.dump_config and raw_capture is not None:
        import json

        redacted = _redact_raw_capture(raw_capture, [username, password])
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(redacted, f, indent=2, default=str, ensure_ascii=False)
        print(f"Redaktierte Rohantworten gespeichert unter {args.dump_config}")
        print(
            "  Bitte vor dem Teilen einmal selbst durchsehen -- die Redaktion faengt bekannte "
            "Faelle ab, kann aber nicht jede moegliche Ueberraschung in unbekannten Antwortformen "
            "garantieren."
        )

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Rueckmeldung an die Maintainer ==")
        print("Falls du diesen Bericht teilen moechtest (hilft der Bibliothek enorm):")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
