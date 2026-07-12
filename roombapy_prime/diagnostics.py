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

Zugangsdaten koennen alternativ ueber Umgebungsvariablen
ROOMBAPY_PRIME_USERNAME / ROOMBAPY_PRIME_PASSWORD / ROOMBAPY_PRIME_COUNTRY
gesetzt werden (nuetzlich fuer CI/wiederholte Laeufe) -- werden aber nie
geloggt oder in den Bericht aufgenommen.

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


async def _try(report: Report, name: str, coro: Any) -> Any:
    """Fuehrt eine einzelne Pruefung aus, faengt JEDE Exception (nicht
    nur RestError) -- ein Diagnoseskript darf nie selbst abstuerzen,
    egal was der Server zurueckgibt."""
    try:
        result = await coro
        report.add(name, "OK")
        return result
    except Exception as exc:  # noqa: BLE001 -- bewusst breit, siehe Docstring
        report.add(name, "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")
        return None


def _skip(report: Report, name: str, reason: str) -> None:
    report.add(name, "UEBERSPRUNGEN", reason)


async def run(
    username: str,
    password: str,
    country_code: str,
    blid: str | None,
    allow_writes: bool,
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

        await _try(report, "Shadow-Zustand abrufen (get_state)", robot.get_state())
        await _try(report, "Shadow-Einstellungen abrufen (get_settings)", robot.get_settings())

        print("\n== REST-Lesezugriffe (Favoriten/Missionshistorie/Zeitplaene/...) ==")
        await _try(report, "Favoriten abrufen (get_favorites)", robot.get_favorites())
        await _try(
            report,
            "Missionshistorie abrufen (get_mission_history)",
            robot.get_mission_history(robot.blid, max_reports=5),
        )
        await _try(report, "Haushaltsliste abrufen (get_user_households)", robot.get_user_households())

        map_versions = await _try(
            report, "Aktive Kartenversionen abrufen (get_active_map_versions)", robot.get_active_map_versions()
        )

        p2map_id: str | None = None
        if map_versions:
            try:
                p2map_id = map_versions[0].get("p2mapId") or map_versions[0].get("id")
            except (AttributeError, IndexError, TypeError):
                p2map_id = None

        if p2map_id:
            await _try(report, "Kartenmetadaten abrufen (get_map_metadata)", robot.get_map_metadata(p2map_id))
            geojson_link = await _try(
                report,
                "Vorsignierte Kartenbuendel-URL abrufen (get_map_geojson_link)",
                robot.get_map_geojson_link(p2map_id, "1"),
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
                        except Exception as exc:  # noqa: BLE001
                            report.add("Kartenbuendel entpacken", "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")
                else:
                    _skip(
                        report,
                        "Kartenbuendel herunterladen",
                        "kein erkennbarer URL-Schluessel in der Antwort (Antwortform unbestaetigt)",
                    )
            households = await _try_silent(robot.get_user_households())
            household_id = _extract_first_id(households, ["householdId", "id"])
            if household_id:
                await _try(report, "Zeitplaene abrufen (get_schedules)", robot.get_schedules(household_id))
                await _try(report, "DND-Einstellungen abrufen (get_dnd_settings)", robot.get_dnd_settings(household_id))
            else:
                _skip(report, "Zeitplaene/DND abrufen", "keine household_id ermittelbar (siehe Haushaltsliste oben)")

            await _try(
                report,
                "Reinigungsprofile abrufen (get_cleaning_profiles)",
                robot.get_cleaning_profiles(robot.blid, p2map_id),
            )
            await _try(
                report, "Standard-Routinen abrufen (get_default_routines)", robot.get_default_routines(p2map_id)
            )
        else:
            _skip(
                report,
                "Kartenmetadaten/Kartenbuendel/Reinigungsprofile/Standard-Routinen",
                "keine aktive Kartenversion gefunden (Roboter hat evtl. noch keine Karte gelernt)",
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


async def _try_silent(coro: Any) -> Any:
    """Wie _try(), aber ohne Bericht-Eintrag -- fuer Zwischenschritte,
    die selbst kein eigener Pruefpunkt sind (z.B. household_id nur
    ermitteln, um EINE andere Pruefung ueberhaupt versuchen zu koennen)."""
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return None


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
    args = parser.parse_args()

    username = args.username or input("Prime-Account-E-Mail: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Passwort: ")

    print(f"\nroombapy-prime Live-Validierung gegen Account fuer Land '{args.country_code}'...")
    if args.allow_writes:
        print("--allow-writes gesetzt: ein Test-Favorit wird angelegt und sofort wieder geloescht.")
    else:
        print("Rein lesender Modus (Standard). --allow-writes fuer den zusaetzlichen Favoriten-Rundlauftest.")

    report = asyncio.run(run(username, password, args.country_code, args.blid, args.allow_writes))
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Zusammenfassung: {ok} OK, {failed} fehlgeschlagen, {skipped} uebersprungen ==")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Bericht gespeichert unter {args.output}")

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
