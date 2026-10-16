"""--output and --dump-config are written after a successful run.

FROM 0.1.11a25 TO 0.6.0 THEY NEVER WERE. Four scripts read

    report, raw_capture = sys.exit(run_script(run(...)))

sys.exit() never returns, so the report file, the redacted capture and
the issue link after it never happened -- on every successful run, with
no error. The gate tests did not notice: their stand-in handed the pair
to sys.exit(), which raised the SystemExit they were waiting for.

So this checks the files themselves, for every script that has them.
"""

from __future__ import annotations

import importlib
import json
from unittest.mock import patch

import pytest

from roombapy_prime.diagnostics import Report

# (module, extra argv to get past its gates, whether confirm() is asked)
_SCRIPTS = [
    ("verify_named_shadows", [], False),
    ("verify_mission_commands", ["--i-understand-this-will-move-my-robot"], True),
    ("verify_map_edit", ["--i-understand-this-will-edit-my-map"], True),
    ("verify_map_edit", ["--i-understand-this-will-edit-my-map", "--test-category"], True),
    ("verify_mission_timeline", ["--duration", "1"], False),
]


def _runner(seen: list):
    def run(coro):
        seen.append(coro)
        coro.close()
        report = Report()
        report.add("Login", "OK", "as user@example.com with hunter2")
        return 0, (report, {"shadow": {"password": "hunter2", "batPct": 100}})
    return run


@pytest.mark.parametrize("name,extra,asks", _SCRIPTS, ids=[" ".join([s[0], *s[1][1:]]) for s in _SCRIPTS])
def test_a_successful_run_writes_both_files_redacted(name, extra, asks, tmp_path, capsys):
    module = importlib.import_module(f"roombapy_prime_tools.{name}")
    report_path, dump_path = tmp_path / "report.md", tmp_path / "capture.json"
    seen: list = []
    argv = [name, "--blid", "B", *extra, "--output", str(report_path),
            "--dump-config", str(dump_path)]

    with patch("sys.argv", argv), \
         patch.object(module, "resolve_credentials", return_value=("user@example.com", "hunter2")), \
         patch.object(module, "confirm", return_value=True, create=not asks) as confirmed, \
         patch.object(module, "run_and_keep", side_effect=_runner(seen)):
        module.main()

    assert len(seen) == 1 and confirmed.called == asks
    assert "Login" in report_path.read_text(encoding="utf-8")
    assert "hunter2" not in report_path.read_text(encoding="utf-8")
    dump = dump_path.read_text(encoding="utf-8")
    assert "hunter2" not in dump and json.loads(dump)
    assert "Feedback for the maintainers" in capsys.readouterr().out


@pytest.mark.parametrize("name,extra,asks", _SCRIPTS[:2], ids=[s[0] for s in _SCRIPTS[:2]])
def test_a_failed_run_exits_with_its_code_and_writes_nothing(name, extra, asks, tmp_path):
    module = importlib.import_module(f"roombapy_prime_tools.{name}")
    report_path = tmp_path / "report.md"

    def failed(coro):
        coro.close()
        return 1, None

    with patch("sys.argv", [name, "--blid", "B", *extra, "--output", str(report_path)]), \
         patch.object(module, "resolve_credentials", return_value=("u", "p")), \
         patch.object(module, "confirm", return_value=True, create=not asks), \
         patch.object(module, "run_and_keep", side_effect=failed), \
         pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 1
    assert not report_path.exists()
