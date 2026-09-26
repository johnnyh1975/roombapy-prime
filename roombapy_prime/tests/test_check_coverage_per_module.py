"""scripts/check_coverage_per_module.py must be able to fail.

A guard that cannot go red checks nothing; ha_roomba_plus learned that
with two guards that skipped in CI and reported success. Each rule the
script enforces has a case here that breaks it.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _guard():
    spec = importlib.util.spec_from_file_location(
        "check_coverage_per_module", _SCRIPTS / "check_coverage_per_module.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _coverage_xml(tmp_path: Path, modules: dict[str, tuple[int, int]]) -> Path:
    """A minimal coverage.xml: `hit` of `total` lines covered per module."""
    classes = []
    for name, (hit, total) in modules.items():
        lines = "".join(
            f'<line number="{n}" hits="{1 if n <= hit else 0}"/>' for n in range(1, total + 1)
        )
        classes.append(f'<class filename="{name}"><lines>{lines}</lines></class>')
    path = tmp_path / "coverage.xml"
    path.write_text(
        f'<coverage><packages><package><classes>{"".join(classes)}</classes>'
        f"</package></packages></coverage>",
        encoding="utf-8",
    )
    return path


def _check(tmp_path, modules, floors):
    guard = _guard()
    return guard.check(guard.module_coverage(_coverage_xml(tmp_path, modules)), floors)


def test_a_module_at_95_passes_and_one_below_fails(tmp_path) -> None:
    _, failures, _ = _check(tmp_path, {"ok.py": (95, 100), "low.py": (94, 100)}, {})
    assert len(failures) == 1 and failures[0].startswith("low.py")


def test_an_exception_may_not_fall_below_its_floor(tmp_path) -> None:
    _, ok, _ = _check(tmp_path, {"old.py": (52, 100)}, {"old.py": 52.0})
    _, failing, _ = _check(tmp_path, {"old.py": (51, 100)}, {"old.py": 52.0})
    assert not ok
    assert failing and failing[0].startswith("old.py")


def test_it_counts_exactly_not_rounded(tmp_path) -> None:
    """94.96 % prints as 95 % in the terminal report and must still fail."""
    _, failures, _ = _check(tmp_path, {"close.py": (1899, 2000)}, {})
    assert failures


def test_a_floor_for_a_module_that_is_gone_fails(tmp_path) -> None:
    _, failures, _ = _check(tmp_path, {"here.py": (100, 100)}, {"gone.py": 50.0})
    assert failures and "gone.py" in failures[0]


def test_it_says_when_an_exception_should_be_raised_or_removed(tmp_path) -> None:
    _, _, hints = _check(
        tmp_path, {"better.py": (80, 100), "done.py": (96, 100)}, {"better.py": 70.0, "done.py": 70.0}
    )
    assert any("better.py" in h and "raise its floor" in h for h in hints)
    assert any("done.py" in h and "remove it" in h for h in hints)


def test_test_modules_are_not_counted(tmp_path) -> None:
    _, failures, _ = _check(tmp_path, {"tests/test_x.py": (0, 10), "x.py": (10, 10)}, {})
    assert not failures


def test_main_exits_non_zero_on_failure(tmp_path, capsys, monkeypatch) -> None:
    """With no floors at all, so the only reason to fail is the module
    below 95 % -- not an entry of the real floors file missing from this
    small report, which once made this test pass for the wrong reason."""
    guard = _guard()
    monkeypatch.setattr(guard, "load_floors", lambda: {})
    assert guard.main(["check", str(_coverage_xml(tmp_path, {"ok.py": (10, 10)}))]) == 0
    assert guard.main(["check", str(_coverage_xml(tmp_path, {"low.py": (10, 100)}))]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_the_committed_floors_are_all_below_95_and_rounded_to_a_tenth() -> None:
    """An entry at 95 % or above is an exception that is no longer one."""
    data = json.loads((_SCRIPTS / "coverage_floors.json").read_text(encoding="utf-8"))
    for name, floor in data["floors"].items():
        assert floor < 95.0, name
        assert round(floor, 1) == pytest.approx(floor), name
