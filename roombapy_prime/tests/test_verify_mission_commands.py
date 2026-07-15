"""Tests for the testable parts of verify_mission_commands.py --
_confirm()'s and _run_command()'s logic, completely mocked (real robot,
real network). The actual purpose of the script (sending real mission
commands to a real device) is by nature not automatable to test --
that's the whole point of the script, see the module docstring."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from roombapy_prime.diagnostics import Report
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
    gesendet -- send_simple_command darf nicht aufgerufen werden."""
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    robot = AsyncMock()
    robot.blid = "BLID123"
    report = Report()

    result = await _run_command(robot, report, {}, "start", "Start")

    robot.send_simple_command.assert_not_called()
    assert result is False
    assert report.results[0].status == "SKIPPED"


@pytest.mark.asyncio
async def test_run_command_sends_and_records_success(monkeypatch) -> None:
    """Full success path: user confirms sending AND afterward confirms
    that the robot actually reacted."""
    answers = iter(["j", "j"])  # 1) confirm sending, 2) confirm observation
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("roombapy_prime.verify_mission_commands.asyncio.sleep", AsyncMock())

    robot = AsyncMock()
    robot.blid = "BLID123"
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {}}})
    report = Report()
    raw_capture: dict = {}

    result = await _run_command(robot, report, raw_capture, "start", "Start")

    robot.send_simple_command.assert_awaited_once_with("start")
    assert result is True
    assert report.results[0].status == "OK"
    assert "Start (before)" in raw_capture
    assert "Start (after)" in raw_capture


@pytest.mark.asyncio
async def test_run_command_records_failure_when_robot_did_not_react(monkeypatch) -> None:
    """Server accepts the command (no error), but the user does NOT
    confirm that the robot reacted -- must be reported as FAILED, not
    OK."""
    answers = iter(["j", "n"])  # 1) confirm sending, 2) DECLINE observation
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("roombapy_prime.verify_mission_commands.asyncio.sleep", AsyncMock())

    robot = AsyncMock()
    robot.blid = "BLID123"
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {}}})
    report = Report()

    result = await _run_command(robot, report, {}, "start", "Start")

    assert result is False
    assert report.results[0].status == "FAILED"


@pytest.mark.asyncio
async def test_run_command_records_server_error(monkeypatch) -> None:
    """Server lehnt den Befehl selbst ab (Exception) -- muss als
    FAILED gemeldet werden, keine Beobachtungsfrage noetig."""
    monkeypatch.setattr("builtins.input", lambda prompt: "j")
    robot = AsyncMock()
    robot.blid = "BLID123"
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {}}})
    robot.send_simple_command.side_effect = RuntimeError("server said no")
    report = Report()

    result = await _run_command(robot, report, {}, "start", "Start")

    assert result is False
    assert report.results[-1].status == "FAILED"
    assert "server said no" in report.results[-1].detail
