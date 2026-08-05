"""Live map streaming response models (GET /v1/p2maps/livemap).

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/internal/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

import zlib

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
    #: When the robot stops publishing unless asked again, from
    #: `update_expire_ts` on the OUTER envelope (@SerialName confirmed
    #: on LiveMapPositionUpdateResponse).
    #:
    #: THIS IS HOW THE APP PACES ITS KEEP-ALIVE. LiveMapKeepAlivePublisher
    #: schedules the next ping at
    #:
    #:     max(0, expiration - now - refreshWindowMillis)
    #:
    #: with refreshWindowMillis defaulting to 10000 -- a ten-second
    #: SAFETY MARGIN before the stream lapses, not a ten-second interval.
    #: This library polled at a fixed ten seconds, which is the same
    #: number meaning something entirely different: 8,640 REST calls per
    #: robot per day instead of one per validity window.
    #:
    #: None when the robot did not send it. The caller then has nothing
    #: to schedule against and must fall back to its own interval --
    #: pacing on a guessed expiry would risk the stream lapsing
    #: mid-mission, which is worse than polling too often.
    expires_at: datetime | None = None

    @classmethod
    def from_json(
        cls, data: dict[str, Any], expire_ts: Any = None
    ) -> PositionUpdateMessage:
        """data is the "pos_update" envelope including cur_path.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#livemapfrom_json
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
                # THE WIRE ANGLE, UNMODIFIED. This added 3.1415927 --
                # half a turn -- with no comment, no entry in the
                # evidence trail, and a test that asserted only
                # `0.0 + 3.1415927`, which restates the code rather than
                # checking it against anything.
                #
                # The first observation anyone has ever made of the
                # heading says it was wrong: with the marker finally
                # drawn (a24), the line points out of the BACK of the
                # robot (@DaRealGuGu, 505). Half a turn is exactly that.
                #
                # Removed rather than compensated for downstream,
                # because a consumer drawing an arrow should get the
                # angle the robot reported. If this turns out to be
                # wrong, the line points backwards -- which is where it
                # already was, so the change cannot cost anything.
                orientation=point_values[i + 2],
                operating_modes=int(point_values[i + 3]),
            )
            for i in range(0, len(point_values), 4)
        ]

        return cls(
            sequence_number=sequence_number,
            updates=updates,
            last_update_timestamp=datetime.fromtimestamp(epoch_ts, tz=UTC),
            expires_at=_as_datetime(expire_ts),
        )


@dataclass(frozen=True)
class MapUpdateMessage:
    """The other message shape on the livemap topic: a new map image
    is available, not a position update. CONFIRMED (session 48) via
    LiveMapUpdateResponse$$serializer/
    LiveMapUpdateResponse$LiveMapUpdate$$serializer's <clinit>s:
    map_update.livemap_url -- exactly matching the nesting already
    used here.

    Full evidence trail, correction history and open questions:
    docs/internal/EVIDENCE_TRAIL.md#livemapmapupdatemessage
    """

    livemap_url: str
    livemap_url_raw: str | None = None
    timestamp: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MapUpdateMessage:
        update = data["map_update"]
        return cls(
            livemap_url=update["livemap_url"],
            livemap_url_raw=update.get("livemap_url_raw"),
            timestamp=data.get("timestamp"),
        )


def _as_datetime(value: Any) -> datetime | None:
    """`update_expire_ts` as a datetime, whatever unit it arrives in.

    SECONDS OR MILLISECONDS IS NOT SETTLED. The app types it as a Date,
    which says nothing about the wire, and no capture carrying the field
    has been seen. Values past the year 2200 are read as milliseconds --
    a threshold, not a guess: an epoch in seconds does not reach it for
    another 175 years, and one in milliseconds passed it in 1970.

    Returns None for anything unusable rather than raising. This is
    pacing information; getting it wrong should cost the pacing, not the
    stream.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    seconds = value / 1000.0 if value > 7_258_118_400 else float(value)
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def parse_livemap_message_data(data: dict[str, Any]) -> PositionUpdateMessage | MapUpdateMessage:
    """Core logic, operates on already-parsed JSON (dict). For
    parse_livemap_message() (raw bytes) AND for prime_robot.py's
    watch_live_map() (already gets the payload as a dict from
    mqtt_client.py's ShadowResponse -- re-serializing would be
    unnecessary)."""
    if "pos_update" in data:
        # The expiry rides on the OUTER envelope, beside `pos_update`,
        # and used to be discarded here -- the inner object was parsed
        # and the wrapper thrown away.
        return PositionUpdateMessage.from_json(
            data["pos_update"], expire_ts=data.get("update_expire_ts")
        )
    if "map_update" in data:
        return MapUpdateMessage.from_json(data)
    msg = f"Unrecognized livemap message shape: keys={list(data.keys())}"
    raise ValueError(msg)


def parse_livemap_message(raw_payload: bytes) -> PositionUpdateMessage | MapUpdateMessage:
    """Decides based on the keys present which of the two message
    shapes this is (see FINDINGS section 2, point 3)."""
    return parse_livemap_message_data(json.loads(raw_payload))


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _read_field(buf: bytes, pos: int) -> tuple[int, int, object, int]:
    """Returns (field_number, wire_type, value, new_pos)."""
    tag, pos = _read_varint(buf, pos)
    field_num = tag >> 3
    wire_type = tag & 0x7
    if wire_type == 0:
        value, pos = _read_varint(buf, pos)
    elif wire_type == 2:
        length, pos = _read_varint(buf, pos)
        value = buf[pos : pos + length]
        pos += length
    elif wire_type == 5:
        value = buf[pos : pos + 4]
        pos += 4
    elif wire_type == 1:
        value = buf[pos : pos + 8]
        pos += 8
    else:
        msg = f"Unsupported protobuf wire type {wire_type} at offset {pos}"
        raise ValueError(msg)
    return field_num, wire_type, value, pos


def _parse_top_level(buf: bytes) -> dict[int, list[tuple[int, object]]]:
    fields: dict[int, list[tuple[int, object]]] = {}
    pos = 0
    while pos < len(buf):
        field_num, wire_type, value, pos = _read_field(buf, pos)
        fields.setdefault(field_num, []).append((wire_type, value))
    return fields


def _maybe_decompress(rawmap_bytes: bytes) -> bytes:
    """Transparently unwraps a zlib-compressed live map.

    CONFIRMED FROM A REAL FIELD CAPTURE (DaRealGuGu, and almost
    certainly the same cause as chairstacker's long-standing blank live
    map). The payload starts `78 9c` -- a zlib header with default
    compression -- and the protobuf parser was being handed those bytes
    directly. It failed at offset 7 with "unsupported wire type 4",
    which reads like a protocol mismatch and is really just compressed
    data.

    Worth noting how this was found: the decoder used to fail silently,
    so the symptom was a permanently blank map image and nothing else.
    The diagnostic logging added for exactly this purpose printed the
    first 32 bytes, and the answer was in the first two.

    Kept tolerant on purpose: if the payload is not compressed, it is
    passed through untouched, since it is not established that every
    firmware compresses. And a decompression failure re-raises with the
    header bytes named, rather than letting the protobuf parser produce
    a misleading offset error further down."""
    if not rawmap_bytes[:1] == b"\x78":
        return rawmap_bytes
    try:
        return zlib.decompress(rawmap_bytes)
    except zlib.error as exc:
        msg = (
            f"live map payload starts with a zlib header "
            f"({rawmap_bytes[:2].hex()}) but could not be decompressed: {exc}"
        )
        raise ValueError(msg) from exc


def decode_rawmap_to_png(rawmap_bytes: bytes) -> bytes:
    """CONFIRMED STRUCTURE (chairstacker, visually verified against
    the real app's own map view) -- promoted here from a standalone
    diagnostic script (decode_rawmap.py) into a proper library
    function, for MapUpdateMessage's "rawmap" path (see that class's
    own docstring for the full protobuf-layout evidence trail).

    "rawmap" is a Protocol Buffers message (no public .proto schema,
    walked generically by field number/wire type, not assumed):
    field 3 -> header (width int, height int, several float32s
    including a 0.05 resolution matching standard 5cm/cell SLAM
    grids); field 4 -> wraps field 1, the actual occupancy grid
    (width*height bytes, one byte per cell).

    Returns PNG bytes, already vertically flipped to match the real
    app's own orientation directly (image formats conventionally
    store row 0 at the top; this occupancy grid stores row 0 at the
    bottom, matching a real-world Y-axis that increases upward --
    confirmed by directly comparing the unflipped render against the
    app, not assumed).

    Raises ValueError if the expected field 3 (header)/field 4 (grid)
    structure isn't found, or if width*height doesn't match the grid
    byte count -- callers should treat either as "this specific
    rawmap didn't match the confirmed layout", not silently render a
    garbled image. Raises ImportError with a clear message if Pillow
    isn't installed -- deliberately NOT a hard dependency of this
    library (most callers never need image rendering), install it
    yourself (`pip install Pillow`) if you need this function."""
    try:
        from PIL import Image
    except ImportError as exc:
        msg = "decode_rawmap_to_png() needs Pillow -- pip install Pillow"
        raise ImportError(msg) from exc

    rawmap_bytes = _maybe_decompress(rawmap_bytes)
    top = _parse_top_level(rawmap_bytes)

    width = height = None
    if 3 in top:
        _wt, header_bytes = top[3][0]
        header_fields = _parse_top_level(header_bytes)
        for fnum in sorted(header_fields.keys()):
            for wt, val in header_fields[fnum]:
                if wt == 0:
                    if fnum == 2 and width is None:
                        width = val
                    elif fnum == 3 and height is None:
                        height = val
    if not (width and height):
        msg = "rawmap header (field 3) didn't contain the expected width/height -- unrecognized layout"
        raise ValueError(msg)

    if 4 not in top:
        msg = "rawmap has no field 4 (grid wrapper) -- unrecognized layout"
        raise ValueError(msg)
    _wt, grid_wrapper = top[4][0]
    inner = _parse_top_level(grid_wrapper)
    if 1 not in inner:
        msg = "rawmap's field 4 has no field 1 (grid bytes) inside it -- unrecognized layout"
        raise ValueError(msg)
    _wt, grid_bytes = inner[1][0]

    if width * height != len(grid_bytes):
        msg = (
            f"rawmap grid byte count ({len(grid_bytes)}) doesn't match "
            f"width*height ({width}*{height}={width * height}) -- unrecognized layout"
        )
        raise ValueError(msg)

    img = Image.frombytes("L", (width, height), grid_bytes)
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


