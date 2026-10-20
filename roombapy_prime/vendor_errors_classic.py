"""iRobot's error texts as the CLASSIC app shows them.

**THE PRIME TABLE IS NOT THE CLASSIC ONE.** `vendor_errors` comes from
the Prime app, and the two apps disagree on what some codes mean, not
just on the wording:

    code  Classic app (iRobot Home 7.18.0)    Prime app (Roomba Home 3.2.0)
    2     "Brushes need cleaning"             "Main Brush stuck"
    6     stuck near a cliff                  clean the cliff sensors
    47    reboot required                     important update available
    106   battery error, take it off the base wait for the battery to cool down
    228   reboot required                     important update available

A j7 showing "Main Brush stuck" names a part the robot does not have.
A Classic robot's code is looked up here; a Prime robot's in
`vendor_errors`.

SOURCE: `com.irobot.home` 7.18.0 (versionCode 1607180002). The strings
come from `base.apk:resources.arsc`; which string belongs to which code
from `RobotStatusResolver` (Java) and `libcore_jni.so`
(StatusMessageUtility, DockStatusProvider). The table is
`vendor_errors_classic.json`, cut from that extraction by
`scripts/generate_vendor_errors_classic.py` and left byte for byte as
the app ships it. The normalising happens here, on the way out.

WHAT THE APP SHOWS FOR ONE CODE, AND WHAT THIS RETURNS:

    title     the status card's heading. For whole groups it is one
              shared line -- "Error", "Charging error", "Unable to
              start" -- rather than a name for the code.
    content   the status card's explanation.
    history   the line in the mission history.
    label     DERIVED, not an app field: `title`, unless that is one of
              the shared lines; then `history`, when there is one.
              What a caller puts where a code needs a name.

A code the status card never shows (54-63, 1000-1004, 350-360 ...) has
no `title`; its `label` is the history line. Where the app ships a
`notification_error_<N>` text but no display path for it was found
(1000/1001 side brush among them), that text is the `content` --
the key names the code, and an explanation beats none.

FOUR THINGS DEPEND ON THE ROBOT, AS THEY DO IN THE APP:

  - SKU. The status card and the history choose separately. The card:
    `^[Rr]69` (Roomba 69x) and `^[Qq]0` / `^[Yy]0` (Roomba Vac / Combo
    Essential) by regex. The history: the SKU's first three characters,
    upper-cased -- R69 and T72. Codes 2 and 206 are why: the 69x has a
    bristle brush, the default text speaks of rubber ones.
  - A Clean Base changes 36.
  - Imperial units change 74's history line.
  - Dock codes are their own namespace (`classic_dock_text`): the
    Classic app never reads 450-463 or 501-509 from the robot's error
    field. The Prime app does -- there they are faults like any other.

`@val` IS THE ROBOT'S NAME, as in `vendor_errors`: the app's `%s` and
`%1$s` become `@val`, so a caller handles one placeholder for both
apps. In dock texts the app's other argument -- `(%s)` and `%2$s`, at
the end, in brackets -- is the code, and it is filled in here.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

_TABLE: Final = Path(__file__).with_name("vendor_errors_classic.json")

#: The languages extracted. Anything else falls back to English, as in
#: `vendor_errors`.
LANGUAGES: Final[tuple[str, ...]] = ("de", "en", "es", "fr", "it", "nl", "pl", "pt")

_STATUS_SKU: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"^[Rr]69"), "R69"),
    (re.compile(r"^[Qq]0"), "Q0"),
    (re.compile(r"^[Yy]0"), "Y0"),
)
_STATUS_FIELDS: Final = ("title", "content")

#: The code in a dock text: "(%s)" or "%2$s" at the end, in brackets.
_DOCK_CODE: Final = re.compile(r"(?<=\()%s(?=\))|%2\$s")
_NAME: Final = re.compile(r"%1\$s|%s")
#: French writes "(Erreur 1 015)": the code is an identifier, not a
#: quantity, and reads back as the robot reports it.
_GROUPED_CODE: Final = re.compile(r"(?<=\d)[   ](?=\d{3}\))")
_ZERO_WIDTH: Final = "​"


@cache
def _table() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(_TABLE.read_text(encoding="utf-8")))


# READ AT IMPORT (0.8.1), not at the first lookup. 0.8.0 read the 500 KB
# here on the first call, and a caller's first call can be in an event
# loop: Home Assistant reported "Detected blocking call to read_text ...
# inside the event loop" for Roomba+ 4.5.0b2. Importing is where file
# I/O is expected (Home Assistant imports in its executor); a lookup
# should only look up. About 8 ms, paid by whoever imports this module
# -- `roombapy_prime` itself does not.
#
# A file that cannot be read must not break the import of everything
# that imports this module: the lookup then reads it itself and raises
# there, as 0.8.0 did.
try:
    _table()
except (OSError, ValueError):
    pass


def _language(language: str | None) -> str:
    lang = re.split(r"[-_]", str(language or "en"))[0].lower()
    return lang if lang in LANGUAGES else "en"


def _number(code: Any) -> int | None:
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _tidy(text: str, code: int | None = None) -> str:
    """`@val` for the robot's name; in a dock text, the code itself."""
    if code is not None:
        text = _DOCK_CODE.sub(str(code), text)
    text = _NAME.sub("@val", text)
    return _GROUPED_CODE.sub("", text.replace(_ZERO_WIDTH, ""))


def _status_variant(sku: str | None) -> str | None:
    if not sku:
        return None
    return next((name for pattern, name in _STATUS_SKU if pattern.match(sku)), None)


def _history_variant(sku: str | None) -> str | None:
    return str(sku).upper()[:3] if sku else None


def _apply_sku(found: dict[str, str], code: int, lang: str, sku: str | None) -> bool:
    """The SKU's variants into `found`; True when the title was left behind.

    The app replaces a 69x's explanation of error 2 ("main brushes")
    and keeps the heading ("Gummibürsten" in German) -- a heading
    naming a part the robot does not have. The label then follows the
    replaced history line, not the heading.
    """
    variants = _table()["sku_overrides"].get(str(code))
    if not variants:
        return False
    status = variants.get(_status_variant(sku) or "", {}).get(lang, {})
    found.update({f: status[f] for f in _STATUS_FIELDS if f in status})
    history = variants.get(_history_variant(sku) or "", {}).get(lang, {})
    if "history" in history:
        found["history"] = history["history"]
    replaced = "history" in history or "content" in status
    return replaced and "title" not in status


def _with_label(found: dict[str, str], generic: bool) -> dict[str, str]:
    label = found.get("history") if generic or "title" not in found else found["title"]
    label = label or found.get("title") or found.get("content")
    if label:
        found["label"] = label
    return found


def classic_vendor_error(
    code: Any,
    language: str = "en",
    *,
    sku: str | None = None,
    clean_base: bool = False,
    imperial: bool = False,
    as_shipped: bool = False,
) -> dict[str, str] | None:
    """The Classic app's texts for a robot error code, or None.

    Keys present: any of `title`, `content`, `history`, and `label`
    (see the module docstring). `sku` is the robot's own (`sku` in its
    state, e.g. "R694020"); without it the default texts apply, as in
    the app for a robot it cannot place. `as_shipped=True` returns the
    app's fields with their placeholders untouched and no `label`.
    Either way the answer is a copy.
    """
    number = _number(code)
    if number is None:
        return None
    lang = _language(language)
    table = _table()
    entry = table["robot"].get(str(number))
    if entry is None and str(number) not in table["sku_overrides"]:
        return None
    found: dict[str, str] = dict((entry or {}).get(lang, {}))
    feet = found.pop("history_feet", None)
    unlinked = found.pop("content_unlinked", None)
    if as_shipped:
        if feet is not None:
            found["history_feet"] = feet
        if unlinked is not None:
            found["content_unlinked"] = unlinked
    else:
        if imperial and feet is not None:
            found["history"] = feet
        if "content" not in found and unlinked is not None:
            found["content"] = unlinked
    title_left_behind = _apply_sku(found, number, lang, sku)
    if clean_base:
        evac = table["clean_base_overrides"].get(str(number), {}).get("evac", {})
        found.update(evac.get(lang, {}))
    if not found:
        return None
    if as_shipped:
        return found
    tidy = {key: _tidy(value) for key, value in found.items()}
    generic = number in table["generic_title_codes"] or title_left_behind
    return _with_label(tidy, generic)


def classic_not_ready_text(
    number: Any, language: str = "en", *, sku: str | None = None, as_shipped: bool = False
) -> dict[str, str] | None:
    """The Classic app's start refusal for `cleanMissionStatus.notReady`.

    `number` IS THE VALUE AS THE ROBOT REPORTS IT, not an index into a
    list of readiness states: the app's tables (`kCommonStartRefuse
    States*`) are keyed by it -- 15 InsufficientCharge, 33 Bumped, 39
    SavingMap. A caller that decodes the wire value into a state index
    first looks up the wrong refusal.

    Its own namespace: notReady 15 is "battery too low to start", error
    15 is "robot rebooted". The history shows the same text under code
    200 + N, which is where the SKU variants are filed (notReady 6 on a
    69x speaks of its brush). `title` is always the shared "Unable to
    start"; `label` is the history line.
    """
    n = _number(number)
    if n is None:
        return None
    lang = _language(language)
    entry = _table()["not_ready"].get(str(n))
    if entry is None:
        return None
    found = dict(entry.get(lang, {}))
    _apply_sku(found, 200 + n, lang, sku)
    if as_shipped:
        return found
    return _with_label({key: _tidy(value) for key, value in found.items()}, True)


def classic_dock_text(
    code: Any, language: str = "en", *, as_shipped: bool = False
) -> dict[str, str] | None:
    """The Classic app's text for a dock state or dock error code.

    301-304 and 350-365 are the Clean Base, 401-463 the refill dock,
    500-520 errors common to docks, 601-669 pad wash, 701-757 pad dry.
    The live status reads them from the robot's `dock` state; the
    mission history from the dock's evacuation history. `content` is
    the live text, `history` the mission history's, `history_complete`
    the history's line when the mission itself finished.
    """
    number = _number(code)
    if number is None:
        return None
    entry = _table()["dock"].get(str(number))
    if entry is None:
        return None
    found = dict(entry.get(_language(language), {}))
    if as_shipped:
        return found
    tidy = {key: _tidy(value, number) for key, value in found.items()}
    return _with_label(tidy, False)


def classic_help_id(code: Any = None, *, not_ready: Any = None) -> str | None:
    """The key iRobot's help catalogue files a Classic error under.

    The Classic app asks `GET <content host>/v2/help/{lang}/{country}/
    {sku}` for the help of a robot model and picks the entry whose `id`
    is this key (`HelpContentId`, `libcore_jni.so`):

        error 1-99, 1000+   "Error <N>"
        error 101-121       "Charging Error <N-100>"
        error 200-299       "Start-Refuse <N-200>"
        notReady n          "Start-Refuse <n>"

    Interface information, not text: what the entry links to is iRobot's
    page for that model, in the language asked for. A key the catalogue
    does not carry is simply not found; nothing is made up for it.
    """
    if not_ready is not None:
        n = _number(not_ready)
        return f"Start-Refuse {n}" if n is not None and n > 0 else None
    number = _number(code)
    if number is None or number <= 0:
        return None
    if 101 <= number <= 121:
        return f"Charging Error {number - 100}"
    if 200 <= number <= 299:
        return f"Start-Refuse {number - 200}"
    return f"Error {number}"
