"""Live map streaming response models (GET /v1/p2maps/livemap).

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any

from .geometry import Position


@dataclass(frozen=True)
class LiveMapStreamInit:
    """Response to GET /v1/p2maps/livemap?robotId={blid}. CONFIRMED
    (session 48) via LiveMapStreamResponse$$serializer's <clinit>:
    mqtt_topic/livemap_url -- exactly matching the field names already
    used here."""

    mqtt_topic: str
    initial_map_url: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LiveMapStreamInit:
        return cls(mqtt_topic=data["mqtt_topic"], initial_map_url=data.get("livemap_url"))


@dataclass(frozen=True)
class PositionSample:
    point: Position
    orientation: float
    operating_modes: int


@dataclass(frozen=True)
class PositionUpdateMessage:
    """A message on the livemap topic with position data. Multiple
    points per message are normal (trajectory-like, see FINDINGS)."""

    sequence_number: int
    updates: list[PositionSample]
    last_update_timestamp: datetime

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PositionUpdateMessage:
        """data is the "pos_update" envelope including cur_path.

        cur_path length must be (2 + 4*n) for n position points --
        exactly as checked in PositionUpdatesSerializer.deserialize().
        Orientation is shifted by +pi, same as in the original -- the
        reason for this convention wasn't further investigated.

        IMPORTANT TENSION, discovered but NOT resolved (session 48):
        a systematic `$$serializer` scan found
        `PositionUpdates$PositionUpdate$$serializer` -- an
        AUTO-GENERATED serializer (unlike the CUSTOM
        `PositionUpdatesSerializer` this method's cur_path-flat-array
        parsing was originally based on) -- with confirmed fields
        `point`/`orientation`/`operatingModes`. This is suspiciously
        close to this library's OWN `PositionSample` dataclass (point/
        orientation/operating_modes), which was built to match the
        cur_path-derived values, not copied from this serializer.
        Two real possibilities, neither confirmed: (a) the actual wire
        format for each position update is a structured JSON object
        matching PositionUpdate's confirmed fields directly, and the
        flat "cur_path" array parsing here is based on an earlier,
        possibly mistaken reading of the custom serializer's logic;
        or (b) both genuinely coexist -- the custom
        `PositionUpdatesSerializer` might pack/unpack a LIST of these
        structured PositionUpdate objects specifically into the flat
        "cur_path" wire array as an optimization, with PositionUpdate
        only ever existing as the in-memory Kotlin representation, not
        a JSON shape of its own. Resolving this needs either a real
        traffic capture of an actual livemap position message, or
        disassembling PositionUpdatesSerializer's own
        serialize()/deserialize() method bodies (not just reading a
        `<clinit>`'s literal strings, the same harder kind of
        investigation this session deliberately didn't pursue for
        SetRoomMetadata/VirtualWall's own custom serializers either).
        NOT changed this session -- flagging this honestly rather than
        guessing which one is right.
        """
        cur_path = data["cur_path"]
        if (len(cur_path) - 2) % 4 != 0:
            msg = f"cur_path unexpected size: {len(cur_path)}"
            raise ValueError(msg)

        sequence_number = int(cur_path[0])
        epoch_ts = cur_path[-1]
        point_values = cur_path[1:-1]

        updates = [
            PositionSample(
                point=(point_values[i], point_values[i + 1]),
                orientation=point_values[i + 2] + 3.1415927,
                operating_modes=int(point_values[i + 3]),
            )
            for i in range(0, len(point_values), 4)
        ]

        return cls(
            sequence_number=sequence_number,
            updates=updates,
            last_update_timestamp=datetime.fromtimestamp(epoch_ts, tz=UTC),
        )


@dataclass(frozen=True)
class MapUpdateMessage:
    """The other message shape on the livemap topic: a new map image
    is available, not a position update. CONFIRMED (session 48) via
    LiveMapUpdateResponse$$serializer/
    LiveMapUpdateResponse$LiveMapUpdate$$serializer's <clinit>s:
    map_update.livemap_url -- exactly matching the nesting already
    used here."""

    livemap_url: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MapUpdateMessage:
        return cls(livemap_url=data["map_update"]["livemap_url"])


def parse_livemap_message_data(data: dict[str, Any]) -> PositionUpdateMessage | MapUpdateMessage:
    """Core logic, operates on already-parsed JSON (dict). For
    parse_livemap_message() (raw bytes) AND for prime_robot.py's
    watch_live_map() (already gets the payload as a dict from
    mqtt_client.py's ShadowResponse -- re-serializing would be
    unnecessary)."""
    if "pos_update" in data:
        return PositionUpdateMessage.from_json(data["pos_update"])
    if "map_update" in data:
        return MapUpdateMessage.from_json(data)
    msg = f"Unrecognized livemap message shape: keys={list(data.keys())}"
    raise ValueError(msg)


def parse_livemap_message(raw_payload: bytes) -> PositionUpdateMessage | MapUpdateMessage:
    """Decides based on the keys present which of the two message
    shapes this is (see FINDINGS section 2, point 3)."""
    return parse_livemap_message_data(json.loads(raw_payload))


