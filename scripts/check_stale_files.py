#!/usr/bin/env python3
"""Detects files left behind from a previous version's layout.

WHY THIS EXISTS: a23 moved every diagnostic script out of
roombapy_prime/ into its own top-level package. Extracting a release
archive over an existing checkout does NOT remove files that were
deleted upstream -- so an a22 tree updated that way keeps both layouts
at once, and the old test files then fail to import modules that have
moved. In CI that surfaced only as "returned non-zero exit status 2".

Cheap to run, unambiguous when it fires, and names the exact paths to
delete. Belongs in CI ahead of the test run, where it turns a confusing
failure into a one-line instruction.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Paths that existed in a22 and must not exist in a23 or later.
_REMOVED_IN_A23 = [
    ("roombapy_prime/tools", "the diagnostic scripts moved to the top-level tools/ package"),
    ("roombapy_prime/tests/test_tools_cli.py", "moved to tools/tests/"),
]
_REMOVED_TEST_GLOBS = [
    ("roombapy_prime/tests/test_verify_*.py", "the verify-script tests moved to tools/tests/"),
]


def main() -> int:
    problems: list[str] = []

    for relative, why in _REMOVED_IN_A23:
        path = _ROOT / relative
        if path.exists():
            problems.append(f"  {relative}  --  {why}")

    for pattern, why in _REMOVED_TEST_GLOBS:
        parent, _, glob = pattern.rpartition("/")
        for path in sorted((_ROOT / parent).glob(glob)):
            problems.append(f"  {path.relative_to(_ROOT)}  --  {why}")

    if not problems:
        print("OK: no files from a previous layout are left in this checkout.")
        return 0

    print("Stale files from a previous version are still present:\n")
    print("\n".join(problems))
    print(
        "\nThese are left over from extracting a release over an existing tree, which does "
        "not delete files removed upstream. They will break test collection with an import "
        "error for a module that has since moved.\n\nDelete them and re-run."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
