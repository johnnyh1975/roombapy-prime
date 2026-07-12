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
