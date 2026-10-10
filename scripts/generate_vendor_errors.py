"""Regenerate VENDOR_ERROR_TEXTS in roombapy_prime/vendor_errors.py.

    python scripts/generate_vendor_errors.py <locale_dir> [--check]

<locale_dir> is the app's `flutter_assets/packages/module_locale/assets/
common/` directory: one JSON file per locale, named `<locale>.odt`
despite being JSON. The table takes `deviceFault_code<N>_title` and
`_content` for every numeric N found in any of the eight locales below.

WHY A SCRIPT. The module said "regenerate rather than patch" from the
start, and nothing in the repository could. The 0.6.0 refresh to app
3.2.0 was the first regeneration; this is the tool it used, and running
it against 3.0.0's locale files reproduces the 0.5.0 table byte for
byte -- that is how the tool was checked before it was trusted with
3.2.0.

WHAT IT KEEPS AS IT WAS.
  - The eight locales and their variants: es_ES, fr_FR, pt_PT and en_US,
    not es_419, fr_CA, pt_BR or en_GB. 0.5.0's table matches those files
    in every one of its 224 strings per locale and no other variant.
  - Numeric codes only. The packs also carry `deviceFault_codeN1_*`
    (an app-internal "undefined" fault); robots report integers.
  - A missing title or content becomes "" -- 286 has no content in any
    locale, in 3.0.0 and 3.2.0 alike.
  - pprint at width 100, the layout the table has always had, so a
    regeneration diffs as text changes and nothing else.

--check exits 1 when the module differs from what the locale files give,
without writing.
"""

from __future__ import annotations

import json
import pprint
import re
import sys
from pathlib import Path

LOCALES = {
    "de": "de_DE",
    "en": "en_US",
    "es": "es_ES",
    "fr": "fr_FR",
    "it": "it_IT",
    "nl": "nl_NL",
    "pl": "pl_PL",
    "pt": "pt_PT",
}

MODULE = Path(__file__).resolve().parent.parent / "roombapy_prime" / "vendor_errors.py"
PREFIX = "VENDOR_ERROR_TEXTS: Final[dict[int, dict[str, dict[str, str]]]] = "
END = "\n\n\ndef vendor_error"
KEY = re.compile(r"^deviceFault_code(\d+)_(title|content)$")


def build_table(locale_dir: Path) -> dict[int, dict[str, dict[str, str]]]:
    """code -> locale -> {"content", "title"} from the app's locale files."""
    packs = {
        short: json.loads((locale_dir / f"{name}.odt").read_text(encoding="utf-8"))
        for short, name in LOCALES.items()
    }
    codes = sorted(
        {int(m.group(1)) for pack in packs.values() for key in pack if (m := KEY.match(key))}
    )
    return {
        code: {
            short: {
                "content": pack.get(f"deviceFault_code{code}_content") or "",
                "title": pack.get(f"deviceFault_code{code}_title") or "",
            }
            for short, pack in packs.items()
        }
        for code in codes
    }


def render(source: str, table: dict[int, dict[str, dict[str, str]]]) -> str:
    """The module's source with the table replaced."""
    start = source.index(PREFIX)
    end = source.index(END, start)
    return source[:start] + PREFIX + pprint.pformat(table, width=100) + source[end:]


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    table = build_table(Path(args[0]))
    source = MODULE.read_text(encoding="utf-8")
    new = render(source, table)
    if "--check" in argv:
        if new != source:
            print(f"{MODULE.name} differs from the locale files ({len(table)} codes there).")
            return 1
        print(f"OK: {MODULE.name} matches the locale files ({len(table)} codes).")
        return 0
    MODULE.write_text(new, encoding="utf-8")
    print(f"Wrote {len(table)} codes to {MODULE.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
