"""The vendor's own value sets, as data rather than as prose.

WHY THIS EXISTS, stated plainly because the reason is a mistake rather
than a design.

A full decode of iRobot app 3.0.0 produced 541 Dart enums, 89 SDK
models, 223 Kotlin serialisers, 35 capability gates and 24 writable
settings. All of it was read. Then six controls were built -- and two of
them were built from recall instead:

  * `padDryDur` shipped with a comment saying no vendor enum existed and
    the range was inferred from two field captures. `DryDurType` was in
    the extract the whole time. The inferred range happened to be right.
  * `pwHeat` shipped with labels off/low/high and no dock gate.
    `HeatType` names the middle value `defaultHeat`, and
    `DockPadWashingType` makes the option set depend on `dock.cap.pw`.
    A level-2 dock would have been offered a heat level it cannot
    produce.

Both were found because somebody asked, not because anything checked.
Reading a document does not make its contents available at the moment of
writing code, and the honest conclusion is that it never will.

WHAT THIS FILE CHANGES: the extract ships with the library as
`vendor_reference.json`, and value sets DECLARE which vendor enum they
come from. A guard asserts the two agree. Forgetting to look is no
longer possible, because not looking is now a test failure.

WHY DECLARED AND NOT DISCOVERED. The first attempt matched value sets
against the extract by shape, and immediately produced false matches:
`{0, 1, 2}` "exactly matches" `CleanPathDensity`, `HeatType`,
`CheckFurnitureValidCode` and several others. Small integer sets
collide, the same way short lowercase words collided in an earlier
literal search. A match is only evidence when the name was stated
first.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REFERENCE = Path(__file__).with_name("vendor_reference.json")


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads(_REFERENCE.read_text(encoding="utf-8"))


class VendorReferenceError(LookupError):
    """A name that is not in the vendor extract.

    Raised rather than returning None on purpose: a typo'd enum name
    silently passing a check would restore exactly the situation this
    module exists to end.
    """


def enum_values(name: str) -> dict[str, Any]:
    """Member name -> wire value, for one vendor enum.

    >>> enum_values("DryDurType")["four"]
    4
    """
    enums = _data()["enums"]
    if name not in enums:
        raise VendorReferenceError(
            f"{name!r} is not in the app 3.0.0 extract. Check the spelling "
            f"against vendor_reference.json before assuming the enum is absent -- "
            f"'not found where I looked' has been wrong here before."
        )
    return dict(enums[name])


def wire_values(name: str) -> set[Any]:
    """Just the values of one vendor enum, for set comparison."""
    return set(enum_values(name).values())


def capability_gate(name: str) -> dict[str, Any]:
    """One entry of the vendor's capability table: key path, type, shadow."""
    gates = _data()["capability_gates"]
    if name not in gates:
        raise VendorReferenceError(f"{name!r} is not a known capability gate")
    return dict(gates[name])


def writable_settings() -> dict[str, Any]:
    """The 24 individually writable setting keys, with their types."""
    return dict(_data()["writable_settings"])


def command_wire_values() -> dict[str, str]:
    """Command constant name -> the value that goes on the wire."""
    return dict(_data()["command_wire_values"])


def has_enum(name: str) -> bool:
    """Whether the extract contains this enum at all.

    For the case where absence is the question being asked, so that it
    can be answered without an exception."""
    return name in _data()["enums"]
