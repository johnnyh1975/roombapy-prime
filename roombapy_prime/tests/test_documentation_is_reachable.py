"""Prose written as documentation must actually be documentation.

WHY THIS EXISTS. `send_simple_command()` carried two triple-quoted blocks
in a row. The first is the docstring; the second was a bare string
expression -- syntactically legal, evaluated and discarded. It held the
whole evidence trail for the corrected mission-control path, including
the independent third-party corroboration, and none of it was in
`__doc__`. `help()` did not show it, and no documentation tool could.

This is the prose form of the dead-code rule this project already
enforces: a private name appearing exactly once in the source is a
definition nothing reaches. Here the definition is a paragraph, and the
cost is worse than dead code -- dead code is merely unused, while
unreachable documentation looks maintained while being invisible.

NOT FLAGGED: attribute docstrings. A string directly after an assignment
is the PEP 258 convention, which Sphinx and other tools do read --
`_REFRESH_RETRY_SECONDS` in prime_robot.py is a legitimate example. The
first version of this check flagged it, which would have meant
"fixing" correct code. The distinction is whether an assignment precedes
the string.
"""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _orphan_prose(path: Path) -> list[tuple[str, int]]:
    """String expressions that are neither a docstring nor documenting
    the assignment above them."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        for index, statement in enumerate(body):
            if index == 0:
                # The docstring position.
                continue
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            previous = body[index - 1]
            if isinstance(previous, (ast.Assign, ast.AnnAssign)):
                # PEP 258 attribute docstring -- read by real tools.
                continue
            found.append((getattr(node, "name", "<module>"), statement.lineno))
    return found


def test_no_unreachable_prose_in_the_package() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for owner, line in _orphan_prose(path):
            offenders.append(
                f"{path.relative_to(PACKAGE_ROOT)}:{line} in {owner}()"
            )

    assert not offenders, (
        "Prose written as documentation but unreachable -- a second "
        "triple-quoted block after the docstring is a discarded "
        "expression, not documentation. Merge it into the docstring "
        "above:\n  " + "\n  ".join(offenders)
    )


def test_the_check_still_recognises_the_real_thing() -> None:
    """Negative control. Without it this test passes on an empty
    package, on a broken parser, and on a rule that accidentally
    excludes everything -- which is exactly what the first version did
    by treating every string after an assignment as legitimate."""
    source = '''
def f():
    """Real docstring."""
    """Orphan prose."""
    return 1

class C:
    X = 1
    """Attribute docstring, legitimate."""
'''
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(source)
        temporary = Path(handle.name)

    try:
        found = _orphan_prose(temporary)
        assert [owner for owner, _ in found] == ["f"], found
    finally:
        temporary.unlink()
