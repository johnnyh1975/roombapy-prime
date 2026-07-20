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

        CONFIRMED LIVE (this session, jayjay13011, roombapy-prime
        v0.1.11a6 -- the first capture with topic tracking, so the
        exact topic this arrives on is now also settled, see
        livemap_topic()/watch_live_map()). This directly resolves the
        TENSION noted below in favor of option (a): the flat cur_path
        array genuinely IS the wire format, not a misreading -- a real
        capture confirms it exactly, including operating_modes
        actually varying (not a fixed constant): 0 for the first ~5
        seconds of cleaning (still settling in after travel/reloc),
        then switching to 5 for the rest of the observed cleaning
        period. The switch happens a few seconds AFTER
        mission/timeline/report's own "room" event fires, not
        precisely at that boundary -- plausibly a finer-grained
        sub-state (e.g. "orienting" vs "actively cleaning") than what
        the mission-timeline channel exposes, but this is not
        confirmed, just a reasonable reading of the timing.

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
        a JSON shape of its own. The live capture above settles which
        of these is right for the WIRE FORMAT (flat array, confirmed);
        it doesn't settle whether PositionUpdate the class still
        exists internally in the app for the same data.
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
    used here.

    CONFIRMED LIVE (this session, jayjay13011, roombapy-prime v0.1.11a6):
    real messages also carry an outer "timestamp" and a sibling
    "livemap_url_raw" alongside "livemap_url" -- both added here, not
    previously modeled. livemap_url is a presigned S3 URL ending in
    "p2mapv_geojson.tgz" -- the EXACT SAME format
    download_map_bundle()/parse_map_bundle() already handle for
    REST-fetched bundles; no new download/parsing code is needed to
    consume this live feed. livemap_url_raw points to a sibling
    "rawmap" path. Both URLs' paths are fixed/generic per robot
    (".../dload_livemap/{blid}/..."), not versioned per-update -- only
    the query-string signing differs between messages, confirmed by
    direct comparison, not assumed.

    "rawmap" FORMAT, FULLY DECODED (this session, chairstacker, from a
    hexdump of a file saved during an earlier run -- the actual map
    content was never shared, only structural bytes/strings). This is
    a Protocol Buffers message, not a raw occupancy grid directly (the
    earlier "raw grid, one byte per file" hypothesis was wrong about
    the FILE as a whole, but right about what's embedded inside it).
    Confirmed structure, hand-decoded against the real hexdump and
    verified with a synthetic reconstruction matching it exactly:

        field 2 -> nested message: two Unix timestamps (map created/
                   updated), and a sub-message (field 7) containing
                   the map_id as a 32-char hex string
        field 3 -> nested message: a plain-int map_id-suffix
                   timestamp, then width and height as plain varints
                   (440 x 400 in the one real example), then five
                   float32 fields -- almost certainly origin_x,
                   origin_y, and other bounds/rotation values, with
                   the smallest positive one (0.05) being the
                   resolution in metres/cell -- a completely standard
                   SLAM occupancy-grid value (5cm/cell)
        field 4 -> wraps exactly one bytes field (field 1): the
                   occupancy grid itself, width*height bytes, one byte
                   per cell -- 176000 bytes in the real example,
                   EXACTLY matching 440*400, confirmed directly rather
                   than assumed

    "Clean Kitchen" (a room name) and "Map1"/"Map2" also appeared as
    plain strings elsewhere in the file (via `strings`) -- not yet
    located precisely in the field layout above, presumably a sibling
    field carrying room-name/multi-map metadata this session didn't
    reach. `models/livemap.py` doesn't yet parse this structure into a
    dataclass -- `decode_rawmap.py` (a standalone script, not part of
    the library) exists to extract and render the grid for
    confirmation first, before committing to field names here.

    NOT YET USED for anything beyond this model -- no entity in
    ha_roomba_plus consumes it yet. A concrete next step this makes
    possible: a live-updating map/camera entity, refreshed from
    whatever the most recent MapUpdateMessage delivered, using
    download_map_bundle()/parse_map_bundle() directly against
    livemap_url -- no new download or parsing code needed. Now that
    rawmap's structure is understood, an occupancy-grid-based overlay
    (or even a full replacement of the GeoJSON-based approach) becomes
    a real, evidenced option -- not yet designed or built."""

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


