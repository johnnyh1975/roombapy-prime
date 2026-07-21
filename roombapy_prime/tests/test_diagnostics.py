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


@pytest.mark.asyncio
async def test_check_candidate_shadows_queries_all_four_by_name() -> None:
    """NEW (this session) -- factored out of run() specifically so this
    is unit-testable, since run() as a whole has no dedicated test.
    Verifies all four never-before-queried named shadows
    (ro-currentstate/ro-stats/ro-services/ro-configinfo) are actually
    requested by name, and that a successful result gets captured."""
    from unittest.mock import AsyncMock, MagicMock

    from roombapy_prime.diagnostics import Report, _check_candidate_shadows
    from roombapy_prime.mqtt_client import ShadowResponse

    robot = MagicMock()
    robot.get_named_shadow = AsyncMock(
        side_effect=lambda name: ShadowResponse(topic="t", payload={"name": name})
    )
    report = Report()
    raw_capture: dict = {}

    await _check_candidate_shadows(report, robot, raw_capture)

    called_names = [call.args[0] for call in robot.get_named_shadow.await_args_list]
    assert called_names == ["ro-currentstate", "ro-stats", "ro-services", "ro-configinfo"]
    assert all(entry.status == "OK" for entry in report.results)
    assert raw_capture['Fetching named shadow "ro-currentstate" (get_named_shadow)'].payload == {
        "name": "ro-currentstate"
    }


@pytest.mark.asyncio
async def test_check_candidate_shadows_reports_failure_without_crashing() -> None:
    """A candidate shadow timing out (plausible -- these have never been
    queried before, might not even exist as a real name) must not abort
    the other checks."""
    from unittest.mock import AsyncMock, MagicMock

    from roombapy_prime.diagnostics import Report, _check_candidate_shadows

    robot = MagicMock()

    async def _flaky(name: str):
        if name == "ro-stats":
            raise TimeoutError("no response")
        return MagicMock(payload={"name": name})

    robot.get_named_shadow = AsyncMock(side_effect=_flaky)
    report = Report()
    raw_capture: dict = {}

    await _check_candidate_shadows(report, robot, raw_capture)

    statuses = {entry.name: entry.status for entry in report.results}
    assert statuses['Fetching named shadow "ro-currentstate" (get_named_shadow)'] == "OK"
    assert statuses['Fetching named shadow "ro-stats" (get_named_shadow)'] == "FAILED"
    assert statuses['Fetching named shadow "ro-services" (get_named_shadow)'] == "OK"
    assert statuses['Fetching named shadow "ro-configinfo" (get_named_shadow)'] == "OK"


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


def test_shallow_summary_safe_for_geojson_geometry() -> None:
    """NEW (session 45) -- dedicated regression test for the new
    reliance this project places on _shallow_summary(): capturing a
    type-only structure summary of an ENTIRE map bundle (session 45's
    diagnostics.py change) rests on this function never leaking actual
    coordinate values for realistic GeoJSON-shaped geometry, not just
    the simple flat dicts the pre-existing leak test covers. A floor
    plan's actual coordinates are exactly the kind of thing this
    project has repeatedly said is more personal than most other data
    it captures -- this test exists so that property is verified
    directly against a realistic shape, not just assumed to generalize
    from the simpler existing test."""
    from roombapy_prime.diagnostics import _shallow_summary

    realistic_room_with_geometry = {
        "room_id": "abc123",
        "name": "Living Room",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[3.14159, 2.71828], [1.41421, 1.73205], [2.23606, 1.61803]]],
        },
    }

    result = _shallow_summary(realistic_room_with_geometry)
    result_str = str(result)

    for coordinate in ("3.14159", "2.71828", "1.41421", "1.73205", "2.23606", "1.61803"):
        assert coordinate not in result_str
    # field names themselves are fine to show -- only their VALUES must never appear
    assert "geometry" in result_str


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


def test_redact_raw_capture_masks_credential_field_names() -> None:
    """NEW (session 54, security hardening) -- regression test for a
    latent gap found during a security review: ConnectionToken's
    iot_token/iot_signature and RobotLoginEntry's user_cert weren't
    covered by the key-based redaction, only the AWS Cognito
    credential field names were. No current raw_capture call site
    actually captures these objects, but this function's whole purpose
    is to be a general-purpose safety net, not just cover the specific
    fields anyone happened to test against."""
    from roombapy_prime.diagnostics import _redact_raw_capture

    data = {
        "iot_token": "super-secret-mqtt-token",
        "iot_signature": "super-secret-signature",
        "user_cert": "-----BEGIN CERTIFICATE-----FAKE-----",
        "cognitoId": "eu-west-1:00000000-0000-0000-0000-000000000000",
        "sku": "i7",  # not sensitive, should remain visible
    }
    result = _redact_raw_capture(data, [])

    assert result["iot_token"] == "[REDACTED]"
    assert result["iot_signature"] == "[REDACTED]"
    assert result["user_cert"] == "[REDACTED]"
    assert result["cognitoId"] == "[REDACTED]"
    assert result["sku"] == "i7"


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


def test_redact_aws_url_secrets_strips_signature_and_token_but_keeps_path() -> None:
    """NEW (this session) -- prompted directly by a real leak: more than
    one tester pasted raw terminal output containing full presigned S3
    URLs with these query parameters completely intact. The base
    path/host must survive (useful for reverse engineering), only the
    actual secret-bearing query parameters get stripped."""
    from roombapy_prime.diagnostics import redact_aws_url_secrets

    url = (
        "https://s3.amazonaws.com/elpasodata018-pmaptransferbucket-1pckk9n2mafep/"
        "p2maps/v011/dload_livemap/BLID/p2mapv_geojson.tgz"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=ASIAU3IUYSB7OJ4JSPFJ%2F20260720%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20260720T145204Z"
        "&X-Amz-Expires=3600"
        "&X-Amz-SignedHeaders=host"
        "&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEN3abcdefghijklmnopqrstuvwxyz"
        "&X-Amz-Signature=72e03b4e53b0c02c8c1027cd3493cbc421abd850348789d18388938660353d0e"
    )

    redacted = redact_aws_url_secrets(url)

    assert "ASIAU3IUYSB7OJ4JSPFJ" not in redacted
    assert "IQoJb3JpZ2luX2VjEN3abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "72e03b4e53b0c02c8c1027cd3493cbc421abd850348789d18388938660353d0e" not in redacted
    assert redacted.count("[REDACTED]") == 3
    # The base path -- genuinely useful for reverse engineering -- survives.
    assert "dload_livemap/BLID/p2mapv_geojson.tgz" in redacted
    assert "X-Amz-Expires=3600" in redacted


def test_redact_aws_url_secrets_leaves_ordinary_text_unchanged() -> None:
    from roombapy_prime.diagnostics import redact_aws_url_secrets

    text = "just an ordinary reported-state string, no URLs at all"
    assert redact_aws_url_secrets(text) == text


def test_redact_raw_capture_applies_aws_url_redaction_to_any_string_value() -> None:
    """The AWS-secret redaction isn't limited to a specific key name
    (unlike sensitive_keys) -- it applies to every string value,
    wherever a presigned URL happens to appear."""
    from roombapy_prime.diagnostics import _redact_raw_capture

    data = {
        "livemap_url": (
            "https://s3.amazonaws.com/bucket/path?X-Amz-Signature=abcdef123456&X-Amz-Expires=3600"
        )
    }
    result = _redact_raw_capture(data, [])

    assert "abcdef123456" not in result["livemap_url"]
    assert "[REDACTED]" in result["livemap_url"]
    assert "X-Amz-Expires=3600" in result["livemap_url"]


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
