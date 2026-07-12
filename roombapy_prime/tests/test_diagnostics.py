"""Tests fuer die testbaren Teile von diagnostics.py -- Report-Klasse
und Hilfsfunktionen, OHNE echte Netzwerkaufrufe. Das eigentliche
Live-Validierungsskript kann per Natur nicht in einer CI-Umgebung ohne
echten Prime-Account getestet werden -- das ist der ganze Punkt des
Skripts. Diese Tests stellen nur sicher, dass die Berichtslogik und
die Best-Effort-Hilfsfunktionen selbst korrekt sind."""

from roombapy_prime.diagnostics import Report, _extract_first_id


def test_report_add_and_summary() -> None:
    report = Report()
    report.add("Check A", "OK")
    report.add("Check B", "FEHLGESCHLAGEN", "irgendein Fehler")
    report.add("Check C", "UEBERSPRUNGEN", "kein Grund vorhanden")

    ok, failed, skipped = report.summary()
    assert (ok, failed, skipped) == (1, 1, 1)


def test_report_to_markdown_contains_all_entries() -> None:
    report = Report()
    report.add("Login", "OK")
    report.add("Kartenbearbeitung", "UEBERSPRUNGEN", "wird nie automatisch ausgefuehrt")

    markdown = report.to_markdown()

    assert "Login" in markdown
    assert "Kartenbearbeitung" in markdown
    assert "wird nie automatisch ausgefuehrt" in markdown
    assert "1 OK, 0 fehlgeschlagen, 1 uebersprungen" in markdown


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
    report.add("Login", "FEHLGESCHLAGEN", "AuthError: bad credentials for geheim@example.com")
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
    report.add("X", "FEHLGESCHLAGEN", "user=alice pass=hunter2")
    report.redact("alice", "hunter2")

    assert "alice" not in report.results[0].detail
    assert "hunter2" not in report.results[0].detail


def test_build_issue_url_contains_encoded_summary() -> None:
    from roombapy_prime.diagnostics import Report, build_issue_url

    report = Report()
    report.add("Login", "OK")
    report.add("Favoriten abrufen", "FEHLGESCHLAGEN", "HTTP 500")

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
    """NEU (21. Sitzung)."""
    from roombapy_prime.diagnostics import Report, _report_device_info
    from roombapy_prime.mqtt_client import ShadowResponse

    report = Report()
    state = ShadowResponse(topic="t", payload={"sku": "i7", "softwareVer": "3.2.1", "extraField": "x"})
    _report_device_info(report, state)

    assert len(report.results) == 1
    assert report.results[0].status == "OK"
    assert "'sku': 'i7'" in report.results[0].detail
    assert "softwareVer" in report.results[0].detail or "3.2.1" in report.results[0].detail
    assert "extraField" in report.results[0].detail  # Top-Level-Schluessel-Liste enthaelt auch Unbekanntes


def test_report_device_info_handles_no_state() -> None:
    from roombapy_prime.diagnostics import Report, _report_device_info

    report = Report()
    _report_device_info(report, None)

    assert len(report.results) == 0


def test_report_device_info_handles_no_known_candidates() -> None:
    from roombapy_prime.diagnostics import Report, _report_device_info
    from roombapy_prime.mqtt_client import ShadowResponse

    report = Report()
    state = ShadowResponse(topic="t", payload={"somethingElse": 1})
    _report_device_info(report, state)

    assert "keine der vermuteten Kandidaten-Felder" in report.results[0].detail
    assert "somethingElse" in report.results[0].detail


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


def test_shallow_summary_dict_shows_keys_and_types() -> None:
    from roombapy_prime.diagnostics import _shallow_summary

    result = _shallow_summary({"foo": "bar", "count": 5})
    assert result == {"foo": "str", "count": "int"}


def test_shallow_summary_list_shows_length_and_first_element() -> None:
    from roombapy_prime.diagnostics import _shallow_summary

    result = _shallow_summary([{"id": "a1"}, {"id": "a2"}])
    assert result == "Liste[2] erstes Element: {'id': '...'}"


def test_shallow_summary_empty_list() -> None:
    from roombapy_prime.diagnostics import _shallow_summary

    assert _shallow_summary([]) == "[] (leere Liste)"


def test_shallow_summary_never_leaks_actual_values() -> None:
    """Sicherheitsrelevant: darf nie den tatsaechlichen Wert zeigen,
    nur den Typ -- Schutz vor versehentlichem Leak sensibler Daten in
    einem geteilten Bericht."""
    from roombapy_prime.diagnostics import _shallow_summary

    result = _shallow_summary({"address": "123 Secret Street", "email": "user@example.com"})
    assert "123 Secret Street" not in str(result)
    assert "user@example.com" not in str(result)
