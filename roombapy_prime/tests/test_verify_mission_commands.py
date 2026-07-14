"""Tests fuer die testbaren Teile von verify_mission_commands.py --
_confirm() und _run_command()'s Logik, komplett gemockt (echter Roboter,
echtes Netzwerk). Der eigentliche Zweck des Skripts (echte Missionsbefehle
an ein echtes Geraet senden) ist per Natur nicht automatisiert testbar --
das ist der ganze Punkt des Skripts, siehe Modul-Docstring."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from roombapy_prime.diagnostics import Report
from roombapy_prime.models import MissionCommandType
from roombapy_prime.mqtt_client import ShadowResponse
from roombapy_prime.verify_mission_commands import _confirm, _run_command, _show_state


@pytest.mark.parametrize("answer,expected", [("j", True), ("ja", True), ("y", True), ("yes", True),
                                              ("Y", True), ("JA", True), ("n", False), ("nein", False),
                                              ("", False), ("x", False)])
def test_confirm_only_accepts_explicit_affirmatives(answer, expected, monkeypatch) -> None:
    """Sicherheitsrelevant: NUR eindeutige Zustimmung darf True ergeben --
    ein versehentliches Enter (leerer String) muss als Ablehnung zaehlen."""
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert _confirm("Test?") is expected


@pytest.mark.asyncio
async def test_show_state_extracts_reported_dict() -> None:
    robot = AsyncMock()
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {"foo": "bar"}}})

    result = await _show_state(robot, "test")

    assert result == {"foo": "bar"}


@pytest.mark.asyncio
async def test_show_state_handles_failure_gracefully() -> None:
    """Darf nicht abstuerzen, wenn get_state() waehrend einer laufenden
    Mission fehlschlaegt -- soll nur None zurueckgeben."""
    robot = AsyncMock()
    robot.get_state.side_effect = RuntimeError("boom")

    result = await _show_state(robot, "test")

    assert result is None


@pytest.mark.asyncio
async def test_run_command_skips_when_user_declines(monkeypatch) -> None:
    """Wenn der Nutzer die Sende-Bestaetigung ablehnt, wird NICHTS
    gesendet -- send_mission_command darf nicht aufgerufen werden."""
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    robot = AsyncMock()
    robot.blid = "BLID123"
    report = Report()

    result = await _run_command(robot, report, {}, MissionCommandType.START, "Start")

    robot.send_mission_command.assert_not_called()
    assert result is False
    assert report.results[0].status == "UEBERSPRUNGEN"


@pytest.mark.asyncio
async def test_run_command_sends_and_records_success(monkeypatch) -> None:
    """Voller Erfolgspfad: Nutzer bestaetigt Senden UND bestaetigt danach,
    dass der Roboter tatsaechlich reagiert hat."""
    answers = iter(["j", "j"])  # 1) Senden bestaetigen, 2) Beobachtung bestaetigen
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("roombapy_prime.verify_mission_commands.asyncio.sleep", AsyncMock())

    robot = AsyncMock()
    robot.blid = "BLID123"
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {}}})
    report = Report()
    raw_capture: dict = {}

    result = await _run_command(robot, report, raw_capture, MissionCommandType.START, "Start", clean_all=True)

    robot.send_mission_command.assert_awaited_once()
    sent_command = robot.send_mission_command.call_args[0][0]
    assert sent_command.command_type == MissionCommandType.START
    assert sent_command.clean_all is True
    assert result is True
    assert report.results[0].status == "OK"
    assert "Start (vorher)" in raw_capture
    assert "Start (nachher)" in raw_capture


@pytest.mark.asyncio
async def test_run_command_records_failure_when_robot_did_not_react(monkeypatch) -> None:
    """Server nimmt den Befehl an (kein Fehler), aber der Nutzer
    bestaetigt NICHT, dass der Roboter reagiert hat -- muss als
    FEHLGESCHLAGEN gemeldet werden, nicht als OK."""
    answers = iter(["j", "n"])  # 1) Senden bestaetigen, 2) Beobachtung ABLEHNEN
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("roombapy_prime.verify_mission_commands.asyncio.sleep", AsyncMock())

    robot = AsyncMock()
    robot.blid = "BLID123"
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {}}})
    report = Report()

    result = await _run_command(robot, report, {}, MissionCommandType.START, "Start")

    assert result is False
    assert report.results[0].status == "FEHLGESCHLAGEN"


@pytest.mark.asyncio
async def test_run_command_records_server_error(monkeypatch) -> None:
    """Server lehnt den Befehl selbst ab (Exception) -- muss als
    FEHLGESCHLAGEN gemeldet werden, keine Beobachtungsfrage noetig."""
    monkeypatch.setattr("builtins.input", lambda prompt: "j")
    robot = AsyncMock()
    robot.blid = "BLID123"
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {}}})
    robot.send_mission_command.side_effect = RuntimeError("server said no")
    report = Report()

    result = await _run_command(robot, report, {}, MissionCommandType.START, "Start")

    assert result is False
    assert report.results[-1].status == "FEHLGESCHLAGEN"
    assert "server said no" in report.results[-1].detail
