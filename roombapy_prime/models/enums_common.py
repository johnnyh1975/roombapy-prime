"""Small, cross-cutting enums/helpers shared across multiple feature areas.

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from enum import IntEnum, StrEnum
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


class RoomCategory(StrEnum):
    """CONFIRMED (live APK decompilation, this session, via
    P2MapRoomMetadata$Serializer.serialize()): the room-category value
    written into SetRoomMetadataV1's room_metadata.type -- a
    COMPLETELY SEPARATE enum from RoomType above, despite both being
    "what kind of room is this". RoomType (2100-2120 int codes) belongs
    to the app-deprecated SetRoomTypeV1/RenameRoomV1 command family.
    This one belongs to SetRoomMetadataV1, the app's current path, and
    has its own distinct wire representation.

    THE ACTUAL TRAP THIS CLASS EXISTS TO DOCUMENT: the underlying
    Kotlin enum (P2MapRoomInfo.RoomType.Value) has its own `raw` field
    with camelCase values ("diningRoom", "livingRoom") -- the
    NATURAL-LOOKING thing to assume the serializer uses. It does NOT.
    The serializer calls `type.name().toLowerCase()` -- the Kotlin enum
    CONSTANT NAME itself, lowercased -- which gives snake_case
    ("dining_room", "living_room"), not the raw field's camelCase. Two
    of nine values would have been wrong (missing their underscore) had
    the more-plausible-looking `raw` field been assumed instead of
    checking the actual serializer call."""

    UNKNOWN = "unknown"
    BEDROOM = "bedroom"
    DINING_ROOM = "dining_room"
    BATHROOM = "bathroom"
    HALLWAY = "hallway"
    KITCHEN = "kitchen"
    LIVING_ROOM = "living_room"
    BALCONY = "balcony"
    OTHER = "other"


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


