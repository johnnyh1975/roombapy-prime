#!/usr/bin/env python3
"""What the vendor knows that this library does not use yet.

THE GUARD AND THIS REPORT ANSWER OPPOSITE QUESTIONS.

`check_vendor_value_sets.py` looks OUTWARD from the code: for every
value set we ship, does it match the vendor's? That catches drift and
it catches guessing. It cannot catch the larger problem, which is that
the extract holds 506 enums and we reference a few dozen -- everything
else is knowledge sitting unread in a file we already own.

Reading the research document did not solve that. It was read in full,
and two controls were still built from recall the same afternoon. The
difference between "we have the answer somewhere" and "the answer
reached the code" is the entire gap, and prose cannot close it.

SO EVERY VENDOR ENUM NEEDS A DISPOSITION, one of three:

  used         mapped in check_vendor_value_sets.CHECKED, or its values
               appear in the code
  not_applicable  triaged and rejected, WITH A REASON -- Picea device
               classes, Flutter internals, UI state machines
  unreviewed   nobody has looked

The third bucket is the report's output and the only one that matters.
It starts large and shrinks by deliberate decisions, each written down.
A finding that stays unreviewed is not a finding, however thoroughly it
was decompiled.

USAGE
    python scripts/vendor_gap_report.py            # summary + top items
    python scripts/vendor_gap_report.py --all      # every unreviewed one
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TRIAGE = ROOT / "docs" / "internal" / "vendor_enum_triage.json"
CLASS_TRIAGE = ROOT / "docs" / "internal" / "vendor_class_triage.json"


def _source_text() -> str:
    parts = []
    for path in (ROOT / "roombapy_prime").rglob("*.py"):
        if "/tests/" in str(path):
            continue
        parts.append(path.read_text(encoding="utf-8"))
    parts.append((ROOT / "scripts" / "check_vendor_value_sets.py").read_text())
    return "\n".join(parts)


#: Private Flutter/Dart framework classes, never iRobot's.
#:
#: Every remaining unreviewed enum after the last pass began with an
#: underscore -- `_ElementLifecycle`, `_GlowState`, `_HighlightType`,
#: `_AndroidViewState`, `_DecorationSlot`. Those are widget internals
#: from the UI toolkit the app is built with, and the extractor swept
#: them up alongside the vendor's own types.
#:
#: Skipped as a CLASS rather than triaged one by one, because a leading
#: underscore is the language's own marker for "not part of any public
#: surface". Anything iRobot declares is public by construction: this
#: library reaches it over a wire.
#:
#: The count they were inflating mattered. "Twenty unreviewed" reads
#: like twenty decisions outstanding; twenty Flutter widget states read
#: like none.
def _is_framework_private(name: str) -> bool:
    return name.startswith("_")


def _triage(path: Path = TRIAGE) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _class_gaps(source: str) -> tuple[list[str], list[str], list[str]]:
    """Serialiser classes and SDK models, split by disposition.

    ADDED AFTER A MEASUREMENT NOBODY HAD TAKEN. This report checked
    enums and nothing else, so it reported shrinking numbers while wire
    key coverage sat at 62% and SDK model coverage at 60% -- unseen,
    because nothing looked.

    A class counts as used when MOST of its keys appear in the source.
    Not all: `MissionHistoryItemResponse` has 33 keys of which 28 are
    read, and calling that unused would bury it under classes nobody
    has touched at all.
    """
    from roombapy_prime.vendor_reference import _data  # noqa: PLC0415

    data = _data()
    triage = _triage(CLASS_TRIAGE)
    groups = dict(data.get("serializers", {}))
    groups.update(data.get("sdk_models", {}))
    used, triaged, unreviewed = [], [], []
    for name, keys in sorted(groups.items()):
        if name in triage:
            triaged.append(name)
            continue
        present = sum(1 for k in keys if f'"{k}"' in source)
        if present * 2 >= len(keys):
            used.append(name)
        else:
            unreviewed.append(f"{name} ({present}/{len(keys)})")
    return used, triaged, unreviewed


def _referenced(name: str, source: str, values: dict) -> bool:
    """Whether this vendor enum reaches the code at all.

    Two ways count: the enum NAME appears (a declared mapping or a
    docstring citing it), or its member names appear as string values.

    DELIBERATELY GENEROUS. A false "used" only hides an enum from the
    report; a false "unreviewed" wastes a reader's attention on
    something already handled, and a report that cries wolf gets
    ignored -- which is how the extract ended up unread in the first
    place.
    """
    bare = name.split(".")[-1]
    if re.search(rf"\b{re.escape(bare)}\b", source):
        return True
    members = [m for m in values if isinstance(m, str) and len(m) > 3]
    if not members:
        return False
    hits = sum(1 for m in members if f'"{m}"' in source)
    return hits >= max(2, len(members) // 2)


#: Name fragments that mark an enum as belonging to something other
#: than the robot protocol.
#:
#: NOT A FILTER ON THE REPORT. Matching one of these does not exclude an
#: enum -- it only sorts it lower, because a heuristic that silently
#: hides things is the failure mode this whole file exists to prevent.
#: A human still decides, and the decision is recorded.
_LIKELY_IRRELEVANT = (
    "Animation", "AppLifecycle", "Widget", "Scroll", "Gesture", "Paint",
    "Render", "Text", "Font", "Locale", "Theme", "Keyboard", "Focus",
    "Navigator", "Route", "Overlay", "Tween", "Curve", "Alignment",
    "BoxFit", "Axis", "Brightness", "Clip", "Flex", "Stack", "Toast",
    "Dialog", "Sheet", "Tab", "Pane", "Banner", "Badge", "Chip",
)


def main() -> int:
    show_all = "--all" in sys.argv
    from roombapy_prime.vendor_reference import _data  # noqa: PLC0415

    enums = _data()["enums"]
    source = _source_text()
    triage = _triage()

    used, triaged, unreviewed = [], [], []
    for name, values in sorted(enums.items()):
        if _is_framework_private(name):
            continue
        if name in triage:
            triaged.append(name)
        elif _referenced(name, source, values):
            used.append(name)
        else:
            unreviewed.append(name)

    def rank(name: str) -> tuple[int, str]:
        return (1 if any(f in name for f in _LIKELY_IRRELEVANT) else 0, name)

    unreviewed.sort(key=rank)

    c_used, c_triaged, c_unreviewed = _class_gaps(source)
    print(
        f"Vendor enums: {len(enums)}   "
        f"used {len(used)}   triaged {len(triaged)}   "
        f"UNREVIEWED {len(unreviewed)}"
    )
    print(
        f"Classes/models: {len(c_used) + len(c_triaged) + len(c_unreviewed)}   "
        f"used {len(c_used)}   triaged {len(c_triaged)}   "
        f"UNREVIEWED {len(c_unreviewed)}"
    )
    if c_unreviewed:
        print("\nUnreviewed classes and models:\n")
        for name in (c_unreviewed if show_all else c_unreviewed[:25]):
            print(f"  {name}")
        if not show_all and len(c_unreviewed) > 25:
            print(f"  ... {len(c_unreviewed) - 25} more, use --all")
        print(
            "\nRecord each in docs/internal/vendor_class_triage.json. "
            "Most of the remainder is message centre, surveys and "
            "entitlements -- app account features rather than robot "
            "protocol, which is a scope boundary and belongs in writing."
        )
    if not unreviewed:
        print("\nEvery vendor enum has a disposition.")
        return 0

    print("\nUnreviewed, most likely protocol-relevant first:\n")
    shown = unreviewed if show_all else unreviewed[:40]
    for name in shown:
        preview = list(enums[name].items())[:5]
        rendered = ", ".join(f"{k}={v}" for k, v in preview)
        more = "" if len(enums[name]) <= 5 else f" (+{len(enums[name]) - 5})"
        print(f"  {name}\n      {rendered}{more}")
    if not show_all and len(unreviewed) > 40:
        print(f"\n  ... {len(unreviewed) - 40} more, use --all")
    print(
        "\nRecord each decision in docs/internal/vendor_enum_triage.json "
        "as name -> reason. 'Not relevant' is a valid reason; leaving it "
        "unreviewed is not the same thing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
