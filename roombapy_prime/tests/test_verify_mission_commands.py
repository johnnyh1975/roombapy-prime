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
from roombapy_prime.verify_mission_commands import (
    _capture_mid_mission_state,
    _confirm,
    _diff_reported_keys,
    _run_command,
    _show_state,
)


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
    """UPDATED (session 40) -- return shape now wraps the raw reported
    dict together with a RobotStatusV2 parse attempt (None here, since
    this dict has none of the confirmed wire keys)."""
    robot = AsyncMock()
    robot.get_state.return_value = ShadowResponse(topic="t", payload={"state": {"reported": {"foo": "bar"}}})

    result = await _show_state(robot, "test")

    assert result == {"reported": {"foo": "bar"}, "robot_status_v2": None}


@pytest.mark.asyncio
async def test_show_state_reports_robot_status_v2_when_present() -> None:
    """NEW (session 40) -- if the reported dict happens to contain any
    of RobotStatusV2's confirmed wire keys, the parse attempt is
    included (as a plain dict, JSON-safe for the diagnostic capture)."""
    robot = AsyncMock()
    robot.get_state.return_value = ShadowResponse(
        topic="t",
        payload={"state": {"reported": {"robot_state": 2, "is_charging": True}}},
    )

    result = await _show_state(robot, "test")

    assert result["reported"] == {"robot_state": 2, "is_charging": True}
    assert result["robot_status_v2"]["robot_state"] == 2
    assert result["robot_status_v2"]["is_charging"] is True


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


# ── NEW (session 57): mid-mission capture / diff ────────────────────────────


def test_diff_reported_keys_identifies_new_keys(capsys) -> None:
    """Der zentrale Zweck der ganzen Aenderung: ein Key, der vorher
    nicht da war, muss klar als NEU gemeldet werden."""
    baseline = {"reported": {"sku": "G185020"}}
    current = {"reported": {"sku": "G185020", "robot_state": 2}}

    _diff_reported_keys(baseline, current)

    out = capsys.readouterr().out
    assert "robot_state" in out
    assert "NEW top-level keys" in out


def test_diff_reported_keys_identifies_changed_values(capsys) -> None:
    """Ein Key, der in beiden Snapshots existiert, aber einen anderen
    Wert hat, muss als GEAENDERT gemeldet werden, nicht ignoriert."""
    baseline = {"reported": {"schedHold": True}}
    current = {"reported": {"schedHold": False}}

    _diff_reported_keys(baseline, current)

    out = capsys.readouterr().out
    assert "schedHold" in out
    assert "VALUE changed" in out


def test_diff_reported_keys_no_change_reported_clearly(capsys) -> None:
    """Identische Snapshots (bisheriger Stand bei beiden echten
    Accounts) muessen explizit als 'keine Aenderung' gemeldet werden,
    nicht stillschweigend nichts ausgeben."""
    snapshot = {"reported": {"sku": "G185020", "cap": {}}}

    _diff_reported_keys(snapshot, snapshot)

    out = capsys.readouterr().out
    assert "No new top-level keys appeared" in out
    assert "No existing key's value changed" in out


def test_diff_reported_keys_handles_none_snapshots() -> None:
    """Darf nicht abstuerzen, wenn eine vorherige get_state()-Erfassung
    fehlgeschlagen ist (_show_state() gibt dann None zurueck)."""
    _diff_reported_keys(None, None)
    _diff_reported_keys(None, {"reported": {"sku": "G185020"}})
    _diff_reported_keys({"reported": {"sku": "G185020"}}, None)


@pytest.mark.asyncio
async def test_capture_mid_mission_state_skips_when_user_declines(monkeypatch) -> None:
    """Wenn der Nutzer nicht bestaetigt, dass der Roboter sichtbar
    reinigt, darf KEIN zusaetzlicher get_state()-Aufruf erfolgen --
    kein stiller Fallback auf eine feste Wartezeit."""
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    robot = AsyncMock()
    report = Report()
    raw_capture: dict = {}
    baseline = {"reported": {"sku": "G185020"}}

    await _capture_mid_mission_state(robot, report, raw_capture, baseline)

    robot.get_state.assert_not_called()
    assert report.results[0].status == "SKIPPED"
    assert "Mid-mission (actively cleaning)" not in raw_capture


@pytest.mark.asyncio
async def test_capture_mid_mission_state_captures_and_diffs_when_confirmed(monkeypatch) -> None:
    """Nach Bestaetigung: get_state() wird aufgerufen, das Ergebnis
    landet im raw_capture-Dict, und der Diff gegen die Baseline laeuft
    (neuer Key sichtbar in der Ausgabe)."""
    monkeypatch.setattr("builtins.input", lambda prompt: "j")
    monkeypatch.setattr("roombapy_prime.verify_mission_commands.asyncio.sleep", AsyncMock())
    robot = AsyncMock()
    robot.get_state.return_value = ShadowResponse(
        topic="t",
        payload={"state": {"reported": {"sku": "G185020", "robot_state": 2}}},
    )
    report = Report()
    raw_capture: dict = {}
    baseline = {"reported": {"sku": "G185020"}}

    await _capture_mid_mission_state(robot, report, raw_capture, baseline)

    robot.get_state.assert_awaited_once()
    assert report.results[0].status == "OK"
    assert raw_capture["Mid-mission (actively cleaning)"]["reported"]["robot_state"] == 2
