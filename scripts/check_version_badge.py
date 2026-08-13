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
INIT_PATH = REPO_ROOT / "roombapy_prime" / "__init__.py"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"


def changelog_has_entry(version: str) -> bool:
    """Whether CHANGELOG.md documents this version.

    THREE RELEASES SHIPPED WITHOUT ONE. b1, b2 and b3 each had release
    notes and no changelog entry, and nothing noticed, because nothing
    looked -- the file simply stopped at 0.2.0b16 while the version
    climbed past it.

    Release notes and a changelog answer different questions. The notes
    say what happened in one release; the changelog is where somebody
    two versions behind finds out what breaks on the way up. A gap in it
    is invisible until the moment it is needed.
    """
    return f"## [{version}]" in CHANGELOG_PATH.read_text(encoding="utf-8")


def get_dunder_version() -> str | None:
    """`__version__` from the package, or None if it is not declared.

    THE PACKAGE DECLARES ITS VERSION TWICE and nothing compared them.
    PR #62 arrived with `pyproject.toml` saying 0.3.0b5 and
    `__init__.py` saying 0.3.0b4, and this script passed it: it checked
    the README badge against pyproject and never looked at the module.

    A consumer reading `roombapy_prime.__version__` and a consumer
    reading the installed distribution's metadata would have disagreed
    about which release they were on -- the kind of mismatch that makes
    a field report unusable, because "which version are you running"
    stops having one answer.
    """
    match = re.search(
        r'^__version__\s*=\s*"([^"]+)"', INIT_PATH.read_text(encoding="utf-8"), re.M
    )
    return match.group(1) if match else None


def get_pyproject_version() -> str:
    with open(PYPROJECT_PATH, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def pep440_alpha_to_readme_style(version: str) -> str:
    """"0.1.11a0" -> "v0.1.11-alpha", "0.2.0b1" -> "v0.2.0-beta".

    BETA ADDED 30 July 2026, when 0.2.0b1 made this function raise
    exactly as its own docstring predicted. Worth noting that the guard
    worked: the version bump could not quietly ship a README badge that
    still said alpha.

    Release candidates and stable versions are still unmapped, and still
    raise. That stays deliberate -- each scheme change is a moment to
    confirm the badge means what it says, not to widen a regex until
    nothing fails.
    """
    match = re.match(r"^(\d+\.\d+\.\d+)a\d+$", version)
    if match:
        return f"v{match.group(1)}-alpha"
    match = re.match(r"^(\d+\.\d+\.\d+)b\d+$", version)
    if match:
        return f"v{match.group(1)}-beta"
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
    """Counts the core suite's tests via pytest's own collection.

    NOTE ON ERROR REPORTING (this session): this used check=True, so a
    collection failure surfaced as a bare CalledProcessError traceback
    with pytest's actual output thrown away. In CI that produced a
    failure that said only "returned non-zero exit status 2" -- the one
    line that explains nothing -- while pytest had printed the real
    reason and had it discarded.

    Exit status 2 from collection almost always means a test module
    could not be imported. On this repository the most likely cause is
    stale files: extracting a release over an existing checkout leaves
    deleted modules behind, and old test files that import modules
    which have since moved will fail exactly this way."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "roombapy_prime/tests/", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"pytest could not collect the test suite (exit {result.returncode}).\n")
        print("--- pytest stdout ---")
        print(result.stdout.strip() or "(empty)")
        if result.stderr.strip():
            print("--- pytest stderr ---")
            print(result.stderr.strip())
        print(
            "\nIf this mentions an import error for a module that no longer exists, "
            "the checkout probably has stale files -- extracting a release over an "
            "existing tree does not remove files that were deleted upstream."
        )
        sys.exit(1)

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

    if not changelog_has_entry(pyproject_version):
        problems.append(
            f"CHANGELOG.md has no '## [{pyproject_version}]' entry. Three "
            "releases already shipped without one because nothing checked."
        )

    dunder_version = get_dunder_version()
    if dunder_version is None:
        problems.append(
            "roombapy_prime/__init__.py declares no __version__. It is part of "
            "the package's public surface and must not silently disappear."
        )
    elif dunder_version != pyproject_version:
        problems.append(
            f"roombapy_prime.__version__ is {dunder_version!r} but "
            f"pyproject.toml says {pyproject_version!r}. The package declares "
            "its version twice and both must agree -- otherwise 'which version "
            "are you running' has two answers."
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
