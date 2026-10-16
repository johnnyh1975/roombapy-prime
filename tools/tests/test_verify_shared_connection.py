"""Tests for verify_shared_connection.py (issue #91).

The script answers one question for a tester with two Prime robots on
one account: can one MQTT connection serve both? Its answer decides how
the library connects. These tests pin the parts that could give a wrong
answer quietly:

  - what a token covers (its claims decide, not the response's list),
  - which drops count (a token refresh is ours, a broker drop is not),
  - that a robot answering only BEFORE the refresh does not pass,
  - and that nothing secret reaches the pasted output.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

import paho.mqtt.client as mqtt
import pytest

from roombapy_prime.auth import ConnectionToken
from roombapy_prime.diagnostics import Report
from roombapy_prime.mqtt_client import ShadowError, ShadowResponse, SubscriptionRejectedError
from roombapy_prime_tools import verify_shared_connection as mod
from roombapy_prime_tools.verify_shared_connection import (
    SharedConnectionProbe,
    Target,
    choose_targets,
    report_tokens,
    token_claims_devices,
    token_covering,
)

BLID_A = "0123456789ABCDEF"
BLID_B = "FEDCBA9876543210"
CLIENT_ID = "app-0123456789abcdef-client"


def _iot_token(devices, padded=True) -> str:
    claims = {"cognito_id": "x", "clientid": CLIENT_ID, "expires_ts": 1, "devices": devices}
    raw = base64.b64encode(json.dumps(claims).encode()).decode()
    return raw if padded else raw.rstrip("=")


def _token(claim_devices=None, listed=(), client_id=CLIENT_ID, expires=None) -> ConnectionToken:
    return ConnectionToken(
        client_id=client_id,
        iot_token=_iot_token(claim_devices) if claim_devices is not None else "not-base64!",
        iot_signature="SIGNATURE",
        iot_authorizer_name="authorizer",
        expires=expires,
        devices=list(listed),
    )


def _login(tokens, robots=None):
    robots = robots if robots is not None else {
        BLID_A: {"name": "Upstairs", "sku": "Y351020"},
        BLID_B: {"name": "Downstairs", "sku": "G185020"},
    }
    return SimpleNamespace(connection_tokens=tokens, robots=robots)


TARGETS = [Target(BLID_A, "Upstairs"), Target(BLID_B, "Downstairs")]


class TestWhatATokenCovers:
    def test_the_claims_name_the_robots(self):
        assert token_claims_devices(_token({BLID_A: 0, BLID_B: 0})) == [BLID_A, BLID_B]

    def test_a_token_without_padding_still_reads(self):
        token = _token()
        token = ConnectionToken(**{**token.__dict__, "iot_token": _iot_token({BLID_A: 0}, padded=False)})
        assert token_claims_devices(token) == [BLID_A]

    def test_unreadable_claims_say_so_rather_than_naming_nobody(self):
        """None, not []: "could not read" must not look like "covers
        no robot" -- the second would fail check 1 for the wrong reason."""
        assert token_claims_devices(_token()) is None

    def test_one_token_naming_both_robots_covers_both(self):
        token = _token({BLID_A: 0, BLID_B: 0})
        assert token_covering(_login([token]), [BLID_A, BLID_B]) is token

    def test_robots_spread_over_two_tokens_are_not_covered_by_one(self):
        login = _login([_token({BLID_A: 0}), _token({BLID_B: 0})])
        assert token_covering(login, [BLID_A, BLID_B]) is None

    def test_the_claims_decide_over_the_response_list(self):
        """The authorizer reads the claims. A response list naming one
        robot beside claims naming both is still one connection's worth."""
        token = _token({BLID_A: 0, BLID_B: 0}, listed=[BLID_A])
        assert token_covering(_login([token]), [BLID_A, BLID_B]) is token

    def test_the_response_list_answers_when_the_claims_cannot(self):
        token = _token(None, listed=[BLID_A, BLID_B])
        assert token_covering(_login([token]), [BLID_A, BLID_B]) is token


class TestWhichRobotsAreTested:
    def test_only_prime_robots_by_default(self):
        robots = {
            BLID_A: {"name": "Upstairs", "sku": "Y351020"},
            BLID_B: {"name": "S9", "sku": "R980020"},
        }
        assert choose_targets(_login([], robots), None) == [Target(BLID_A, "Upstairs")]

    def test_a_named_robot_the_account_lacks_aborts(self):
        with pytest.raises(SystemExit):
            choose_targets(_login([]), [BLID_A, "0000000000000000"])


class TestNothingSecretIsPrinted:
    def test_the_token_report_names_no_id_token_or_client_id(self, capsys):
        token = _token({BLID_A: 0, BLID_B: 0}, listed=[BLID_A, BLID_B], expires=None)
        report = Report()

        report_tokens(_login([token]), TARGETS, report)

        out = capsys.readouterr().out + str(report.results)
        assert BLID_A not in out and BLID_B not in out
        assert CLIENT_ID not in out
        assert token.iot_token not in out and "SIGNATURE" not in out
        assert "...CDEF" in out and "...3210" in out

    def test_one_token_for_both_passes_check_one(self):
        report = Report()
        report_tokens(_login([_token({BLID_A: 0, BLID_B: 0})]), TARGETS, report)
        assert report.results[-1].status == "OK"

    def test_a_robot_no_token_names_fails_check_one_by_its_short_id(self):
        report = Report()
        report_tokens(_login([_token({BLID_A: 0})]), TARGETS, report)
        result = report.results[-1]
        assert result.status == "FAILED"
        assert "...3210" in result.detail and BLID_B not in result.detail


    def test_the_library_warnings_shorten_robot_ids_too(self, capsys):
        handler = mod.mask_library_logs([BLID_B])
        try:
            mod.logging.getLogger("roombapy_prime.mqtt_client").warning(
                "SUBACK missing for %s", f"$aws/things/{BLID_B}/shadow/get/accepted",
            )
        finally:
            logger = mod.logging.getLogger("roombapy_prime")
            logger.removeHandler(handler)
            logger.propagate = True

        err = capsys.readouterr().err
        assert "...3210/shadow/get/accepted" in err and BLID_B not in err


class FakeClient:
    """Just enough of PrimeMqttClient: topics, generations, drops."""

    def __init__(self, token, refuse=()):
        self._token = token
        self.generation = 1
        self.connected = True
        self.closed = False
        self.disconnect_reason = None
        self.last_disconnect_was_deliberate = False
        self.due = None
        self.refuse = set(refuse)
        self.subs: dict[str, list] = {}
        self.published: list[str] = []
        self.reconnects = 0
        self._ends: dict[int, tuple[str, bool]] = {}
        self._changed = asyncio.Event()

    async def subscribe(self, topic, callback):
        if any(blid in topic for blid in self.refuse):
            raise SubscriptionRejectedError(f"SUBACK refused for {topic}")
        self.subs.setdefault(topic, []).append(callback)

    async def _publish(self, topic, payload, timeout=5.0):
        if not self.connected:
            raise ShadowError(f"PUBLISH to {topic} has no connection to go out on.")
        self.published.append(topic)
        if topic.endswith("/shadow/get"):
            self.deliver(topic + "/accepted")

    def deliver(self, topic):
        for pattern, callbacks in self.subs.items():
            if mqtt.topic_matches_sub(pattern, topic):
                for callback in callbacks:
                    callback(ShadowResponse(topic=topic, payload={}))

    def drop(self, reason, deliberate=False):
        self._ends[self.generation] = (reason, deliberate)
        self.disconnect_reason = reason
        self.last_disconnect_was_deliberate = deliberate
        self.connected = False
        self._changed.set()

    def ended(self, generation):
        return self._ends.get(generation)

    async def wait_for_disconnect(self, generation=None):
        while self.connected and self.generation == generation:
            self._changed.clear()
            await self._changed.wait()
        return self.disconnect_reason or "unknown"

    async def wait_until_settled(self):
        await asyncio.sleep(0)

    async def reconnect(self, timeout=10.0):
        self.reconnects += 1
        self.generation += 1
        self.connected = True

    def seconds_until_token_refresh_due(self):
        return self.due

    async def replace_token(self, token):
        self._token = token
        self.drop("deliberate: token refresh or reconnect", deliberate=True)
        await asyncio.sleep(0)
        self.generation += 1
        self.connected = True


def _probe(client, relogin=None):
    async def no_relogin():
        raise AssertionError("no relogin expected")
    return SharedConnectionProbe(
        client, TARGETS, relogin or no_relogin, "irbt", all_blids=[BLID_A, BLID_B],
    )


def _verdicts(probe, send_find=False):
    return {name: (status, detail) for name, status, detail in probe.verdicts(75, send_find)}


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0)


class TestOneConnectionForTwoRobots:
    @pytest.mark.asyncio
    async def test_both_robots_answering_without_a_drop_passes(self):
        probe = _probe(FakeClient(_token({BLID_A: 0, BLID_B: 0})))

        await probe.subscribe_all()
        await probe.ask_every_robot()

        verdicts = _verdicts(probe)
        assert verdicts["2a. Subscriptions accepted for every robot"][0] == "OK"
        assert verdicts["2b. Every robot answered on the shared connection"][0] == "OK"
        assert verdicts["2c. No connection drops"][0] == "OK"
        # Asked for each robot's own shadow, on the one connection.
        assert probe.client.published == [
            f"$aws/things/{BLID_A}/shadow/get", f"$aws/things/{BLID_B}/shadow/get",
        ]

    @pytest.mark.asyncio
    async def test_a_refused_robot_fails_and_is_named_by_its_short_id(self):
        probe = _probe(FakeClient(_token({BLID_A: 0}), refuse=[BLID_B]))

        await probe.subscribe_all()
        await probe.ask_every_robot()

        status, detail = _verdicts(probe)["2a. Subscriptions accepted for every robot"]
        assert status == "FAILED"
        assert "Downstairs" in detail and BLID_B not in detail
        assert _verdicts(probe)["2b. Every robot answered on the shared connection"][0] == "FAILED"

    @pytest.mark.asyncio
    async def test_the_summary_line_counts_a_failed_check(self):
        """Report.summary() counts "FAILED" only; a short "FAIL" printed
        "0 failed" under a failed check."""
        probe = _probe(FakeClient(_token({BLID_A: 0}), refuse=[BLID_B]))
        await probe.subscribe_all()
        report = Report()

        for name, status, detail in probe.verdicts(75, False):
            report.add(name, status, detail)

        assert report.summary()[1] >= 2

    @pytest.mark.asyncio
    async def test_a_broker_drop_counts_and_a_refresh_does_not(self):
        """The whole measurement: 3,500 drops an hour was the eviction.
        Our own reconnect for the token refresh must not look like one,
        and a broker drop must not hide behind it."""
        client = FakeClient(_token({BLID_A: 0, BLID_B: 0}))
        probe = _probe(client)
        watch = asyncio.create_task(probe.watch_connection())
        await _settle()

        client.drop("deliberate: token refresh or reconnect", deliberate=True)
        client.generation += 1
        client.connected = True
        await _settle()
        assert probe.drops == []

        client.drop("Unspecified error")
        await _settle()

        assert [why for _, why in probe.drops] == ["Unspecified error"]
        assert client.connected and client.reconnects == 1
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
        assert _verdicts(probe)["2c. No connection drops"][0] == "FAILED"

    @pytest.mark.asyncio
    async def test_the_watch_ends_with_the_run_instead_of_spinning(self):
        client = FakeClient(_token({BLID_A: 0, BLID_B: 0}))
        probe = _probe(client)
        watch = asyncio.create_task(probe.watch_connection())
        await _settle()

        client.closed = True
        client.drop("deliberate: token refresh or reconnect", deliberate=True)

        await asyncio.wait_for(watch, 1)
        assert probe.drops == [] and client.reconnects == 0


class TestTheTokenRefresh:
    @pytest.mark.asyncio
    async def test_both_robots_must_answer_after_the_refresh(self):
        old = _token({BLID_A: 0, BLID_B: 0})
        new = _token({BLID_A: 0, BLID_B: 0})
        client = FakeClient(old)
        client.due = 0.0

        async def relogin():
            client.due = None  # one refresh, then the test ends
            return _login([new])

        probe = _probe(client, relogin)
        await probe.subscribe_all()
        await probe.ask_every_robot()

        await probe.refresh_token_when_due(send_find=False)

        assert client._token is new
        status, detail = _verdicts(probe)["2d. Every robot answered after the token refresh"]
        assert status == "OK" and "same client id" in detail

    @pytest.mark.asyncio
    async def test_a_robot_silent_after_the_refresh_fails(self):
        """Answers from before the refresh do not carry over: the point
        of the long run is the refreshed connection."""
        client = FakeClient(_token({BLID_A: 0, BLID_B: 0}))
        probe = _probe(client)
        await probe.subscribe_all()
        await probe.ask_every_robot()

        probe.refreshes.append((3300.0, True))
        probe.refreshed_at = 3300.0
        client.deliver(f"$aws/things/{BLID_A}/shadow/get/accepted")

        status, detail = _verdicts(probe)["2d. Every robot answered after the token refresh"]
        assert status == "FAILED" and "Downstairs" in detail

    def test_a_run_that_ends_before_the_refresh_skips_rather_than_passes(self):
        probe = _probe(FakeClient(_token({BLID_A: 0, BLID_B: 0})))
        assert _verdicts(probe)["2d. Every robot answered after the token refresh"][0] == "SKIPPED"


class TestTheFindGate:
    def test_declining_find_asks_for_no_password(self, capsys):
        with patch.object(mod, "confirm", return_value=False), \
             patch.object(mod, "resolve_credentials") as credentials, \
             pytest.raises(SystemExit):
            mod.main(["--find"])

        credentials.assert_not_called()
        assert "no login was made" in capsys.readouterr().out

    def test_token_only_needs_no_find_confirmation(self):
        with patch.object(mod, "confirm") as asked, \
             patch.object(mod, "resolve_credentials", return_value=("u", "p")), \
             patch.object(mod, "run_script", return_value=0) as ran, \
             pytest.raises(SystemExit):
            mod.main(["--find", "--token-only"])

        asked.assert_not_called()
        ran.call_args.args[0].close()  # the coroutine that was never run


class TestTheWholeRun:
    def test_one_login_one_connection_and_a_verdict(self, capsys, monkeypatch):
        """End to end with the cloud faked: one login, one connection
        for both robots, and a conclusion printed under the table."""
        token = _token({BLID_A: 0, BLID_B: 0}, listed=[BLID_A, BLID_B])
        login_result = _login([token])
        login_result.mqtt_endpoint = "broker.example.com"
        login_result.irbt_topic_prefix = "irbt"
        account = SimpleNamespace(login_result=login_result)
        logins = []

        async def fake_login(*args, **kwargs):
            logins.append(args[1:])
            return account

        async def no_relogin():
            raise AssertionError("no refresh inside this run")

        account.relogin = no_relogin
        clients = []

        class Client(FakeClient):
            def __init__(self, token, endpoint, blid):
                super().__init__(token)
                self.connected = False
                clients.append(self)

            async def connect(self):
                self.connected = True

            async def disconnect(self):
                self.closed = True
                self.drop("deliberate: token refresh or reconnect", deliberate=True)

        monkeypatch.setattr(mod.CloudAccount, "login", fake_login)
        monkeypatch.setattr(mod, "PrimeMqttClient", Client)
        monkeypatch.setattr(mod, "LAST_ANSWER_WAIT_SECONDS", 0)
        # The real one detaches roombapy_prime's logs from the root logger,
        # where every later test's caplog listens.
        monkeypatch.setattr(mod, "mask_library_logs", lambda blids: None)

        report = asyncio.run(mod.run(
            "user@example.com", "hunter2", "US", None, 0.0005, 5, False, False,
        ))

        assert len(logins) == 1 and len(clients) == 1
        statuses = {r.name: r.status for r in report.results}
        assert statuses["2b. Every robot answered on the shared connection"] == "OK"
        assert statuses["2c. No connection drops"] == "OK"
        assert statuses["2d. Every robot answered after the token refresh"] == "SKIPPED"
        out = capsys.readouterr().out
        assert "not every check ran" in out
        assert "hunter2" not in out and BLID_A not in out and BLID_B not in out
