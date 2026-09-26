"""verify_classic_cloud: the comparisons and, above all, the write gate.

The reset is the only line in this script that changes a real robot, so
most of these tests are about when it must NOT happen, and about the
fallback that keeps a tester's reset from being lost when the Prime body
turns out not to work.
"""
from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from roombapy_prime.diagnostics import Report
from roombapy_prime.models.robot_info import RobotPartsInfo
from roombapy_prime.rest_client import RestError
from roombapy_prime_tools import verify_classic_cloud as vcc

_CLASSIC_SKU = "i355640"
_PRIME_SKU = "W155020"


def _parts(counter_35: int) -> RobotPartsInfo:
    return RobotPartsInfo.from_json({
        "robot_id": "BLID", "num_parts": 2, "parts": [
            {"part_id": "35", "counter": counter_35, "count_used": 10, "count_remaining": 90},
            {"part_id": "36", "counter": 37, "count_used": 30, "count_remaining": 60},
        ],
    })


def _history(*start_times: int) -> list[dict]:
    return [{"startTime": t, "nMssn": i, "done": "ok"} for i, t in enumerate(start_times)]


def _run(func_name: str, clients, *args, sku: str = _CLASSIC_SKU, confirm_answer: bool = True):
    classic, prime = clients
    report = Report()

    @contextlib.asynccontextmanager
    async def _clients(*_a, **_k):
        yield classic, prime, "BLID", sku, report

    with patch.object(vcc, "logged_in_cloud_clients", _clients), \
         patch.object(vcc, "confirm", return_value=confirm_answer):
        asyncio.run(getattr(vcc, func_name)("u", "p", "US", "BLID", *args))
    return {r.name: r for r in report.results}


def _clients() -> tuple[MagicMock, MagicMock]:
    """A Classic and a Prime client, each with only its own methods --
    spec'd, so a call on the wrong client fails instead of passing."""
    from roombapy_prime.rest_client import ClassicRestClient, PrimeRestClient

    classic = MagicMock(spec=ClassicRestClient)
    prime = MagicMock(spec=PrimeRestClient)
    for client in (classic, prime):
        client.get_mission_history = AsyncMock()
        client.get_favorites_raw = AsyncMock()
        client.get_robot_parts = AsyncMock()
    prime.reset_robot_parts = AsyncMock(return_value={"num_parts": 1})
    classic.set_robot_part_counter = AsyncMock(return_value={"num_parts": 1})
    return classic, prime


class TestCompareReads:
    @staticmethod
    def _answers(classic, prime, classic_pages, prime_pages, favorites=([], [])):
        classic.get_mission_history.side_effect = classic_pages
        prime.get_mission_history.side_effect = prime_pages
        classic.get_favorites_raw.return_value = favorites[0]
        prime.get_favorites_raw.return_value = favorites[1]
        classic.get_robot_parts.return_value = _parts(43)

    def test_identical_answers_are_reported_identical(self):
        classic, prime = _clients()
        self._answers(classic, prime, [_history(300, 200), _history(100)],
                      [_history(300, 200), _history(100)],
                      ([{"favorite_id": "F"}], [{"favorite_id": "F"}]))

        results = _run("compare_reads", (classic, prime), 2)

        assert results["History, first page"].status == "OK"
        assert results["History, second page"].status == "OK"
        assert results["Favorites"].status == "OK"
        prime.reset_robot_parts.assert_not_awaited()
        classic.set_robot_part_counter.assert_not_awaited()

    def test_each_client_sends_its_own_form_and_paging_starts_at_the_last_record(self):
        classic, prime = _clients()
        self._answers(classic, prime, [_history(300, 200), _history(100)],
                      [_history(300, 200), _history(100)])

        _run("compare_reads", (classic, prime), 2)

        first, page = classic.get_mission_history.await_args_list
        assert first.kwargs["count"] == 2 and first.kwargs["app_id"].startswith("IOS-")
        assert page.kwargs["before"] == 200
        first, page = prime.get_mission_history.await_args_list
        assert first.kwargs["max_reports"] == 2
        assert page.kwargs["exclusive_start_timestamp"] == 200
        # Each client's own default is its generation's request.
        classic.get_favorites_raw.assert_awaited_once_with()
        prime.get_favorites_raw.assert_awaited_once_with()

    def test_different_answers_are_a_finding(self):
        classic, prime = _clients()
        self._answers(classic, prime, [_history(300, 200), _history(100)],
                      [_history(300), [{"startTime": 100, "extra": 1}]],
                      ([{"favorite_id": "F"}], []))

        results = _run("compare_reads", (classic, prime), 2)

        assert results["History, first page"].status == "FAILED"
        assert results["History, second page"].status == "FAILED"
        assert results["Favorites"].status == "FAILED"

    def test_a_refused_prime_request_is_reported_not_raised(self):
        classic, prime = _clients()
        self._answers(classic, prime, [_history(300)], [RestError("HTTP 400", status=400)])

        results = _run("compare_reads", (classic, prime), 2)

        assert results["History, Prime parameters"].status == "FAILED"
        assert "HTTP 400" in results["History, Prime parameters"].detail
        assert "History, first page" not in results

    def test_a_prime_robot_is_refused_before_any_request(self):
        classic, prime = _clients()

        results = _run("compare_reads", (classic, prime), 2, sku=_PRIME_SKU)

        assert results["Target robot"].status == "SKIPPED"
        classic.get_mission_history.assert_not_awaited()
        prime.get_mission_history.assert_not_awaited()


class TestResetPartWithPrimeBody:
    def test_an_unknown_part_writes_nothing(self):
        classic, prime = _clients()
        classic.get_robot_parts.return_value = _parts(43)

        results = _run("reset_part_with_prime_body", (classic, prime), "99")

        assert results["Target part"].status == "FAILED"
        prime.reset_robot_parts.assert_not_awaited()
        classic.set_robot_part_counter.assert_not_awaited()

    def test_a_part_already_at_zero_writes_nothing(self):
        """A reset of 0 % would show nothing either way."""
        classic, prime = _clients()
        classic.get_robot_parts.return_value = _parts(0)

        results = _run("reset_part_with_prime_body", (classic, prime), "35")

        assert results["Target part"].status == "SKIPPED"
        prime.reset_robot_parts.assert_not_awaited()

    def test_a_declined_confirmation_writes_nothing(self):
        classic, prime = _clients()
        classic.get_robot_parts.return_value = _parts(43)

        results = _run("reset_part_with_prime_body", (classic, prime), "35", confirm_answer=False)

        assert results["Reset"].status == "SKIPPED"
        prime.reset_robot_parts.assert_not_awaited()
        classic.set_robot_part_counter.assert_not_awaited()

    def test_a_prime_robot_is_refused_before_any_request(self):
        classic, prime = _clients()

        results = _run("reset_part_with_prime_body", (classic, prime), "35", sku=_PRIME_SKU)

        assert results["Target robot"].status == "SKIPPED"
        classic.get_robot_parts.assert_not_awaited()

    def test_a_working_prime_body_is_recorded_and_no_fallback_is_sent(self):
        classic, prime = _clients()
        classic.get_robot_parts.side_effect = [_parts(43), _parts(0)]

        results = _run("reset_part_with_prime_body", (classic, prime), "35")

        prime.reset_robot_parts.assert_awaited_once_with("BLID", ["35"], {"35": 0})
        classic.set_robot_part_counter.assert_not_awaited()
        assert results["Prime body on a Classic robot"].status == "OK"

    def test_an_ineffective_prime_body_falls_back_so_the_part_is_still_reset(self):
        classic, prime = _clients()
        classic.get_robot_parts.side_effect = [_parts(43), _parts(43), _parts(0)]

        results = _run("reset_part_with_prime_body", (classic, prime), "35")

        assert results["Prime body on a Classic robot"].status == "FAILED"
        classic.set_robot_part_counter.assert_awaited_once_with("BLID", "35", 0)
        assert results["Reset, Classic body"].status == "OK"

    def test_a_rejected_prime_body_also_falls_back(self):
        classic, prime = _clients()
        classic.get_robot_parts.side_effect = [_parts(43), _parts(43), _parts(0)]
        prime.reset_robot_parts.side_effect = RestError("HTTP 400", status=400)

        results = _run("reset_part_with_prime_body", (classic, prime), "35")

        assert results["Reset, Prime body"].status == "FAILED"
        classic.set_robot_part_counter.assert_awaited_once_with("BLID", "35", 0)
        assert results["Reset, Classic body"].status == "OK"

    def test_a_failed_fallback_says_where_to_finish_the_reset(self):
        classic, prime = _clients()
        classic.get_robot_parts.side_effect = [_parts(43), _parts(43), _parts(43)]

        results = _run("reset_part_with_prime_body", (classic, prime), "35")

        assert results["Reset, Classic body"].status == "FAILED"
        assert "iRobot app" in results["Reset, Classic body"].detail


def test_both_actions_at_once_are_refused_before_credentials():
    with patch("sys.argv", ["x", "--blid", "B", "--compare-reads",
                            "--reset-part-with-prime-body", "35",
                            "--i-understand-this-resets-a-real-part-counter"]), \
         patch.object(vcc, "resolve_credentials",
                      side_effect=AssertionError("must not ask for credentials")):
        try:
            vcc.main()
        except SystemExit as exc:
            assert exc.code == 1
        else:  # pragma: no cover - the assertion below explains the failure
            raise AssertionError("main() should have exited")


def test_compare_reads_dispatches_without_a_gate():
    ran: list = []
    with patch("sys.argv", ["x", "--blid", "B", "--compare-reads"]), \
         patch.object(vcc, "resolve_credentials", return_value=("u", "p")), \
         patch.object(vcc, "run_script",
                      side_effect=lambda coro: ran.append(coro) or coro.close() or 0):
        try:
            vcc.main()
        except SystemExit as exc:
            assert exc.code == 0
    assert len(ran) == 1
