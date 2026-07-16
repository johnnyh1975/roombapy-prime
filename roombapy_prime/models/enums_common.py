"""Small, cross-cutting enums/helpers shared across multiple feature areas.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from enum import IntEnum
from typing import Any


class RoomType(IntEnum):
    NOT_RECOGNIZED = 2100
    BEDROOM = 2101
    DINING_ROOM = 2102
    BATHROOM = 2103
    HALLWAY = 2104
    KITCHEN = 2105
    LIVING_ROOM = 2106
    BALCONY = 2107
    OTHER = 2120


class FurnitureType(IntEnum):
    UNKNOWN = 0
    BED = 1
    SOFA = 2
    DINING_TABLE = 3
    COFFEE_TABLE = 4
    TOILET = 5
    LIVING_CHAIR = 6
    LEFT_L_SOFA = 7
    RIGHT_L_SOFA = 8
    CABINET = 9
    REFRIGERATOR = 10
    SIDETABLE = 11
    TVCABINET = 12
    WASHINGMACHINEORDRYER = 13
    LITTER_BOX = 14
    PET_BOWL = 15
    PET_BED = 16
    PET_FEEDER = 17
    CAT_TOWER = 18


def _enum_or_none(enum_cls: type, value: Any) -> Any:
    """Helper function: returns enum_cls(value) if possible, otherwise
    the raw value (instead of raising a ValueError) -- the server may
    introduce new values that this library version doesn't know yet."""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return value


