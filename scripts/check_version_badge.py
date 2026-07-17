#!/usr/bin/env python3
"""Checks README.md's stated version and test count against the actual
project state, adapted from the same-purpose script in ha_roomba_plus's
own CI pipeline ("version-badge hung two releases behind 3.4.0, unnoticed").

Two things checked here, both found stale by hand (not by CI) multiple
times over the course of this project's development, which is exactly
the motivation for automating it:

1. README's "Status: vX.Y.Z-alpha" badge against pyproject.toml's own
   version string.
2. README's "N+ unit tests"/"N+ tests, all passing" mentions against
   the actual number of tests pytest collects right now.

Exit code 1 (CI failure) if either is stale; exit 0 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def get_pyproject_version() -> str:
    with open(PYPROJECT_PATH, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def pep440_alpha_to_readme_style(version: str) -> str:
    """"0.1.11a0" -> "v0.1.11-alpha". This project has, so far, only ever
    used plain alpha pre-releases (aN) -- if a beta/rc/stable version
    ever appears, this mapping needs extending, and this function
    raising is the right failure mode (loud, not a silent false pass)."""
    match = re.match(r"^(\d+\.\d+\.\d+)a\d+$", version)
    if match:
        return f"v{match.group(1)}-alpha"
    raise ValueError(
        f"pyproject.toml version {version!r} isn't a plain alpha "
        f"pre-release (expected X.Y.ZaN) -- this script's version-string "
        f"mapping needs to be extended for the new scheme before it can "
        f"check anything meaningful."
    )


def get_readme_version_badge() -> str:
    text = README_PATH.read_text(encoding="utf-8")
    match = re.search(r"Status:\s*(v\d+\.\d+\.\d+-\w+)\.", text)
    if not match:
        raise ValueError("Could not find a 'Status: vX.Y.Z-suffix.' badge in README.md")
    return match.group(1)


def get_actual_test_count() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "roombapy_prime/tests/", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    if not match:
        raise ValueError(f"Could not parse test count from pytest --collect-only output:\n{result.stdout}")
    return int(match.group(1))


def get_readme_test_count_mentions() -> list[int]:
    text = README_PATH.read_text(encoding="utf-8")
    return [int(n) for n in re.findall(r"(\d+)\+ (?:unit )?tests?", text)]


def main() -> int:
    problems = []

    pyproject_version = get_pyproject_version()
    expected_badge = pep440_alpha_to_readme_style(pyproject_version)
    actual_badge = get_readme_version_badge()
    if actual_badge != expected_badge:
        problems.append(
            f"README's version badge says {actual_badge!r}, but pyproject.toml's "
            f"version ({pyproject_version!r}) implies it should say {expected_badge!r}."
        )

    actual_test_count = get_actual_test_count()
    readme_mentions = get_readme_test_count_mentions()
    if not readme_mentions:
        problems.append("Could not find any 'N+ tests' mention in README.md to check.")
    else:
        # "339+" is correct as long as it's <= the real count and not
        # drifted far below it -- this project's convention is "N+" as a
        # rounded-down floor, not an exact live count, so allow the
        # mentioned number to be up to 20 less than actual before flagging
        # it as meaningfully stale (an exact-match requirement would fail
        # on every single test added without a README touch-up, which is
        # noisier than useful).
        for mentioned in set(readme_mentions):
            if mentioned > actual_test_count:
                problems.append(
                    f"README claims '{mentioned}+ tests', but only {actual_test_count} "
                    f"actually exist -- this is impossible and must be fixed."
                )
            elif actual_test_count - mentioned > 20:
                problems.append(
                    f"README claims '{mentioned}+ tests', but {actual_test_count} actually "
                    f"exist now -- more than 20 tests have been added since README was last "
                    f"updated, worth refreshing the number."
                )

    if problems:
        print("README staleness check FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"README version badge ({actual_badge}) and test count mentions are consistent "
          f"with pyproject.toml ({pyproject_version}) and the actual test suite ({actual_test_count} tests).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
