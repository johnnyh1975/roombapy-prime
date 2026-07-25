"""Enforces the one-way dependency between the library core and the
diagnostic tooling.

The direction was already respected before tools/ existed, purely by
discipline -- this makes it structural. It matters beyond tidiness:
tools/ registers console scripts, several of which MOVE A REAL ROBOT.
Keeping the core free of any dependency on them is what makes it
possible to ship the library alone (to Home Assistant installations via
ha_roomba_plus) without those commands coming along.

A failure here means someone reached from the library into the tooling
-- which would silently make the tooling non-optional.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PACKAGE = Path(__file__).resolve().parent.parent
_CORE_FILES = sorted(
    p for p in _PACKAGE.rglob("*.py")
    if "tests" not in p.relative_to(_PACKAGE).parts
)


def _imported_modules(path: Path) -> set[str]:
    """Every module name this file imports, absolute or relative,
    including imports nested inside functions (this project uses those
    deliberately, to avoid circular imports at module load)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(("." * (node.level or 0)) + (node.module or ""))
    return names


def test_core_files_were_actually_found():
    """Guards the guard: if the layout changes and this collects
    nothing, every assertion below would pass vacuously."""
    assert len(_CORE_FILES) >= 5
    assert any(p.name == "prime_robot.py" for p in _CORE_FILES)


@pytest.mark.parametrize("path", _CORE_FILES, ids=lambda p: p.name)
def test_core_module_does_not_import_tools(path: Path):
    offending = [
        name for name in _imported_modules(path)
        if name.split(".")[0] == "roombapy_prime_tools"
    ]
    assert not offending, (
        f"{path.name} imports {offending} from the tooling package. The core must "
        "never depend on tools/ -- see tools/__init__.py for why."
    )


def test_package_init_exports_nothing_from_tools():
    """A re-export would make tools reachable through the public API
    even without a direct import."""
    init = (_PACKAGE / "__init__.py").read_text(encoding="utf-8")

    assert "roombapy_prime_tools" not in init, "__init__.py must not reference the tooling package"
