"""Tests for verify_mission_timeline.py -- the new diagnostic script for
capturing whatever arrives on the mission-timeline topic(s) during a
real, actively-running mission (see the module's own docstring for the
full context: prompted by a live idle-vs-mid-mission diff proving
mission status does NOT flow through get_state()/watch_state())."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from roombapy_prime.diagnostics import Report
from roombapy_prime.verify_mission_timeline import _confirm, _watch_one


@pytest.mark.parametrize(
    "answer,expected",
    [("y", True), ("Y", True), ("yes", True), ("ja", True), ("j", True),
     ("n", False), ("", False), ("no", False), ("anything else", False)],
)
def test_confirm_only_accepts_explicit_affirmatives(answer, expected, monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert _confirm("Proceed?") is expected


async def _fake_agen(payloads: list[dict]):
    for p in payloads:
        response = MagicMock()
        response.payload = p
        yield response


class _FakeAsyncGenWrapper:
    """Wraps an async generator function so .aclose() can be asserted
    on -- MagicMock's auto-generated attributes don't track async
    generator protocol calls the same way a real one does."""

    def __init__(self, payloads: list[dict]) -> None:
        self._gen = _fake_agen(payloads)
        self.aclose = AsyncMock(side_effect=self._gen.aclose)

    def __aiter__(self):
        return self._gen.__aiter__()


@pytest.mark.asyncio
async def test_watch_one_captures_and_reports_messages() -> None:
    report = Report()
    raw_capture: dict = {}
    wrapper = _FakeAsyncGenWrapper([{"phase": "run"}, {"phase": "charge"}])

    await _watch_one(lambda: wrapper, "mission/timeline/report", raw_capture, report)

    assert raw_capture["watch: mission/timeline/report"] == [{"phase": "run"}, {"phase": "charge"}]
    wrapper.aclose.assert_awaited_once()
    entry = next(e for e in report.results if e.name == "Watch mission/timeline/report")
    assert entry.status == "OK"
    assert "2 message(s)" in entry.detail


@pytest.mark.asyncio
async def test_watch_one_reports_clearly_when_nothing_arrives() -> None:
    """A null result is itself meaningful (see the module docstring) --
    must still report OK with a clear explanation, not look like a
    failure or go unreported."""
    report = Report()
    raw_capture: dict = {}
    wrapper = _FakeAsyncGenWrapper([])

    await _watch_one(lambda: wrapper, "mission/timeline/report", raw_capture, report)

    assert raw_capture["watch: mission/timeline/report"] == []
    entry = next(e for e in report.results if e.name == "Watch mission/timeline/report")
    assert entry.status == "OK"
    assert "no messages arrived" in entry.detail


@pytest.mark.asyncio
async def test_watch_one_closes_generator_on_cancellation() -> None:
    """The normal way a watch ends (the surrounding asyncio.wait_for()
    timeout in run() cancels the task) must still call .aclose() on
    the generator, and must not propagate CancelledError back out --
    it's expected, not a failure."""
    report = Report()
    raw_capture: dict = {}

    async def _never_ending():
        response = MagicMock()
        response.payload = {"phase": "run"}
        yield response
        # Then just hangs -- simulates a live, ongoing subscription.
        await asyncio.Event().wait()

    wrapper = _FakeAsyncGenWrapper([])
    wrapper._gen = _never_ending()
    wrapper.aclose = AsyncMock(side_effect=wrapper._gen.aclose)

    task = asyncio.ensure_future(
        _watch_one(lambda: wrapper, "mission/timeline/report", raw_capture, report)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    wrapper.aclose.assert_awaited_once()
