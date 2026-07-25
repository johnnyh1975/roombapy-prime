"""Tests for verify_named_shadows.py.

This script had ZERO tests despite being the single most consequential
one in the project: its --dump-config output is where nearly every
protocol finding this project has came from -- the capability flags,
detectedPad, cleanMissionStatus, the classic/unnamed shadow's very
existence. A silent regression here would not break loudly; it would
quietly produce an incomplete capture that gets analysed as if it were
complete.
"""

from __future__ import annotations

import asyncio
import contextlib

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from roombapy_prime.diagnostics import Report
from roombapy_prime_tools.verify_named_shadows import (
    CANDIDATE_SHADOWS,
    KNOWN_SHADOWS,
    _fetch_and_report,
)


def _response(reported):
    return MagicMock(payload={"state": {"reported": reported}})


class TestShadowLists:
    def test_the_unnamed_classic_shadow_is_included(self):
        """None is not a placeholder -- it selects get_state() instead
        of get_named_shadow(), and that shadow is the ONLY source of
        per-device capability flags found anywhere in this project."""
        assert None in KNOWN_SHADOWS

    def test_all_nine_shadows_are_covered_without_duplicates(self):
        combined = KNOWN_SHADOWS + CANDIDATE_SHADOWS

        assert len(combined) == len(set(combined)) == 9


class TestFetchAndReport:
    @pytest.mark.asyncio
    async def test_named_shadow_uses_get_named_shadow(self):
        robot = AsyncMock()
        robot.get_named_shadow.return_value = _response({"batPct": 100})
        capture: dict = {}

        await _fetch_and_report(robot, "ro-currentstate", Report(), capture)

        robot.get_named_shadow.assert_awaited_once_with("ro-currentstate")
        robot.get_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_selects_get_state_not_get_named_shadow(self):
        """The classic/unnamed shadow is reached through a different
        call entirely -- getting this wrong would silently drop the
        capability flags from every capture."""
        robot = AsyncMock()
        robot.get_state.return_value = _response({"cap": {"scrub": 3}})
        capture: dict = {}

        await _fetch_and_report(robot, None, Report(), capture)

        robot.get_state.assert_awaited_once()
        robot.get_named_shadow.assert_not_awaited()
        assert "shadow: (classic/unnamed)" in capture

    @pytest.mark.asyncio
    async def test_payload_is_captured_verbatim_for_later_analysis(self):
        """The capture is the actual deliverable -- it must hold the
        raw payload, not our summary of it, because the fields that
        turned out to matter were repeatedly ones nobody had thought
        to look at yet."""
        robot = AsyncMock()
        payload = {"state": {"reported": {"cap": {"scrub": 0}, "somethingUnmodelled": 42}}}
        robot.get_named_shadow.return_value = MagicMock(payload=payload)
        capture: dict = {}

        await _fetch_and_report(robot, "ro-currentstate", Report(), capture)

        assert capture["shadow: ro-currentstate"] == payload

    @pytest.mark.asyncio
    async def test_one_failing_shadow_is_recorded_and_does_not_raise(self):
        """A field tester hit throttling where only 3 of 8 shadows came
        through. The run must continue and record which ones failed --
        that pattern is itself a finding."""
        robot = AsyncMock()
        robot.get_named_shadow.side_effect = RuntimeError("throttled")
        report = Report()

        await _fetch_and_report(robot, "ro-stats", report, {})

        entry = report.results[-1]
        assert entry.status == "FAILED"
        assert "throttled" in entry.detail

    @pytest.mark.asyncio
    async def test_empty_reported_block_is_ok_not_failed(self):
        """An empty shadow is a real, meaningful observation about the
        device -- not an error in fetching it."""
        robot = AsyncMock()
        robot.get_named_shadow.return_value = _response({})
        report = Report()

        await _fetch_and_report(robot, "rw-schedule", report, {})

        assert report.results[-1].status == "OK"

    @pytest.mark.asyncio
    async def test_reported_keys_are_listed_in_the_report(self):
        robot = AsyncMock()
        robot.get_named_shadow.return_value = _response({"batPct": 93, "detectedPad": "noPad"})
        report = Report()

        await _fetch_and_report(robot, "ro-currentstate", report, {})

        assert "batPct" in report.results[-1].detail
        assert "detectedPad" in report.results[-1].detail


class TestRunOrchestration:
    """run()'s own loop, previously untested.

    Its output IS the deliverable: the --dump-config capture is where
    nearly every protocol finding in this project came from. A silent
    regression here would not break loudly -- it would produce a capture
    that looks complete and quietly omits a shadow, which then gets
    analysed as evidence of absence.
    """

    @contextlib.asynccontextmanager
    async def _connection(self, robot, report):
        yield robot, report

    def _run(self, robot, delay_seconds=0.0):
        from roombapy_prime.diagnostics import Report
        from roombapy_prime_tools import verify_named_shadows as mod

        report = Report()
        with patch.object(mod, "connected_robot",
                          lambda *a, **k: self._connection(robot, report)):
            return asyncio.run(mod.run("u", "p", "US", "BLID", delay_seconds))

    def _robot(self):
        robot = AsyncMock()
        robot.get_named_shadow.return_value = MagicMock(
            payload={"state": {"reported": {"batPct": 90}}}
        )
        robot.get_state.return_value = MagicMock(
            payload={"state": {"reported": {"cap": {"scrub": 3}}}}
        )
        return robot

    def test_every_known_and_candidate_shadow_is_attempted(self):
        """Nine shadows, nine attempts -- a capture missing one because
        the loop skipped it is indistinguishable from a device that
        does not have it."""
        from roombapy_prime_tools.verify_named_shadows import CANDIDATE_SHADOWS, KNOWN_SHADOWS

        robot = self._robot()

        _report, capture = self._run(robot)

        assert len(capture) == len(KNOWN_SHADOWS) + len(CANDIDATE_SHADOWS)

    def test_the_unnamed_shadow_is_fetched_via_get_state(self):
        robot = self._robot()

        self._run(robot)

        robot.get_state.assert_awaited_once()

    def test_one_failing_shadow_does_not_abort_the_rest(self):
        """A tester hit throttling where only 3 of 8 came through. The
        run must keep going and record which failed -- that pattern is
        itself the finding."""
        robot = self._robot()
        robot.get_named_shadow.side_effect = [
            RuntimeError("throttled"),
            *[MagicMock(payload={"state": {"reported": {}}}) for _ in range(8)],
        ]

        report, capture = self._run(robot)

        assert any(r.status == "FAILED" for r in report.results)
        assert any(r.status == "OK" for r in report.results)

    def test_capture_is_returned_for_the_dump_file(self):
        robot = self._robot()

        _report, capture = self._run(robot)

        assert isinstance(capture, dict)
        assert capture, "an empty capture would silently produce an empty dump file"
