#!/usr/bin/env python3
"""Line coverage per module: 95 %, or at least what a named exception had.

WHY PER MODULE. A total hides where the gaps are: 96 % overall can be
four modules at 100 % carrying one at 50. ha_roomba_plus gates every
module at 95 % since its 4.2.11, and the rule there found untested
paths nobody had looked for.

WHY EXCEPTIONS INSTEAD OF 95 % FOR ALL. When this was added (0.4.0) nine
modules were below 95 % -- diagnostics.py at 52 %. A gate that fails on
the day it is switched on gets switched off again. So each of them is
listed in coverage_floors.json with the coverage it had, rounded DOWN to
a tenth, and may not fall below it. Every module not listed must reach
95 %, which includes every module added from now on.

The list is meant to shrink. This script says when an exception has
risen a full point above its floor (raise the floor) or reached 95 %
(remove the entry), and fails when an entry names a module that no
longer exists.

COUNTED EXACTLY, from coverage.xml, never from the rounded terminal
report: that prints 94.96 % as 95 %, and in ha_roomba_plus once let
three short modules through.

Usage: python scripts/check_coverage_per_module.py coverage.xml
Exit 0 = every module at or above its floor. Exit 1 otherwise.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FLOORS_PATH = Path(__file__).with_name("coverage_floors.json")
DEFAULT_FLOOR = 95.0


def module_coverage(xml_path: Path) -> dict[str, tuple[int, int]]:
    """Covered and total statement lines per module, keyed by the path
    coverage.xml gives (relative to the package, e.g. "models/livemap.py").
    Test modules are left out."""
    counts: dict[str, tuple[int, int]] = {}
    for cls in ET.parse(xml_path).getroot().iter("class"):
        name = cls.get("filename") or ""
        if name.startswith("tests/") or "/tests/" in name:
            continue
        lines = cls.find("lines")
        entries = list(lines) if lines is not None else []
        hit = sum(1 for line in entries if int(line.get("hits", "0")) > 0)
        prior_hit, prior_total = counts.get(name, (0, 0))
        counts[name] = (prior_hit + hit, prior_total + len(entries))
    return counts


def check(
    counts: dict[str, tuple[int, int]], floors: dict[str, float]
) -> tuple[list[str], list[str], list[str]]:
    """(report lines, failures, hints)."""
    report: list[str] = []
    failures: list[str] = []
    hints: list[str] = []
    for name, (hit, total) in sorted(counts.items()):
        percent = 100.0 if total == 0 else 100.0 * hit / total
        floor = floors.get(name, DEFAULT_FLOOR)
        marker = "exception" if name in floors else ""
        report.append(f"{percent:7.2f} %  (floor {floor:5.1f})  {name}  {marker}".rstrip())
        if percent < floor:
            failures.append(
                f"{name}: {percent:.2f} % is below its floor of {floor:.1f} % "
                f"({total - hit} of {total} lines uncovered)"
            )
        elif name in floors and percent >= DEFAULT_FLOOR:
            hints.append(f"{name} reached {percent:.2f} % -- remove it from coverage_floors.json")
        elif name in floors and percent - floor >= 1.0:
            hints.append(f"{name} is at {percent:.2f} % -- raise its floor from {floor:.1f}")
    for name in sorted(set(floors) - set(counts)):
        failures.append(f"{name}: listed in coverage_floors.json but not in the report -- remove it")
    return report, failures, hints


def load_floors(path: Path = FLOORS_PATH) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {name: float(value) for name, value in data["floors"].items()}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_coverage_per_module.py coverage.xml")
        return 2
    report, failures, hints = check(module_coverage(Path(argv[1])), load_floors())
    print("\n".join(report))
    for hint in hints:
        print(f"hint: {hint}")
    if failures:
        print("\nCoverage per module FAILED:")
        print("\n".join(f"  - {failure}" for failure in failures))
        return 1
    print(f"\nOK: every module at or above its floor ({len(report)} modules).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
