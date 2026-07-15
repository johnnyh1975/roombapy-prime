"""Tests for the testable parts of diagnostics.py -- the Report class
and helper functions, WITHOUT real network calls. The actual live
validation script by its nature can't be tested in a CI environment
without a real Prime account -- that's the whole point of the script.
These tests only ensure that the reporting logic and the best-effort
helper functions themselves are correct."""

import pytest

from roombapy_prime.diagnostics import Report, _extract_first_id


def test_report_add_and_summary() -> None:
    report = Report()
    report.add("Check A", "OK")
    report.add("Check B", "FAILED", "irgendein Fehler")
    report.add("Check C", "SKIPPED", "kein Grund vorhanden")

    ok, failed, skipped = report.summary()
    assert (ok, failed, skipped) == (1, 1, 1)


def test_report_to_markdown_contains_all_entries() -> None:
    report = Report()
    report.add("Login", "OK")
    report.add("Kartenbearbeitung", "SKIPPED", "wird nie automatisch ausgefuehrt")

    markdown = report.to_markdown()

    assert "Login" in markdown
    assert "Kartenbearbeitung" in markdown
    assert "wird nie automatisch ausgefuehrt" in markdown
    assert "1 OK, 0 failed, 1 skipped" in markdown


def test_extract_first_id_flat_dict() -> None:
    assert _extract_first_id({"householdId": "hh1"}, ["householdId", "id"]) == "hh1"


def test_extract_first_id_nested_dict() -> None:
    data = {"households": [{"id": "hh1", "name": "Home"}]}
    assert _extract_first_id(data, ["householdId", "id"]) == "hh1"


def test_extract_first_id_not_found_returns_none() -> None:
    assert _extract_first_id({"foo": "bar"}, ["householdId", "id"]) is None


def test_extract_first_id_prefers_first_matching_key() -> None:
    assert _extract_first_id({"householdId": "hh1", "id": "other"}, ["householdId", "id"]) == "hh1"


def test_report_redact_replaces_secret_in_detail() -> None:
    from roombapy_prime.diagnostics import Report

    report = Report()
    report.add("Login", "FAILED", "AuthError: bad credentials for geheim@example.com")
    report.redact("geheim@example.com", "supersecretpw")

    assert "geheim@example.com" not in report.results[0].detail
    assert "[REDACTED]" in report.results[0].detail


def test_report_redact_ignores_empty_secrets() -> None:
    from roombapy_prime.diagnostics import Report

    report = Report()
    report.add("Login", "OK")
    report.redact("", None)  # type: ignore[arg-type]

    assert report.results[0].detail == ""


def test_report_redact_multiple_secrets() -> None:
    from roombapy_prime.diagnostics import Report

    report = Report()
    report.add("X", "FAILED", "user=alice pass=hunter2")
    report.redact("alice", "hunter2")

    assert "alice" not in report.results[0].detail
    assert "hunter2" not in report.results[0].detail


def test_build_issue_url_contains_encoded_summary() -> None:
    from roombapy_prime.diagnostics import Report, build_issue_url

    report = Report()
    report.add("Login", "OK")
    report.add("Favoriten abrufen", "FAILED", "HTTP 500")

    url = build_issue_url(report, repo="someowner/somerepo")

    assert url.startswith("https://github.com/someowner/somerepo/issues/new?")
    assert "title=" in url
    assert "body=" in url
    # URL-encoded, so raw text won't appear, but the encoded form of a
    # distinctive substring should
    from urllib.parse import quote

    assert quote("Favoriten abrufen") in url


def test_to_markdown_includes_version_and_platform_info() -> None:
    from roombapy_prime.diagnostics import Report

    report = Report()
    report.add("Login", "OK")

    markdown = report.to_markdown()

    assert "roombapy-prime" in markdown
    assert "Python" in markdown


def test_report_device_info_extracts_known_candidates() -> None:
    """UPDATED (session 25) -- now uses the real nesting confirmed via
    live data (chairstacker), payload["state"]["reported"], no longer
    the original (wrong) top-level assumption."""
    from roombapy_prime.diagnostics import Report, _report_device_info
    from roombapy_prime.mqtt_client import ShadowResponse

    report = Report()
    state = ShadowResponse(
        topic="t",
        payload={"state": {"reported": {"sku": "G185020", "soldAsSku": "G185020", "extraField": "x"}}},
    )
    _report_device_info(report, state)

    assert len(report.results) == 1
    assert report.results[0].status == "OK"
    assert "'sku': 'G185020'" in report.results[0].detail
    assert "extraField" in report.results[0].detail  # state.reported-Schluesselliste enthaelt auch Unbekanntes


def test_report_device_info_handles_no_state() -> None:
    from roombapy_prime.diagnostics import Report, _report_device_info

    report = Report()
    _report_device_info(report, None)

    assert len(report.results) == 0


def test_report_device_info_handles_no_known_candidates() -> None:
    from roombapy_prime.diagnostics import Report, _report_device_info
    from roombapy_prime.mqtt_client import ShadowResponse

    report = Report()
    state = ShadowResponse(topic="t", payload={"state": {"reported": {"somethingElse": 1}}})
    _report_device_info(report, state)

    assert "none of the suspected candidate fields" in report.results[0].detail
    assert "somethingElse" in report.results[0].detail


def test_report_device_info_handles_missing_state_key_gracefully() -> None:
    """Verteidigung falls payload kein 'state'-Feld hat (z.B. eine ganz
    andere Antwortform) -- darf nicht abstuerzen."""
    from roombapy_prime.diagnostics import Report, _report_device_info
    from roombapy_prime.mqtt_client import ShadowResponse

    report = Report()
    state = ShadowResponse(topic="t", payload={"somethingUnexpected": True})
    _report_device_info(report, state)

    assert len(report.results) == 1
    assert "none of the suspected candidate fields" in report.results[0].detail


def test_report_tier_inference_smart_when_settings_succeeded() -> None:
    from roombapy_prime.diagnostics import Report, _report_tier_inference
    from roombapy_prime.mqtt_client import ShadowResponse

    report = Report()
    _report_tier_inference(report, ShadowResponse(topic="t", payload={}))

    assert "SMART" in report.results[0].detail


def test_report_tier_inference_ephemeral_when_settings_failed() -> None:
    from roombapy_prime.diagnostics import Report, _report_tier_inference

    report = Report()
    _report_tier_inference(report, None)

    assert "EPHEMERAL" in report.results[0].detail


class _FakeRobotForTopicPrefix:
    def __init__(self, irbt_topic_prefix: str | None, deployment: dict | None = None) -> None:
        self._irbt_topic_prefix = irbt_topic_prefix
        self.deployment = deployment or {}


def test_report_topic_prefix_status_ok_when_found() -> None:
    from roombapy_prime.diagnostics import Report, _report_topic_prefix_status

    report = Report()
    robot = _FakeRobotForTopicPrefix("v011-irbthbu")
    _report_topic_prefix_status(report, robot)

    assert report.results[0].status == "OK"
    assert "v011-irbthbu" in report.results[0].detail


def test_report_topic_prefix_status_reports_actual_deployment_keys_when_missing() -> None:
    """NEW (session 41) -- regression test against the bug a live test
    found: the guessed keys don't match reality for this account. Must
    report the ACTUAL deployment keys, not silently fail with no
    actionable information."""
    from roombapy_prime.diagnostics import Report, _report_topic_prefix_status

    report = Report()
    robot = _FakeRobotForTopicPrefix(None, deployment={"httpBase": "x", "mqtt": "y", "someOtherKey": "z"})
    _report_topic_prefix_status(report, robot)

    assert report.results[0].status == "FAILED"
    assert "httpBase" in report.results[0].detail
    assert "someOtherKey" in report.results[0].detail
    # values must NOT leak, only structure/types (same rule as _shallow_summary elsewhere)
    assert "\"x\"" not in report.results[0].detail
    assert "'x'" not in report.results[0].detail


def test_report_topic_prefix_status_handles_empty_deployment() -> None:
    from roombapy_prime.diagnostics import Report, _report_topic_prefix_status

    report = Report()
    robot = _FakeRobotForTopicPrefix(None, deployment={})
    _report_topic_prefix_status(report, robot)

    assert report.results[0].status == "FAILED"
    assert "empty" in report.results[0].detail


def test_shallow_summary_dict_shows_keys_and_types() -> None:
    from roombapy_prime.diagnostics import _shallow_summary

    result = _shallow_summary({"foo": "bar", "count": 5})
    assert result == {"foo": "str", "count": "int"}


def test_shallow_summary_list_shows_length_and_first_element() -> None:
    from roombapy_prime.diagnostics import _shallow_summary

    result = _shallow_summary([{"id": "a1"}, {"id": "a2"}])
    assert result == "list[2] first element: {'id': '...'}"


def test_shallow_summary_empty_list() -> None:
    from roombapy_prime.diagnostics import _shallow_summary

    assert _shallow_summary([]) == "[] (empty list)"


def test_shallow_summary_never_leaks_actual_values() -> None:
    """Security-relevant: must never show the actual value, only the
    type -- protection against accidentally leaking sensitive data in
    a shared report."""
    from roombapy_prime.diagnostics import _shallow_summary

    result = _shallow_summary({"address": "123 Secret Street", "email": "user@example.com"})
    assert "123 Secret Street" not in str(result)
    assert "user@example.com" not in str(result)


# =========================================================================
# Raw capture + redaction for --dump-config (session 24)
# =========================================================================


@pytest.mark.asyncio
async def test_try_captures_raw_result_when_capture_dict_given() -> None:
    from roombapy_prime.diagnostics import Report, _try

    report = Report()
    capture: dict = {}

    async def fake_call():
        return {"foo": "bar"}

    result = await _try(report, "Test-Check", fake_call(), capture=capture)

    assert result == {"foo": "bar"}
    assert capture == {"Test-Check": {"foo": "bar"}}
    assert report.results[0].status == "OK"


@pytest.mark.asyncio
async def test_try_does_not_capture_on_failure() -> None:
    from roombapy_prime.diagnostics import Report, _try

    report = Report()
    capture: dict = {}

    async def failing_call():
        raise ValueError("boom")

    await _try(report, "Test-Check", failing_call(), capture=capture)

    assert capture == {}
    assert report.results[0].status == "FAILED"


@pytest.mark.asyncio
async def test_try_without_capture_param_behaves_as_before() -> None:
    """Abwaertskompatibilitaet: capture ist optional, Standardverhalten
    unveraendert."""
    from roombapy_prime.diagnostics import Report, _try

    report = Report()

    async def fake_call():
        return {"foo": "bar"}

    result = await _try(report, "Test-Check", fake_call())
    assert result == {"foo": "bar"}


def test_redact_raw_capture_masks_sensitive_keys() -> None:
    from roombapy_prime.diagnostics import _redact_raw_capture

    data = {"address": "123 Main St", "latitude": 52.5, "batteryLevel": 80}
    result = _redact_raw_capture(data, [])

    assert result["address"] == "[REDACTED]"
    assert result["latitude"] == "[REDACTED]"
    assert result["batteryLevel"] == 80  # nicht sensibel, bleibt sichtbar


def test_redact_raw_capture_replaces_literal_secrets_in_strings() -> None:
    from roombapy_prime.diagnostics import _redact_raw_capture

    data = {"someField": "value containing secretuser123"}
    result = _redact_raw_capture(data, ["secretuser123"])

    assert "secretuser123" not in result["someField"]
    assert "[REDACTED]" in result["someField"]


def test_redact_raw_capture_handles_nested_lists_and_dicts() -> None:
    from roombapy_prime.diagnostics import _redact_raw_capture

    data = {"items": [{"ssid": "MyWifiNetwork", "name": "Living Room"}]}
    result = _redact_raw_capture(data, [])

    assert result["items"][0]["ssid"] == "[REDACTED]"
    assert result["items"][0]["name"] == "Living Room"


def test_redact_raw_capture_preserves_non_sensitive_structure() -> None:
    """The point of the dump file: real values for unknown fields not
    recognized as sensitive stay visible -- that's what deliberately
    distinguishes it from _shallow_summary()."""
    from roombapy_prime.diagnostics import _redact_raw_capture

    data = {"sku": "G185020", "softwareVer": "p25-405+9.3.7+I4.6.150", "missionCount": 42}
    result = _redact_raw_capture(data, [])

    assert result == data


def test_extract_first_id_finds_confirmed_household_id_field() -> None:
    """Regression test against the bug found in that session:
    _extract_first_id only searched for householdId/id, real data
    (chairstacker) shows household_id (snake_case)."""
    from roombapy_prime.diagnostics import _extract_first_id

    real_households = [
        {
            "household_id": "c4714a01-f6ad-4ace-b111-d326d83867a5",
            "owner_cognito_id": "us-east-1:abc",
            "household_robots": [],
            "household_users": [],
        }
    ]
    result = _extract_first_id(real_households, ["household_id", "householdId", "id"])

    assert result == "c4714a01-f6ad-4ace-b111-d326d83867a5"
