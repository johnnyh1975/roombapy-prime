"""Regenerate roombapy_prime/vendor_errors_classic.json.

    python scripts/generate_vendor_errors_classic.py <extraction.json> [--check]

<extraction.json> is the extraction from the Classic iRobot Home app
(`com.irobot.home` 7.18.0, versionCode 1607180002): its strings come
from `base.apk:resources.arsc`, its code-to-key mapping from
`RobotStatusResolver` (Java) and `libcore_jni.so` (StatusMessageUtility,
DockStatusProvider). It is not part of this repository; it is the
research artefact the table was cut from.

WHAT IS KEPT. The texts the app shows a user for a code, per language:
  - robot:  `title` and `content` (the status card), `history` (the
            mission history), `history_feet` (74 in imperial units),
            `content_unlinked` (a `notification_error_<N>` text whose
            display path was not found in the app -- 1000/1001 side
            brush among them).
  - sku_overrides: per code and SKU prefix, the fields the app replaces
            for that robot family.
  - clean_base_overrides: 36 with a Clean Base.
  - not_ready: the start refusals, by readiness number.
  - dock:   `title`, `content`, `history`, `history_complete`.

WHAT IS DROPPED. Push texts: the server chooses those, not the code
(`PushNotificationUtils` resolves a key name from the payload), so a
code-to-push mapping would be a guess. Fields that are null in every
language. The research notes (`_meta`), except where the texts came
from.

Values are copied unchanged -- placeholders (`%s`, `%1$s`, `%2$s`),
no-break spaces and all. vendor_errors_classic.py normalises them on the
way out, as vendor_errors.py does for the Prime table.

--check exits 1 when the JSON differs from what the extraction gives,
without writing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

LANGUAGES = ("de", "en", "es", "fr", "it", "nl", "pl", "pt")
TARGET = Path(__file__).resolve().parent.parent / "roombapy_prime" / "vendor_errors_classic.json"

FIELDS = {
    "robot": ("title", "content", "history", "history_feet", "content_unlinked"),
    "sku_overrides": ("title", "content", "history"),
    "clean_base_overrides": ("title", "content"),
    "not_ready": ("title", "content", "history"),
    "dock": ("title", "content", "history", "history_complete"),
}
SOURCE_SECTION = {"clean_base_overrides": "dock_type_overrides"}

#: The status card's title for these is one line shared by a whole
#: group -- "Error", "Charging error", "Unable to start" -- not a name
#: for the code. A caller that wants a label reaches for `history`.
GENERIC_TITLE_KEYS = ("status_text_error", "status_text_charging_error", "status_text_start_refused")


def _texts(entry: dict[str, Any], fields: tuple[str, ...]) -> dict[str, dict[str, str]]:
    kept = {
        lang: {f: entry[lang][f] for f in fields if entry.get(lang, {}).get(f) is not None}
        for lang in LANGUAGES
    }
    if any(set(kept[lang]) != set(kept["en"]) for lang in LANGUAGES):
        raise ValueError(f"fields differ between languages: {kept}")
    return kept


def _section(name: str, data: dict[str, Any]) -> dict[str, Any]:
    source = data[SOURCE_SECTION.get(name, name)]
    out: dict[str, Any] = {}
    for code in sorted(source, key=int):
        entry = source[code]
        if name in ("sku_overrides", "clean_base_overrides"):
            out[code] = {variant: _texts(entry[variant], FIELDS[name]) for variant in sorted(entry)}
        else:
            texts = _texts(entry, FIELDS[name])
            if texts["en"]:
                out[code] = texts
    return out


def build(data: dict[str, Any]) -> dict[str, Any]:
    meta = data["_meta"]
    table: dict[str, Any] = {
        "_source": {
            "app": meta["source"],
            "path": meta["path"],
            "locale_choice": meta["locale_choice"],
            "extracted": meta["extracted"],
            "sku_selection": meta["sku_selection"],
        },
        "generic_title_codes": sorted(
            (
                int(code)
                for code, keys in meta["key_map"].items()
                if keys.get("title") in GENERIC_TITLE_KEYS
            )
        ),
    }
    for name in FIELDS:
        table[name] = _section(name, data)
    return table


def render(table: dict[str, Any]) -> str:
    return json.dumps(table, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    data = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    new = render(build(data))
    counts = ", ".join(f"{name} {len(json.loads(new)[name])}" for name in FIELDS)
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != new:
            print(f"{TARGET.name} differs from the extraction ({counts}).")
            return 1
        print(f"OK: {TARGET.name} matches the extraction ({counts}).")
        return 0
    TARGET.write_text(new, encoding="utf-8")
    print(f"Wrote {TARGET.name}: {counts}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
