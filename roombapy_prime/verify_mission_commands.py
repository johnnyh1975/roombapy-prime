"""Manuelle, beobachtete Verifikation von Missionsbefehlen (Start/Stop/
Pause/...) gegen einen echten Prime/V4-Roboter.

BEWUSST GETRENNT von diagnostics.py, aus demselben Grund, aus dem
diagnostics.py selbst Missionsbefehle NIE automatisch ausfuehrt: dieses
Skript bewegt den echten Roboter. Es existiert nur, weil es einen
Weg braucht, das irgendwann EINMAL bewusst, beobachtet zu tun -- nicht,
um das automatisierte Diagnoseskript "sicherer" zu machen.

SICHERHEITSDESIGN (zweifach abgesichert, beide Stufen sind Pflicht):
1. --i-understand-this-will-move-my-robot muss beim Start explizit
   gesetzt werden, sonst bricht das Skript sofort ab, bevor es sich
   ueberhaupt einloggt.
2. Vor JEDEM einzelnen Befehl wird interaktiv gefragt (nicht nur einmal
   am Anfang) -- inklusive Anzeige, was genau gesendet wird. Enter/"j"
   bestaetigt, alles andere bricht ab.

ABLAUF (bewusst konservativ, kein vollstaendiger Reinigungszyklus):
  START (clean_all=True) -> kurze Pause, waehrend der Roboter reagieren
  sollte -> STOP. Optional, einzeln abgefragt: PAUSE/RESUME, DOCK.

Vor UND nach jedem gesendeten Befehl wird zusaetzlich get_state()
abgerufen und der rohe reported-Zustand angezeigt -- ein aktiver
Missionszustand wurde bisher noch nie eingefangen (alle bisherigen
echten Antworten zeigten einen geladenen, aber nicht laufenden
Roboter). Das ist selbst neue, bisher unbestaetigte Information.

Ergebnis wird wie bei diagnostics.py als Markdown-Bericht
zusammengefasst, inklusive vorausgefuelltem GitHub-Issue-Link (gleiche
Redaktionslogik, gleiche Warnung: Zugangsdaten werden entfernt, sonst
nichts automatisch geteilt).

VERWENDUNG:
  roombapy-prime-verify-commands \\
      --username you@example.com --country-code US --blid BLID123 \\
      --i-understand-this-will-move-my-robot

Zugangsdaten wie bei diagnostics.py: ROOMBAPY_PRIME_PASSWORD env var
oder interaktive Abfrage, nie als Kommandozeilenargument.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import webbrowser
from typing import Any

import aiohttp

from .diagnostics import Report, _redact_raw_capture, build_issue_url
from .models import MissionCommandType, RoutineCommand
from .prime_factory import PrimeFactory


def _confirm(prompt: str) -> bool:
    """Interaktive Bestaetigung -- NUR "j"/"ja"/"y"/"yes" (Gross-/
    Kleinschreibung egal) gilt als Zustimmung, alles andere (inklusive
    einfach Enter) bricht ab. Bewusst restriktiv -- ein versehentliches
    Enter darf niemals als Zustimmung gelten."""
    answer = input(f"{prompt} [j/N] ").strip().lower()
    return answer in ("j", "ja", "y", "yes")


async def _show_state(robot: Any, label: str) -> dict[str, Any] | None:
    """Ruft get_state() ab und zeigt den rohen reported-Zustand an --
    bisher wurde nie ein Zustand waehrend einer AKTIVEN Mission
    eingefangen, das ist also selbst neue Information, unabhaengig vom
    Testergebnis."""
    try:
        state = await robot.get_state()
        reported = state.payload.get("state", {}).get("reported", {}) if isinstance(state.payload, dict) else {}
        print(f"\n  [{label}] get_state().reported = {reported}")
        return reported
    except Exception as exc:  # noqa: BLE001
        print(f"\n  [{label}] get_state() fehlgeschlagen: {type(exc).__name__}: {exc}")
        return None


async def _run_command(
    robot: Any,
    report: Report,
    raw_capture: dict[str, Any],
    command_type: MissionCommandType,
    label: str,
    clean_all: bool = False,
) -> bool:
    """Fragt vor dem Senden explizit nach, zeigt den Zustand davor/danach,
    und fragt danach, was der Nutzer am echten Roboter beobachtet hat.
    Gibt True zurueck, wenn der Nutzer bestaetigt hat, dass es wie
    erwartet funktioniert hat."""
    cmd = RoutineCommand(command_type=command_type, asset_id=robot.blid, clean_all=clean_all)
    print(f"\n{'=' * 60}")
    print(f"NAECHSTER BEFEHL: {label} ({command_type.value})")
    print(f"Wird gesendet: {cmd.to_json()}")
    if not _confirm(f"Jetzt \"{label}\" an den echten Roboter senden?"):
        report.add(label, "UEBERSPRUNGEN", "vom Nutzer nicht bestaetigt")
        return False

    before = await _show_state(robot, f"{label}: vorher")
    try:
        await robot.send_mission_command(cmd)
        print("  Befehl gesendet, keine Fehlermeldung vom Server.")
    except Exception as exc:  # noqa: BLE001
        report.add(label, "FEHLGESCHLAGEN", f"{type(exc).__name__}: {exc}")
        return False

    await asyncio.sleep(3.0)
    after = await _show_state(robot, f"{label}: danach")
    if raw_capture is not None:
        raw_capture[f"{label} (vorher)"] = before
        raw_capture[f"{label} (nachher)"] = after

    observed = _confirm(f"Hat der Roboter tatsaechlich wie erwartet auf \"{label}\" reagiert?")
    if observed:
        report.add(label, "OK", "vom Nutzer am echten Roboter bestaetigt")
    else:
        report.add(label, "FEHLGESCHLAGEN", "Server nahm den Befehl an, aber Roboter hat NICHT wie erwartet reagiert")
    return observed


async def run(username: str, password: str, country_code: str, blid: str) -> tuple[Report, dict[str, Any]]:
    report = Report()
    raw_capture: dict[str, Any] = {}

    async with aiohttp.ClientSession() as session:
        print("\n== Anmeldung ==")
        robot = await PrimeFactory.create_prime_robot(session, username, password, country_code, blid)
        report.add("Login", "OK", f"BLID={robot.blid}")
        await robot.connect()
        report.add("MQTT-Verbindung", "OK")

        print("\n== Kern-Test: Start -> Stop ==")
        started = await _run_command(robot, report, raw_capture, MissionCommandType.START, "Start (clean_all)", clean_all=True)

        if started:
            await _run_command(robot, report, raw_capture, MissionCommandType.STOP, "Stop")
        else:
            report.add("Stop", "UEBERSPRUNGEN", "Start wurde nicht bestaetigt, Stop ergibt keinen Sinn ohne laufende Mission")

        print("\n== Optionale Zusatztests ==")
        if _confirm("Zusaetzlich Pause/Resume testen? (braucht eine erneut gestartete Mission)"):
            if await _run_command(robot, report, raw_capture, MissionCommandType.START, "Start (fuer Pause-Test)", clean_all=True):
                await _run_command(robot, report, raw_capture, MissionCommandType.PAUSE, "Pause")
                await _run_command(robot, report, raw_capture, MissionCommandType.RESUME, "Resume")
                await _run_command(robot, report, raw_capture, MissionCommandType.STOP, "Stop (nach Pause-Test)")
        else:
            report.add("Pause/Resume", "UEBERSPRUNGEN", "vom Nutzer nicht gewaehlt")

        if _confirm("Zusaetzlich Dock testen? (schickt den Roboter zur Ladestation)"):
            await _run_command(robot, report, raw_capture, MissionCommandType.DOCK, "Dock")
        else:
            report.add("Dock", "UEBERSPRUNGEN", "vom Nutzer nicht gewaehlt")

        await robot.disconnect()

    return report, raw_capture


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Manuelle, beobachtete Verifikation von Missionsbefehlen (Start/Stop/Pause/Dock) "
            "gegen einen ECHTEN Prime/V4-Roboter. Bewegt den Roboter tatsaechlich -- siehe "
            "Modul-Docstring fuer das Sicherheitsdesign."
        )
    )
    parser.add_argument("--username", default=os.environ.get("ROOMBAPY_PRIME_USERNAME"))
    parser.add_argument("--country-code", default=os.environ.get("ROOMBAPY_PRIME_COUNTRY", "US"))
    parser.add_argument(
        "--blid",
        required=True,
        help="Pflicht (anders als bei diagnostics.py) -- das genaue Zielgeraet muss bewusst "
        "gewaehlt werden, kein 'erstes gefundenes Geraet'.",
    )
    parser.add_argument(
        "--i-understand-this-will-move-my-robot",
        action="store_true",
        dest="confirmed",
        help="Pflicht. Ohne dieses Flag bricht das Skript sofort ab, vor jedem Login.",
    )
    parser.add_argument("--output", default=None, metavar="PATH")
    parser.add_argument("--dump-config", default=None, metavar="PATH")
    parser.add_argument("--no-issue-link", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    if not args.confirmed:
        print(
            "Abgebrochen: --i-understand-this-will-move-my-robot fehlt. Dieses Skript sendet "
            "ECHTE Missionsbefehle an ein ECHTES Geraet -- siehe Modul-Docstring."
        )
        sys.exit(1)

    username = args.username or input("Prime-Account-E-Mail: ")
    password = os.environ.get("ROOMBAPY_PRIME_PASSWORD") or getpass.getpass("Passwort: ")

    print(f"\nZIEL-GERAET: {args.blid}")
    print("Dieses Skript sendet gleich echte Start-/Stop-Befehle an dieses Geraet.")
    if not _confirm("Fortfahren?"):
        print("Abgebrochen.")
        sys.exit(0)

    report, raw_capture = asyncio.run(run(username, password, args.country_code, args.blid))
    report.redact(username, password)

    ok, failed, skipped = report.summary()
    print(f"\n== Zusammenfassung: {ok} OK, {failed} fehlgeschlagen, {skipped} uebersprungen ==")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        print(f"Bericht gespeichert unter {args.output}")

    if args.dump_config:
        import json

        redacted = _redact_raw_capture(raw_capture, [username, password])
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(redacted, f, indent=2, default=str, ensure_ascii=False)
        print(f"Redaktierte Rohantworten (inkl. get_state() waehrend der Missionen) gespeichert unter {args.dump_config}")

    if not args.no_issue_link:
        issue_url = build_issue_url(report)
        print("\n== Rueckmeldung an die Maintainer ==")
        print("Falls du diesen Bericht teilen moechtest:")
        print(f"  {issue_url}")
        if args.open_browser:
            webbrowser.open(issue_url)


if __name__ == "__main__":
    main()
