#!/usr/bin/env python3
"""Verifies that the tools distribution pins exactly the core version
it ships alongside.

THE ONE REAL RISK OF SPLITTING INTO TWO DISTRIBUTIONS. The tools reach
deep into the library -- shadow models, wire-format helpers, report
plumbing -- so a mismatched pair does not fail cleanly. It fails as a
confusing AttributeError or a silently wrong payload, in a field
tester's terminal, on someone else's robot.

Everything else about the split is cheap. This is the part that needs
a machine watching it, because "bump both" is exactly the kind of
two-step a human does correctly right up until the one release they
don't.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "pyproject.toml"
_TOOLS = _ROOT / "tools" / "pyproject.toml"


def _core_version() -> str:
    match = re.search(r'^version = "([^"]+)"', _CORE.read_text(encoding="utf-8"), re.M)
    if not match:
        sys.exit(f"Could not find a version in {_CORE}")
    return match.group(1)


def _tools_version_and_pin(text: str) -> tuple[str, str]:
    version = re.search(r'^version = "([^"]+)"', text, re.M)
    pin = re.search(r"roombapy-prime\.git@v([0-9a-z.]+)", text)
    if not version or not pin:
        sys.exit(f"Could not find a version and a core pin in {_TOOLS}")
    return version.group(1), pin.group(1)


def main() -> None:
    core = _core_version()
    tools_text = _TOOLS.read_text(encoding="utf-8")
    tools_version, pinned = _tools_version_and_pin(tools_text)

    problems = []
    if pinned != core:
        problems.append(
            f"  - tools pins core v{pinned}, but the core in this repo is {core}. "
            "A tester installing the tools would get a DIFFERENT core than the one "
            "these tools were tested against."
        )
    if tools_version != core:
        problems.append(
            f"  - tools version is {tools_version}, core is {core}. They are released "
            "together from one repo; diverging numbers make it impossible to tell "
            "which pair someone is running from a bug report alone."
        )

    if problems:
        print("Version pin check FAILED:")
        print("\n".join(problems))
        sys.exit(1)

    print(f"OK: core, tools and the tools' core pin all say {core}.")


if __name__ == "__main__":
    main()
