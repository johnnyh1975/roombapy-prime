"""State-/Kommando-Payload-Typen fuer roombapy-prime.

STATUS: Draft. Basiert auf Java/Kotlin-Quellcode-Analyse der von der
Prime-App genutzten `irobotdata`-Schicht (siehe
docs/FINDINGS_2026-07-11.md) -- NICHT gegen ein echtes V4-Konto live
verifiziert. Wire-Shapes hier sind so genau wie die Analyse es zulaesst,
aber ungetestet gegen echte Server-Antworten.

Enthaelt:
  - Geometrie-Primitive (Position/Point/LineString/Polygon) -- bestaetigt
    reines GeoJSON (siehe GeometrySerializer.java: Polygon.getRawValue()
    liefert List<List<List<Double>>>, exakt GeoJSON-Polygon-Nesting)
  - RoomType, FurnitureType -- Int-Enums, Werte aus Java-Quellcode
  - Die 10 bestaetigten p2maps-Editierbefehle (POST /v2/p2maps/{id}/versions)
  - Live-Karte/-Position-Antwortmodelle (GET /v1/p2maps/livemap)
"""
from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from io import BytesIO
from typing import Any


# --- Geometrie (GeoJSON) ------------------------------------------------
#
# Bestaetigt in com.irobot.irobotdata.maps.domainmodels.geometry.*:
# Position ist ein flaches [x, y] (optional [x, y, z]) Array (Position
# erweitert dort sogar ArrayList<Double> direkt). Point/LineString/Polygon
# sind Standard-GeoJSON mit "type"-Feld. LinearRing ist nur ein interner
# Kotlin-Marker -- auf dem Wire ist Polygon.coordinates eine reine
# [[[x,y],...]]-Verschachtelung ohne LinearRing-Objektwrapper.

Position = tuple[float, float]  # (x, y) -- z wird bislang nirgends benutzt


def _position_to_raw(pos: Position) -> list[float]:
    return [pos[0], pos[1]]


@dataclass(frozen=True)
class Point:
    coordinates: Position

    def to_geojson(self) -> dict[str, Any]:
        return {"type": "Point", "coordinates": _position_to_raw(self.coordinates)}


@dataclass(frozen=True)
class LineString:
    coordinates: list[Position]

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "LineString",
            "coordinates": [_position_to_raw(p) for p in self.coordinates],
        }


@dataclass(frozen=True)
class Polygon:
    """coordinates: Liste von Ringen, jeder Ring eine Liste von Position.
    Erster Ring = Aussenkontur, weitere = Loecher (Standard-GeoJSON,
    hier nie mit mehr als einem Ring in freier Wildbahn beobachtet)."""

    coordinates: list[list[Position]]

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "Polygon",
            "coordinates": [[_position_to_raw(p) for p in ring] for ring in self.coordinates],
        }


@dataclass(frozen=True)
class MultiPolygon:
    """coordinates: Liste von Polygon -- bestaetigt in
    MultiPolygon.java (extends Geometry, type="MultiPolygon",
    coordinates: List<Polygon>). Nur fuer Lese-Modelle gebraucht
    (BorderInfo, CoverageInfo) -- kein Editierbefehl nutzt das bisher."""

    coordinates: list[Polygon]

    def to_geojson(self) -> dict[str, Any]:
        return {"type": "MultiPolygon", "coordinates": [p.to_geojson()["coordinates"] for p in self.coordinates]}


# --- RoomType / FurnitureType -------------------------------------------
#
# Werte woertlich aus EditMapV2Request$RoomType (Int-Enum) und
# P2MapFurnitureInfo$FurnitureType (Int-Enum). set_room_type ist laut
# Quellcode @Deprecated zugunsten von set_room_metadata -- hier trotzdem
# modelliert, da das Kommando technisch weiterhin existiert.

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


# --- p2maps-Editierbefehle (POST /v2/p2maps/{id}/versions) --------------
#
# Body-Huelle fuer alle Kommandos: {"command": "<cmd>", "params": {...}}.
# Jede Kommando-Klasse hier hat eine to_command_body()-Methode, die genau
# dieses Envelope produziert. Feldnamen (snake_case-JSON-Keys) sind aus
# den Kotlin @SerialName-Annotationen uebernommen, siehe
# docs/FINDINGS_2026-07-11.md fuer die vollstaendige Herleitung.
#
# WICHTIG: Kein einziges dieser 10 Kommandos wurde live gegen einen
# echten Server getestet -- nur die Java-Serialisierungslogik ist
# bestaetigt. Behandelt als Draft, bis echte Antworten vorliegen.


@dataclass(frozen=True)
class SetRoomMetadata:
    room_id: str
    name: str | None = None
    room_type: RoomType | None = None

    def to_command_body(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if self.name is not None:
            metadata["name"] = self.name
        if self.room_type is not None:
            metadata["type_id"] = int(self.room_type)
        return {
            "command": "set_room_metadata",
            "params": {"id": self.room_id, "metadata": metadata},
        }


@dataclass(frozen=True)
class MergeRooms:
    room_ids: list[str]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "merge_rooms", "params": {"ids": self.room_ids}}


@dataclass(frozen=True)
class SplitRoom:
    room_id: str
    split_line: LineString

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "split_room",
            "params": {"id": self.room_id, "split_line": self.split_line.to_geojson()},
        }

    @classmethod
    def from_two_points(cls, room_id: str, from_pos: Position, to_pos: Position) -> "SplitRoom":
        return cls(room_id=room_id, split_line=LineString([from_pos, to_pos]))


@dataclass(frozen=True)
class SetRoomType:
    """@Deprecated in Kotlin-Quellcode zugunsten von SetRoomMetadata --
    hier trotzdem modelliert, da das Kommando weiterhin existiert."""

    room_id: str
    room_type: RoomType

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_room_type",
            "params": {"room_id": self.room_id, "type_id": int(self.room_type)},
        }


@dataclass(frozen=True)
class KeepOutZone:
    """Deckt sowohl Linear- als auch Rechteck-Sperrzonen ab -- je nachdem
    ob eine LineString oder ein Polygon uebergeben wird."""

    geometry: LineString | Polygon
    zone_id: str | None = None

    def to_geojson(self) -> dict[str, Any]:
        payload = self.geometry.to_geojson()
        if self.zone_id is not None:
            return {"id": self.zone_id, "geometry": payload}
        return {"geometry": payload}


@dataclass(frozen=True)
class SetKeepOutZones:
    keep_out_zones: list[KeepOutZone] = field(default_factory=list)
    no_mop_zones: list[KeepOutZone] = field(default_factory=list)
    virtual_walls: list[KeepOutZone] = field(default_factory=list)

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_keep_out_zones",
            "params": {
                "keep_out_zones": [z.to_geojson() for z in self.keep_out_zones],
                "no_mop_zones": [z.to_geojson() for z in self.no_mop_zones],
                "virtual_walls": [z.to_geojson() for z in self.virtual_walls],
            },
        }


@dataclass(frozen=True)
class CleanZone:
    name: str
    geometry: Polygon
    zone_id: str | None = None

    def to_geojson(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "geometry": self.geometry.to_geojson()}
        if self.zone_id is not None:
            payload["id"] = self.zone_id
        return payload


@dataclass(frozen=True)
class AddCleanZones:
    zones: list[CleanZone]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "add_clean_zones", "params": {"zones": [z.to_geojson() for z in self.zones]}}


@dataclass(frozen=True)
class DeleteCleanZones:
    zone_ids: list[str]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "delete_clean_zones", "params": {"ids": self.zone_ids}}


@dataclass(frozen=True)
class Furniture:
    furniture_type: FurnitureType
    geometry: Polygon
    furniture_id: str | None = None
    user_modified: bool = True

    def to_geojson(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user_modified": self.user_modified,
            "geometry": self.geometry.to_geojson(),
            "type": self.furniture_type.name.lower(),
        }
        if self.furniture_id is not None:
            payload["id"] = self.furniture_id
        return payload


@dataclass(frozen=True)
class SetFurniture:
    furniture: list[Furniture]

    def to_command_body(self) -> dict[str, Any]:
        return {"command": "set_furniture", "params": {"furniture": [f.to_geojson() for f in self.furniture]}}


@dataclass(frozen=True)
class RevertUserEdits:
    def to_command_body(self) -> dict[str, Any]:
        return {"command": "revert_user_edits", "params": {}}


@dataclass(frozen=True)
class FloorTypeEntry:
    """Zwei Varianten im Quellcode (WithGeometry / WithRoomId) -- genau
    eine von geometry/room_id muss gesetzt sein, nicht beide."""

    floor_type_id: str
    type_name: str
    name: str
    enabled: bool
    user_modified: bool = True
    geometry: Polygon | None = None
    room_id: str | None = None

    def to_geojson(self) -> dict[str, Any]:
        if (self.geometry is None) == (self.room_id is None):
            msg = "FloorTypeEntry needs exactly one of geometry or room_id"
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "id": self.floor_type_id,
            "type": self.type_name,
            "user_modified": self.user_modified,
            "name": self.name,
            "enabled": self.enabled,
        }
        if self.geometry is not None:
            payload["geometry"] = self.geometry.to_geojson()
        else:
            payload["room_id"] = self.room_id
        return payload


@dataclass(frozen=True)
class SetFloorTypes:
    floor_types: list[FloorTypeEntry]

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_floor_types",
            "params": {"floor_types": [f.to_geojson() for f in self.floor_types]},
        }


@dataclass(frozen=True)
class ThresholdEntry:
    threshold_id: str
    status: str
    geometry: Polygon

    def to_geojson(self) -> dict[str, Any]:
        return {"id": self.threshold_id, "status": self.status, "geometry": self.geometry.to_geojson()}


@dataclass(frozen=True)
class SetThresholds:
    thresholds: list[ThresholdEntry]

    def to_command_body(self) -> dict[str, Any]:
        return {
            "command": "set_thresholds",
            "params": {"thresholds": [t.to_geojson() for t in self.thresholds]},
        }


MapEditCommand = (
    SetRoomMetadata
    | MergeRooms
    | SplitRoom
    | SetRoomType
    | SetKeepOutZones
    | AddCleanZones
    | DeleteCleanZones
    | SetFurniture
    | RevertUserEdits
    | SetFloorTypes
    | SetThresholds
)


# --- Live-Karte/-Position (GET /v1/p2maps/livemap) ----------------------
#
# Siehe docs/FINDINGS_2026-07-11.md Abschnitt 2 fuer die vollstaendige
# Herleitung. cur_path ist ein flaches JSON-Array:
# [seq_nr, x1,y1,orient1,mode1, x2,y2,orient2,mode2, ..., epoch_ts]


@dataclass(frozen=True)
class LiveMapStreamInit:
    """Antwort auf GET /v1/p2maps/livemap?robotId={blid}."""

    mqtt_topic: str
    initial_map_url: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LiveMapStreamInit":
        return cls(mqtt_topic=data["mqtt_topic"], initial_map_url=data.get("livemap_url"))


@dataclass(frozen=True)
class PositionSample:
    point: Position
    orientation: float
    operating_modes: int


@dataclass(frozen=True)
class PositionUpdateMessage:
    """Eine Nachricht auf dem livemap-Topic mit Positionsdaten. Mehrere
    Punkte pro Nachricht sind normal (trajektorienartig, siehe FINDINGS)."""

    sequence_number: int
    updates: list[PositionSample]
    last_update_timestamp: datetime

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PositionUpdateMessage":
        """data ist die "pos_update"-Huelle inkl. cur_path.

        cur_path-Laenge muss (2 + 4*n) sein fuer n Positionspunkte --
        genau wie in PositionUpdatesSerializer.deserialize() geprueft.
        Orientierung wird, wie im Original, um +pi verschoben -- Grund
        fuer diese Konvention ist nicht weiter untersucht.
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
            last_update_timestamp=datetime.fromtimestamp(epoch_ts, tz=timezone.utc),
        )


@dataclass(frozen=True)
class MapUpdateMessage:
    """Die andere Nachrichtenform auf dem livemap-Topic: ein neues
    Karten-Bild ist verfuegbar, kein Positions-Update."""

    livemap_url: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MapUpdateMessage":
        return cls(livemap_url=data["map_update"]["livemap_url"])


def parse_livemap_message_data(data: dict[str, Any]) -> PositionUpdateMessage | MapUpdateMessage:
    """Kernlogik, operiert auf bereits geparstem JSON (dict). Fuer
    parse_livemap_message() (rohe Bytes) UND fuer prime_robot.py's
    watch_live_map() (bekommt den Payload schon als dict von
    mqtt_client.py's ShadowResponse -- Neuserialisieren waere unnoetig)."""
    if "pos_update" in data:
        return PositionUpdateMessage.from_json(data["pos_update"])
    if "map_update" in data:
        return MapUpdateMessage.from_json(data)
    msg = f"Unrecognized livemap message shape: keys={list(data.keys())}"
    raise ValueError(msg)


def parse_livemap_message(raw_payload: bytes) -> PositionUpdateMessage | MapUpdateMessage:
    """Entscheidet anhand der vorhandenen Keys, welche der zwei
    Nachrichtenformen vorliegt (siehe FINDINGS Abschnitt 2, Punkt 3)."""
    return parse_livemap_message_data(json.loads(raw_payload))


# =========================================================================
# Lese-Modelle: was tatsaechlich IN einer Karte steckt
# =========================================================================
#
# STATUS: Neu, deutlich UNSICHERER als die Editier-Kommandos oben.
#
# Die Editier-Kommandos (SetRoomMetadata, SplitRoom, etc.) sind
# @Serializable-Kotlin-Klassen mit expliziten JsonObjectBuilder-
# Serialisierern -- das Wire-JSON-Format war dort direkt aus dem
# Serialisierungscode ablesbar.
#
# Diese Lese-Modelle hier (com.irobot.irobotdata.maps.domainmodels.
# p2maps.bundlecontents.*) sind PLAIN Kotlin data classes OHNE
# sichtbare @Serializable/@SerialName-Annotationen -- sie werden
# vermutlich ueber einen separaten Bundle-Unpacking-Mechanismus
# (P2MapBundleContentHolder / P2MapInfoFactory) aus einem rohen Format
# befuellt, dessen genaue Wire-Struktur NICHT Teil der heutigen Analyse
# war. Die Feldnamen hier sind die Kotlin-Property-Namen -- eine
# plausible, aber NICHT auf JSON-Ebene bestaetigte Annahme fuer die
# tatsaechlichen Schluessel.
#
# WICHTIGER, BEWUSST OFFENER PUNKT: Diese Klassen sind einzeln modelliert,
# aber es gibt noch KEINEN Parser, der eine komplette get_map_metadata()/
# fetchPersistentMap()-Antwort in diese Typen zerlegt -- das
# Gesamt-Umschlagformat (wie P2MapBundleContentHolder die einzelnen
# "infoType"-Diskriminatoren wie "rooms", "borders", "hazard",
# "trajectories", "coverage", "dockPoses", "furniture",
# "adHocCleanZones", "cleanZones" zu einer Antwort zusammenfasst) wurde
# heute nicht untersucht. get_map_metadata() in rest_client.py gibt
# weiterhin rohes, ungeparste JSON zurueck.


class RoomTypeSource(str, Enum):
    """Bestaetigt aus P2MapRoomInfo$RoomType$Source -- WIE ein Raumtyp
    zustande kam (erkannt vs. vom Nutzer gesetzt). Exakte String-Werte
    nicht 1:1 bestaetigt (Enum-Namen ja, Wire-String-Serialisierung nicht
    explizit im Code gesehen) -- hier als Platzhalter mit den Enum-Namen
    selbst befuellt, nicht als bestaetigte Wire-Strings."""

    DETECTED = "DETECTED"
    USER_SET = "USER_SET"


@dataclass(frozen=True)
class RoomInfo:
    """Bestaetigt aus P2MapRoomInfo (Lese-Modell -- nicht zu verwechseln
    mit SetRoomMetadata, dem Editier-Kommando oben)."""

    room_id: str
    geometry: Polygon
    name: str | None = None
    simplified_geometry: Polygon | None = None
    room_type: RoomType | None = None
    adjacent_room_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BorderInfo:
    """Bestaetigt aus P2MapBorderInfo: nur eine MultiPolygon-Geometrie,
    kein id-Feld."""

    geometry: MultiPolygon


@dataclass(frozen=True)
class TrajectoryInfo:
    """Bestaetigt aus P2MapTrajectoryInfo. operating_modes: rohe Werte
    aus P2MapOperatingModes.OperatingMode -- dessen genaue Werte heute
    nicht gefunden, daher als rohe Strings/Ints durchgereicht statt
    eines eigenen Enums."""

    geometry: LineString
    index: int | None = None
    operating_modes: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class CoverageInfo:
    """Bestaetigt aus P2MapCoverageInfo."""

    geometry: MultiPolygon
    operating_modes: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class DockInfo:
    """Bestaetigt aus P2MapDockInfo -- Position als Point, nicht Polygon."""

    geometry: Point
    orientation: float | None = None


class HazardType(str, Enum):
    """Bestaetigt aus P2MapHazardInfo$HazardType, vollstaendige Liste."""

    UNKNOWN = "UNKNOWN"
    BAR_STOOL = "BAR_STOOL"
    BLANKET = "BLANKET"
    CABLES = "CABLES"
    CAT = "CAT"
    DOG = "DOG"
    DRY_DEBRIS = "DRY_DEBRIS"
    LIQUID = "LIQUID"
    OTHER_TOYS = "OTHER_TOYS"
    PERSON = "PERSON"
    PET_WASTE = "PET_WASTE"
    PURSE = "PURSE"
    SHOES = "SHOES"
    SOCKS = "SOCKS"
    TRASH_CAN = "TRASH_CAN"
    WEIGHING_SCALE = "WEIGHING_SCALE"


@dataclass(frozen=True)
class HazardInfo:
    """Bestaetigt aus P2MapHazardInfo -- Position als Point."""

    hazard_id: str
    hazard_type: HazardType
    geometry: Point


@dataclass(frozen=True)
class NoMopZoneInfo:
    """Bestaetigt aus P2MapNoMopZoneInfo: nur geometry + id."""

    zone_id: str
    geometry: Polygon


@dataclass(frozen=True)
class AdHocCleanZoneInfo:
    """Bestaetigt aus P2MapAdHocCleanZoneInfo: nur geometry + id."""

    zone_id: str
    geometry: Polygon


@dataclass(frozen=True)
class KeepOutZoneInfoRead:
    """Bestaetigt aus P2MapKeepOutZoneInfo (Lese-Modell). Absichtlich
    ...Read benannt, um Verwechslung mit dem gleichnamigen
    Editier-Konzept (KeepOutZone oben, Teil von SetKeepOutZones) zu
    vermeiden -- dort Linear/Rectangle-Unterscheidung, hier nur
    geometry + id."""

    zone_id: str
    geometry: Polygon


@dataclass(frozen=True)
class VirtualWallInfo:
    """Bestaetigt aus P2MapVirtualWallInfo: LineString statt Polygon."""

    wall_id: str
    geometry: LineString


@dataclass(frozen=True)
class CleanZoneInfoRead:
    """Bestaetigt aus P2MapCleanZoneInfo (Lese-Modell, mit name --
    anders als die uebrigen einfachen Zonen). ...Read benannt aus
    demselben Grund wie KeepOutZoneInfoRead."""

    zone_id: str
    name: str | None
    geometry: Polygon


@dataclass(frozen=True)
class FurnitureInfoRead:
    """Bestaetigt aus P2MapFurnitureInfo (Lese-Modell) -- hat ZWEI
    Felder mehr als das Editier-Kommando SetFurniture/Furniture oben
    (orientation, cleaning_area). Das war ein Fehler in einer frueheren
    Version dieser Analyse: dort faelschlich als fehlend im
    Editier-Kommando gemeldet -- tatsaechlich gehoeren diese Felder nur
    hierher, ins Lese-Modell (bestaetigt gegen EditMapV2Request.
    Furniture's Serializer, der wirklich nur id/type/userModified/
    geometry sendet)."""

    furniture_id: str
    geometry: Polygon
    furniture_type: FurnitureType
    user_edited: bool
    orientation: float
    cleaning_area: Polygon | None = None


# =========================================================================
# Missionssteuerung (CLEAN/START/STOP/PAUSE/DOCK/etc.)
# =========================================================================
#
# STATUS: NEU (11. Juli, zweite Sitzung). Vorher als "strukturell harte
# native Grenze" eingestuft (siehe PRIME_APP_GAP_ANALYSIS_2026-07-11.md
# Punkt C1) -- das war nur zur Haelfte richtig. Der DISPATCH-Mechanismus
# (core::CommandTierAgentImpl::postCommand()) ist tatsaechlich nativ und
# bleibt unsichtbar. Aber das eigentliche PAYLOAD (RoutineCommand) ist
# eine ganz normale, @Serializable Kotlin-Klasse mit expliziten
# @SerialName-Annotationen -- also derselbe Vertrauensstand wie die
# p2maps-Editierbefehle oben, NICHT wie ein natives Raetsel.
#
# Transport bestaetigt via native Disassemblierung (aarch64-objdump):
# liblegacyCore.so enthaelt woertlich den Format-String
# "$aws/things/%s/shadow/update" (Adresse 0xde2a3a) -- Kommandos laufen
# ueber den bereits implementierten Shadow-update()-Pfad
# (mqtt_client.py), NICHT ueber einen separaten "cmd"-Topic (der war
# in frueheren Sitzungen bereits als Sackgasse bestaetigt -- passt
# zusammen).
#
# Payload-Huelle bestaetigt aus CommandWrapper.java (@Serializable,
# genau ein Feld, @SerialName("cmd")): state.desired.cmd = RoutineCommand.
#
# WEITERHIN OFFEN: der native postCommand()-Pfad selbst wurde nicht bis
# zum tatsaechlichen MQTT-Publish-Aufruf zurueckverfolgt (mehrere
# Indirektionsebenen ueber nicht-exportierte statische Funktionen ohne
# Symbolnamen -- mit den verfuegbaren Werkzeugen (objdump, kein echter
# Decompiler wie Ghidra/IDA) nicht wirtschaftlich weiter aufloesbar).
# Die HIER dokumentierte Huelle (shadow update, "cmd"-Schluessel) ist
# eine Kombination aus zwei unabhaengigen, aber nie GEMEINSAM live
# bestaetigten Fakten -- nie gegen einen echten Server gesendet.


class MissionCommandType(str, Enum):
    """Bestaetigt aus com.irobot.data.missioncommand.datamodels.
    CommandType -- Werte sind die tatsaechlichen @SerialName-Strings,
    NICHT die Kotlin-Enum-Konstantennamen (z.B. CLEAN_SPOT serialisiert
    als "point_clean", nicht "clean_spot")."""

    CLEAN = "clean"
    QUICK = "quick"
    SPOT = "spot"
    DOCK = "dock"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    WAKE = "wake"
    RESET = "reset"
    FIND = "find"
    WIPE = "wipe"
    IPDONE = "ipdone"
    PROVDONE = "provdone"
    RECHRG = "rechrg"
    TRAIN = "train"
    EVAC = "evac"
    STOPEVAC = "stopevac"
    QUERYDOCK = "querydock"
    TIDY = "tidy"
    VIEWPOINT = "viewpoint"
    STARTLOG = "startlog"
    SKIP = "skip"
    FLREFILL = "flrefill"
    WASHPAD = "washpad"
    DRYPAD = "drypad"
    STOPPADDRY = "stoppaddry"
    FLUSHSLUICE = "flushsluice"
    CLEAN_SPOT = "point_clean"
    START_CLEAN = "start_clean"


@dataclass(frozen=True)
class RoutineCommand:
    """Bestaetigt aus com.irobot.data.missioncommand.datamodels.
    RoutineCommand (@Serializable). Feldnamen-Zuordnung 1:1 aus den
    @SerialName-Annotationen im Quellcode, NICHT geraten:
      type -> "command", assetId -> "robot_id", mapId -> "p2map_id",
      cleanAll -> "select_all", idMultipolys -> "id_multipolys",
      pmapVersionId -> "user_p2mapv_id", spotGeometry -> "geom",
      favoriteId -> "favorite_id". ordered/params/regions haben KEINE
      eigene @SerialName -- serialisieren unter ihrem Property-Namen.

    KORRIGIERT (elfte Sitzung, per Gegenpruefung durch ha_roomba_plus):
    "ordered" ist KEIN Hinweis auf eine Sequenzierung mehrerer separat
    verschickter RoutineCommand-Objekte (z.B. aus einer FavoriteV1/
    Routine.commandDefs-Liste). ha_roomba_plus (jahrelang produktiv
    gegen echte Classic-Geraete verifiziert) nutzt "ordered" als
    INTRA-Command-Eigenschaft neben "regions" im selben Kommando-Objekt:
    ob die Regionen INNERHALB dieses einen Kommandos in gelisteter
    Reihenfolge angefahren werden sollen, oder der Roboter selbst
    optimieren darf. Die Frage, ob mehrere commandDefs-Eintraege
    tatsaechlich als separate, aufeinanderfolgende Kommandos verschickt
    werden, bleibt damit weiterhin UNGEKLAERT -- "ordered" ist dafuer
    kein Beleg.

    params/regions/id_multipolys als rohe dicts durchgereicht -- deren
    verschachtelte Struktur (CommandParams/Region/CommandPolygon) wurde
    heute nicht im Detail modelliert."""

    command_type: MissionCommandType
    asset_id: str
    map_id: str | None = None
    ordered: int = 0
    """Intra-Command-Eigenschaft (siehe Klassen-Docstring): 1 = Regionen
    in gelisteter Reihenfolge anfahren, 0 (vermutlich) = Roboter darf
    selbst optimieren. Bestaetigt aus ha_roomba_plus' produktivem
    Classic-Code, nicht aus Primes eigenen Quellen."""
    id_multipolys: list["CommandPolygon"] | list[dict[str, Any]] | None = None
    params: "CommandParams | dict[str, Any] | None" = None
    regions: list["Region"] | list[dict[str, Any]] | None = None
    pmap_version_id: str | None = None
    clean_all: bool = False
    spot_geometry: dict[str, Any] | None = None
    favorite_id: str | None = None
    initiator: str | None = None
    """NEU (25. Sitzung) -- bestaetigt aus echter Missionshistorie
    (chairstacker): Wire-Schluessel "initiator", beobachtete Werte
    "cloud" (zeitplangesteuert) und "rmtApp" (manuell per App
    ausgeloest). Kein @SerialName gefunden -- Property-Name direkt.
    Optional/None gelassen statt eines geratenen Default-Werts, da
    unklar ist, was der Server bei fehlendem Feld annimmt."""

    def to_json(self) -> dict[str, Any]:
        """NEU (11. Juli, achte Sitzung): id_multipolys/params/regions
        akzeptieren jetzt entweder die bytecode-bestaetigten Typen
        (CommandPolygon/CommandParams/Region, siehe unten im Modul) oder
        weiterhin rohe dicts (Abwaertskompatibilitaet/Fluchtluke fuer
        Faelle, die die typisierten Modelle nicht abdecken)."""
        body: dict[str, Any] = {
            "command": self.command_type.value,
            "robot_id": self.asset_id,
            "ordered": self.ordered,
            "select_all": self.clean_all,
        }
        if self.map_id is not None:
            body["p2map_id"] = self.map_id
        if self.id_multipolys is not None:
            body["id_multipolys"] = [
                p.to_json() if hasattr(p, "to_json") else p for p in self.id_multipolys
            ]
        if self.params is not None:
            body["params"] = self.params.to_json() if hasattr(self.params, "to_json") else self.params
        if self.regions is not None:
            body["regions"] = [r.to_json() if hasattr(r, "to_json") else r for r in self.regions]
        if self.pmap_version_id is not None:
            body["user_p2mapv_id"] = self.pmap_version_id
        if self.spot_geometry is not None:
            body["geom"] = self.spot_geometry
        if self.favorite_id is not None:
            body["favorite_id"] = self.favorite_id
        if self.initiator is not None:
            body["initiator"] = self.initiator
        return body

    def to_shadow_desired(self) -> dict[str, Any]:
        """Bestaetigt aus CommandWrapper.java (@Serializable, ein
        Feld, @SerialName("cmd")): das ist, was in
        state.desired.cmd landen sollte, wenn die Huellen-Annahme
        (siehe Modul-Docstring) stimmt -- NIE live bestaetigt."""
        return {"cmd": self.to_json()}


# =========================================================================
# V1-Editier-Kommandos (POST /v1/p2maps/{id}/versions) -- TATSAECHLICH
# AKTIVER PFAD, nicht die oben modellierten V2-Kommandos
# =========================================================================
#
# STATUS: NEU (11. Juli, vierte Sitzung, nach voller Neu-Dekompilierung
# der App -- siehe PRIME_APP_GAP_ANALYSIS). Bestaetigt: JEDE einzelne
# Editier-Operation im App-Code (Raum, Zone, Moebel, virtuelle Wand)
# ruft requestEditV1() auf. requestEditV2() wird im gesamten App-Code
# KEIN EINZIGES MAL aufgerufen -- nur in Signaturen referenziert. Die
# oben modellierten V2-Kommandos (SplitRoom, MergeRooms, etc. mit
# to_command_body(), Endpunkt /v2/p2maps/{id}/versions) sind fuer einen
# Pfad gebaut, den die App selbst nicht benutzt. Sie bleiben im Code,
# da /v2/... immerhin existiert (toter Pfad, kein erfundener), aber
# edit_map() in rest_client.py nutzt ab jetzt V1.
#
# Feldnamen bestaetigt via androguard-Bytecode-Inspektion direkt aus der
# DEX (jadx scheiterte an dieser einen Klassenfamilie -- alle 56 von 56
# Dekompilierungsfehlern der GESAMTEN App liegen genau hier, sonst kein
# einziger Fehler in ueber 24.000 Klassen).
#
# WICHTIGE UNSICHERHEIT: das genaue Envelope-Format (wie der
# "Command"-Diskriminator auf die Leitung kommt) ist NICHT bestaetigt.
# EditMapV1Request$Command$CommandSerializer ist ein eigener, custom
# Serializer (kein Standard-Sealed-Class-Polymorphismus per
# kotlinx.serialization), dessen Logik nicht dekompiliert werden konnte
# (auch nicht via androguard -- das braeuchte Bytecode-Disassemblierung
# der Serializer-Methode selbst, nicht nur Feldlisten). Die hier
# gebaute to_v1_command_body()-Form ({"command": "<Name>", ...Felder
# direkt, kein "params"-Nesting...}) ist eine Analogie-Annahme aus V2s
# bestaetigtem Muster (dort aber "params" verschachtelt!) -- KEINE
# bestaetigte Tatsache fuer V1. Kein @SerialName auf einem einzigen der
# gefundenen Felder -- Wire-Schluessel vermutlich identisch zu den
# Kotlin-Property-Namen, aber auch das ueber den custom Serializer
# theoretisch ueberschreibbar.


@dataclass(frozen=True)
class RenameRoomV1:
    """Bestaetigt (Felder) aus EditMapV1Request$Command$RenameRoom via
    androguard: id (String), name (String)."""

    room_id: str
    name: str

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "RenameRoom", "id": self.room_id, "name": self.name}


@dataclass(frozen=True)
class SplitRoomV1:
    """Bestaetigt: id (String), splitPoints (List) -- anders als V2s
    SplitRoom (das eine LineString-Geometrie nimmt), hier eine simple
    Punktliste. Genaue Bedeutung von "splitPoints" (zwei Endpunkte wie
    V2? oder mehr?) nicht weiter bestaetigt."""

    room_id: str
    split_points: list[Position]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "SplitRoom",
            "id": self.room_id,
            "splitPoints": [list(p) for p in self.split_points],
        }


@dataclass(frozen=True)
class MergeRoomsV1:
    """Bestaetigt: ids (List) -- Feldname "ids", nicht "roomIds"."""

    ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "MergeRooms", "ids": self.ids}


@dataclass(frozen=True)
class SetRoomTypeV1:
    """Bestaetigt: id (String), type (V1s EIGENE RoomType-Enum-Klasse --
    getrennt von der oben definierten RoomType, aber mit denselben
    numerischen Werten: NOT_RECOGNIZED, BEDROOM, DINING_ROOM, BATHROOM,
    HALLWAY, KITCHEN, LIVING_ROOM, BALCONY, OTHER -- die bereits
    vorhandene RoomType-IntEnum wird hier wiederverwendet)."""

    room_id: str
    room_type: RoomType

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "SetRoomType", "id": self.room_id, "type": int(self.room_type)}


@dataclass(frozen=True)
class SetRoomMetadataV1:
    """Bestaetigt: einziges Feld "metadata": P2MapRoomMetadata (der
    Lese-Modell-Typ, siehe RoomInfo-Abschnitt oben) -- ruft dieselbe
    Struktur wie set_room_metadata (V2) auf, aber ohne "params"-Nesting
    und mit dem gesamten Objekt unter "metadata" statt getrennten
    id/metadata-Feldern."""

    room_id: str
    name: str | None = None
    room_type: RoomType | None = None

    def to_v1_command_body(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"id": self.room_id}
        if self.name is not None:
            metadata["name"] = self.name
        if self.room_type is not None:
            metadata["type"] = int(self.room_type)
        return {"command": "SetRoomMetadata", "metadata": metadata}


@dataclass(frozen=True)
class PermanentAreaV1:
    """Bestaetigt aus EditMapV1Request$PermanentArea: geometry (Polygon),
    id (String), name (String)."""

    area_id: str
    name: str
    geometry: Polygon

    def to_json(self) -> dict[str, Any]:
        return {"id": self.area_id, "name": self.name, "geometry": self.geometry.to_geojson()}


@dataclass(frozen=True)
class SetPermanentAreasV1:
    """Bestaetigt: einziges Feld "areaPoints" (List) -- Name deutet auf
    Punktlisten hin, aber der Feldtyp (List<PermanentArea> vs. reine
    Positionslisten) wurde nicht bytecode-seitig aufgeloest. Hier als
    Liste von PermanentAreaV1-Objekten modelliert (plausibelste Lesart
    angesichts der separat existierenden PermanentArea-Klasse), NICHT
    bestaetigt."""

    areas: list[PermanentAreaV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "SetPermanentAreas", "areaPoints": [a.to_json() for a in self.areas]}


@dataclass(frozen=True)
class DeletePermanentAreasV1:
    """Bestaetigt: areaIDs (List)."""

    area_ids: list[str]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "DeletePermanentAreas", "areaIDs": self.area_ids}


@dataclass(frozen=True)
class VirtualWallLinearV1:
    """Bestaetigt aus EditMapV1Request$VirtualWall$Linear: from/to
    (Position), id (String) -- ein Linienstueck, kein Polygon."""

    wall_id: str
    from_pos: Position
    to_pos: Position

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "Linear",
            "id": self.wall_id,
            "from": list(self.from_pos),
            "to": list(self.to_pos),
        }


@dataclass(frozen=True)
class VirtualWallRectangleV1:
    """Bestaetigt aus EditMapV1Request$VirtualWall$Rectangle: id
    (String), polygon (Polygon) -- trotz des Namens "Rectangle" als
    allgemeines Polygon gespeichert, keine eigene Rechteck-Struktur."""

    wall_id: str
    polygon: Polygon

    def to_json(self) -> dict[str, Any]:
        return {"type": "Rectangle", "id": self.wall_id, "polygon": self.polygon.to_geojson()}


@dataclass(frozen=True)
class VirtualWallNoMopZoneV1:
    """Bestaetigt aus EditMapV1Request$VirtualWall$NoMopZone: id
    (String), polygon (Polygon). WICHTIGER FUND: No-Mop-Zonen laufen in
    V1 ueber denselben Kommandotyp wie virtuelle Waende
    (SetVirtualWalls), nicht ueber ein eigenes Kommando."""

    wall_id: str
    polygon: Polygon

    def to_json(self) -> dict[str, Any]:
        return {"type": "NoMopZone", "id": self.wall_id, "polygon": self.polygon.to_geojson()}


VirtualWallV1 = VirtualWallLinearV1 | VirtualWallRectangleV1 | VirtualWallNoMopZoneV1


@dataclass(frozen=True)
class SetVirtualWallsV1:
    """Bestaetigt: einziges Feld "walls" (List von VirtualWall-
    Untertypen). Wie der "type"-Diskriminator der drei Untertypen
    (Linear/Rectangle/NoMopZone) tatsaechlich auf die Leitung kommt, ist
    NICHT bestaetigt (eigener VirtualWallSerializer gefunden, dessen
    Logik nicht dekompiliert werden konnte) -- "type"-Schluessel hier
    als plausibelste Annahme genutzt."""

    walls: list[VirtualWallV1]

    def to_v1_command_body(self) -> dict[str, Any]:
        return {"command": "SetVirtualWalls", "walls": [w.to_json() for w in self.walls]}


@dataclass(frozen=True)
class FurnitureItemV1:
    """Bestaetigt aus EditMapV1Request$Furniture: geometry (Polygon), id
    (String), type (Int -- NICHT String/Enum wie bei V2s Furniture!),
    userModified (bool). Nutzt die vorhandene FurnitureType-IntEnum fuer
    den int-Wert."""

    furniture_id: str
    furniture_type: FurnitureType
    geometry: Polygon
    user_modified: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.furniture_id,
            "type": int(self.furniture_type),
            "geometry": self.geometry.to_geojson(),
            "userModified": self.user_modified,
        }


@dataclass(frozen=True)
class AdjustFurnitureV1:
    """Bestaetigt aus EditMapV1Request$Command$AdjustFurniture:
    furnitureList (List), packageInfo (List), timeStamp (long). Eine
    BATCH-Operation (mehrere Moebelstuecke auf einmal), anders als V2s
    SetFurniture (ein Stueck pro Aufruf). Bedeutung von "packageInfo"
    nicht bestaetigt -- hier als rohe Liste durchgereicht."""

    furniture_list: list[FurnitureItemV1]
    package_info: list[dict[str, Any]] = field(default_factory=list)
    timestamp: int = 0

    def to_v1_command_body(self) -> dict[str, Any]:
        return {
            "command": "AdjustFurniture",
            "furnitureList": [f.to_json() for f in self.furniture_list],
            "packageInfo": self.package_info,
            "timeStamp": self.timestamp,
        }


MapEditCommandV1 = (
    RenameRoomV1
    | SplitRoomV1
    | MergeRoomsV1
    | SetRoomTypeV1
    | SetRoomMetadataV1
    | SetPermanentAreasV1
    | DeletePermanentAreasV1
    | SetVirtualWallsV1
    | AdjustFurnitureV1
)


# =========================================================================
# Favoriten (FavoriteV1) -- POST/GET/PUT/DELETE /v1/user/favorites
# =========================================================================
#
# STATUS: NEU (11. Juli, vierte Sitzung). Feldnamen und @SerialName-
# Werte bestaetigt aus com.irobot.data.restservices.favorites.
# datamodels.FavoriteV1 (@Serializable, sauber dekompiliert). Wichtiger
# Fund: commandDefs ist eine List<RoutineCommand> -- ein Favorit ist
# also strukturell nichts anderes als eine benannte, gespeicherte
# Liste von Missionskommandos (siehe RoutineCommand oben). Das deckt
# sich mit der laengst bekannten "len(commanddefs) > 1"-Beobachtung aus
# der HA-Integration (FavoriteButton.async_press()).
#
# REST-Endpunkte bestaetigt aus FavoriteCommonRequest.java (Basis-URL)
# und den drei separat existierenden Subklassen (Delete/Fetch/Order --
# HTTP-Methode dort explizit gesetzt):
#   GET    /v1/user/favorites?app_edition=1                    (fetch)
#   POST   /v1/user/favorites?app_edition=1                     (create, ANGENOMMEN -- die konkrete
#                                                                 Lambda-Klasse dafuer hat jadx
#                                                                 stillschweigend nicht ausgegeben,
#                                                                 kein Fehler dafuer gemeldet)
#   PUT    /v1/user/favorites/{favoriteId}?app_edition=1        (update, ebenfalls ANGENOMMEN)
#   DELETE /v1/user/favorites/{favoriteId}?app_edition=1        (delete, BESTAETIGT)
#   PUT    /v1/user/favorites/{favoriteId}/order?app_edition=1  (order, BESTAETIGT)
#
# app_edition=1 ist ein fixer Query-Parameter (NotificationCenterConsts
# .NOTIFICATION_HELP_CONTENT_VERSION1 = "1"), kein Nutzer-Wert.


class TimeEstimateConfidence(str, Enum):
    """Bestaetigt aus TimeEstimateConfidence, vollstaendige Liste."""

    GOOD_CONFIDENCE = "GOOD_CONFIDENCE"
    POOR_CONFIDENCE = "POOR_CONFIDENCE"
    PARTIAL_CONFIDENCE = "PARTIAL_CONFIDENCE"


class TimeEstimateTimeUnit(str, Enum):
    """Bestaetigt aus TimeEstimateTimeUnit -- sowohl Singular- als auch
    Pluralform existieren als eigene Werte (keine Fehlerkorrektur
    meinerseits, so im Quellcode)."""

    HOUR = "hour"
    HOURS = "hours"
    MINUTE = "minute"
    MINUTES = "minutes"
    SECOND = "second"
    SECONDS = "seconds"


@dataclass(frozen=True)
class FavoriteTimeEstimate:
    """Bestaetigt via androguard-Bytecode-Inspektion (Basisklasse selbst
    von jadx nicht ausgegeben, kein Fehler dafuer gemeldet -- aehnlicher
    stiller Ausfall wie bei den createFavorite/updateFavorite-Lambdas):
    confidence (TimeEstimateConfidence), estimate (double), unit
    (TimeEstimateTimeUnit). Keine @SerialName-Abweichung fuer die
    Feldnamen selbst gefunden -- vermutlich direkt unter ihrem
    Property-Namen serialisiert."""

    estimate: float
    unit: TimeEstimateTimeUnit
    confidence: TimeEstimateConfidence

    def to_json(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "unit": self.unit.value,
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class FavoriteV1:
    """Bestaetigt aus FavoriteV1.java (@Serializable, sauber
    dekompiliert). Feldnamen-Zuordnung aus den @SerialName-Annotationen:
      commandDefs -> "commanddefs" (List<RoutineCommand> -- siehe oben),
      creationTimestamp -> "creation_timestamp",
      displayOrder -> "display_order", favoriteId -> "favorite_id",
      lastModified -> "last_modified",
      lastUserModified -> "last_user_modified",
      modificationSecs -> "modification_secs",
      timeEstimates -> "time_estimates", isDefault -> "default",
      isDeleted -> "deleted", isHidden -> "hidden". color/icon/name/
      order/version haben KEINE eigene @SerialName -- Property-Name
      direkt uebernommen."""

    name: str | None = None
    color: str | None = None
    icon: str | None = None
    order: str | None = None
    display_order: int | None = None
    is_default: bool = False
    is_deleted: bool = False
    is_hidden: bool = False
    modification_secs: str | None = None
    version: str | None = None
    command_defs: list[RoutineCommand] = field(default_factory=list)
    creation_timestamp: int | None = None
    last_user_modified: int | None = None
    last_modified: int | None = None
    time_estimates: list[FavoriteTimeEstimate] | None = None
    favorite_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "default": self.is_default,
            "deleted": self.is_deleted,
            "hidden": self.is_hidden,
            "commanddefs": [c.to_json() for c in self.command_defs],
        }
        if self.name is not None:
            body["name"] = self.name
        if self.color is not None:
            body["color"] = self.color
        if self.icon is not None:
            body["icon"] = self.icon
        if self.order is not None:
            body["order"] = self.order
        if self.display_order is not None:
            body["display_order"] = self.display_order
        if self.modification_secs is not None:
            body["modification_secs"] = self.modification_secs
        if self.version is not None:
            body["version"] = self.version
        if self.creation_timestamp is not None:
            body["creation_timestamp"] = self.creation_timestamp
        if self.last_user_modified is not None:
            body["last_user_modified"] = self.last_user_modified
        if self.last_modified is not None:
            body["last_modified"] = self.last_modified
        if self.time_estimates is not None:
            body["time_estimates"] = [t.to_json() for t in self.time_estimates]
        return body


# =========================================================================
# Kartenbuendel entpacken (tar.gz -> rohe Pro-Typ-JSON-Listen)
# =========================================================================
#
# STATUS: NEU (11. Juli, fuenfte Sitzung). Schliesst einen Teil von C2/C3
# (siehe PRIME_APP_GAP_ANALYSIS): bisher gab es einen Weg, an die
# vorsignierte Download-URL zu kommen (get_map_geojson_link()) und einen
# Weg, die Bytes herunterzuladen (download_map_bundle()), aber nichts,
# das das tar.gz-Archiv tatsaechlich entpackt und den einzelnen Dateien
# eine Bedeutung zuordnet.
#
# 11 von 15 bekannten Info-Typ-Diskriminatoren bestaetigt (P2MapInfoType-
# Konstanten aus dem Quellcode): "rooms", "borders", "floorPlan",
# "dockPoses", "floorTypes", "coverage", "cleanZones", "hazard",
# "trajectories", "adHocCleanZones", "furniture". VIER FEHLEN
# ("keepOutZones"/"noMopZones"/"virtualWalls"/"thresholds" haben in den
# entsprechenden Klassen KEIN eigenes P2MapInfoType-Feld gefunden --
# vermutlich anders eingebettet, z.B. unter einem gemeinsamen
# "zones"-Diskriminator, nicht weiter untersucht).
#
# WICHTIGE UNSICHERHEIT: wie die Dateien INNERHALB des tar.gz tatsaechlich
# heissen (z.B. "rooms.json" vs. "rooms" vs. etwas komplett anderes) ist
# NICHT bestaetigt -- P2MapBundleContentHolder/P2MapInfoFactory (die
# Klassen, die das Archiv oeffnen) wurden nicht im Detail durchleuchtet.
# parse_map_bundle() unten geht davon aus, dass der Dateiname (ohne
# Endung) direkt einem der obigen Diskriminatoren entspricht -- eine
# plausible, aber ungetestete Annahme. Nie gegen ein echtes Archiv
# ausprobiert.


KNOWN_BUNDLE_INFO_TYPES = frozenset({
    "rooms", "borders", "floorPlan", "dockPoses", "floorTypes",
    "coverage", "cleanZones", "hazard", "trajectories",
    "adHocCleanZones", "furniture",
})


def parse_map_bundle(data: bytes) -> dict[str, Any]:
    """Entpackt ein von download_map_bundle() geladenes tar.gz-Archiv.

    Gibt {dateiname_ohne_endung: geparster_inhalt} zurueck --
    geparster_inhalt ist rohes JSON (dict oder list), wenn die Datei als
    JSON lesbar war, sonst der rohe Text, sonst die rohen Bytes (falls
    weder Text noch JSON -- z.B. ein Bild oder Binaerformat innerhalb
    des Archivs, das nicht weiter untersucht wurde).

    Bewusst KEINE automatische Umwandlung in die RoomInfo/BorderInfo/
    etc.-Dataclasses oben -- das exakte JSON-Feldformat innerhalb jeder
    Datei ist nicht bestaetigt (nur die Kotlin-Klassenfelder sind es),
    eine automatische Zuordnung wuerde stillschweigend falsche Annahmen
    treffen koennen. Aufrufer, die Zugriff auf die typisierten Modelle
    wollen, muessen die rohen dicts hier selbst in RoomInfo(**...) o.ae.
    umwandeln und dabei die eigene Unsicherheit im Blick behalten."""
    result: dict[str, Any] = {}
    with tarfile.open(fileobj=BytesIO(data), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            raw = extracted.read()
            # Dateiname ohne Verzeichnispfad und ohne Endung als Schluessel
            key = member.name.rsplit("/", 1)[-1]
            if "." in key:
                key = key.rsplit(".", 1)[0]
            try:
                result[key] = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                try:
                    result[key] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    result[key] = raw
    return result


# =========================================================================
# Zeitplaene (households/settings/schedule)
# =========================================================================
#
# STATUS: NEU (11. Juli, siebte Sitzung). ScheduleOptions/HouseholdSchedule/
# HouseholdScheduleUpdate/ScheduleTime existieren NICHT im jadx-Ausgabebaum
# -- wie schon bei EditMapV1Request und den Favorite-create/update-Lambdas
# hat jadx sie stillschweigend uebersprungen, OHNE das in der Fehlerzahl
# zu zeigen. Alle Felder unten via androguard direkt aus der DEX bestaetigt.
# ScheduleDateEntry und ScheduleFrequency dagegen decompilierten normal
# (jadx-Quelle, @SerialName direkt sichtbar).


class ScheduleFrequency(str, Enum):
    """Bestaetigt (jadx-Quelle, @SerialName pro Wert, identisch zum
    Enum-Namen): nur 4 Werte, kein DAILY."""

    BI_WEEKLY = "BI_WEEKLY"
    MONTHLY = "MONTHLY"
    ONCE = "ONCE"
    WEEKLY = "WEEKLY"


@dataclass(frozen=True)
class ScheduleTime:
    """Bestaetigt (androguard): day (List -- Wochentage, Typ des
    Listeninhalts nicht ueber Bytecode-Feldsignatur auflösbar, vermutlich
    Int oder String-Kuerzel wie "MO"/"TU"), hour (Integer), min
    (Integer)."""

    day: list[Any] = field(default_factory=list)
    hour: int | None = None
    min: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"day": self.day}
        if self.hour is not None:
            body["hour"] = self.hour
        if self.min is not None:
            body["min"] = self.min
        return body


@dataclass(frozen=True)
class ScheduleDateEntry:
    """Bestaetigt (jadx-Quelle, @SerialName pro Feld, identisch zum
    Property-Namen): dayOfMonth, hour, min, month, year -- genutzt fuer
    ScheduleOptions.after/until (Start-/Enddatum eines Zeitplans)."""

    day_of_month: int | None = None
    hour: int | None = None
    min: int | None = None
    month: int | None = None
    year: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.day_of_month is not None:
            body["dayOfMonth"] = self.day_of_month
        if self.hour is not None:
            body["hour"] = self.hour
        if self.min is not None:
            body["min"] = self.min
        if self.month is not None:
            body["month"] = self.month
        if self.year is not None:
            body["year"] = self.year
        return body


@dataclass(frozen=True)
class ScheduleOptions:
    """Bestaetigt (androguard, alle 17 Felder -- kein @SerialName
    gefunden, Wire-Schluessel vermutlich = Kotlin-Property-Name direkt):
    assetId, name, frequency, start/end (ScheduleTime), after/until
    (ScheduleDateEntry), commands/endCommands/append/exclude (Listen),
    createdTime, deleted, enabled, forceCloud, reminder.

    UNSICHERHEIT: commands/endCommands sind ueber die rohe Bytecode-
    Feldsignatur nur als "List" erkennbar (Java-Generics-Typloeschung
    zur Laufzeit) -- hier als List[RoutineCommand] modelliert, in
    starker Analogie zu FavoriteV1.command_defs (dasselbe Muster:
    ein Zeitplan loest beim Ausloesen ein RoutineCommand aus), aber
    NICHT direkt ueber eine generische Signatur bestaetigt. append/
    exclude aehnlich unsicher (Inhalt unbekannt, hier als rohe Liste
    belassen)."""

    asset_id: str | None = None
    name: str | None = None
    frequency: ScheduleFrequency | None = None
    start: ScheduleTime | None = None
    end: ScheduleTime | None = None
    after: ScheduleDateEntry | None = None
    until: ScheduleDateEntry | None = None
    commands: list[RoutineCommand] = field(default_factory=list)
    end_commands: list[RoutineCommand] = field(default_factory=list)
    append: list[Any] = field(default_factory=list)
    exclude: list[Any] = field(default_factory=list)
    created_time: str | None = None
    deleted: bool | None = None
    enabled: bool | None = None
    force_cloud: bool | None = None
    reminder: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.asset_id is not None:
            body["assetId"] = self.asset_id
        if self.name is not None:
            body["name"] = self.name
        if self.frequency is not None:
            body["frequency"] = self.frequency.value
        if self.start is not None:
            body["start"] = self.start.to_json()
        if self.end is not None:
            body["end"] = self.end.to_json()
        if self.after is not None:
            body["after"] = self.after.to_json()
        if self.until is not None:
            body["until"] = self.until.to_json()
        if self.commands:
            body["commands"] = [c.to_json() for c in self.commands]
        if self.end_commands:
            body["endCommands"] = [c.to_json() for c in self.end_commands]
        if self.append:
            body["append"] = self.append
        if self.exclude:
            body["exclude"] = self.exclude
        if self.created_time is not None:
            body["createdTime"] = self.created_time
        if self.deleted is not None:
            body["deleted"] = self.deleted
        if self.enabled is not None:
            body["enabled"] = self.enabled
        if self.force_cloud is not None:
            body["forceCloud"] = self.force_cloud
        if self.reminder is not None:
            body["reminder"] = self.reminder
        return body


@dataclass(frozen=True)
class HouseholdSchedule:
    """Bestaetigt (androguard): scheduleId (String), options
    (ScheduleOptions). Wird laut SchedulesAPI fuer updateSchedules()
    genutzt (List<HouseholdSchedule>)."""

    schedule_id: str
    options: ScheduleOptions

    def to_json(self) -> dict[str, Any]:
        return {"scheduleId": self.schedule_id, "options": self.options.to_json()}


@dataclass(frozen=True)
class HouseholdScheduleUpdate:
    """Bestaetigt (androguard): identische Feldform wie HouseholdSchedule
    (scheduleId, options) -- separate Klasse existiert im Bytecode,
    vermutlich fuer einen spezifischeren Update-Kontext, aber die
    Unterscheidung zu HouseholdSchedule wurde nicht weiter aufgeloest."""

    schedule_id: str
    options: ScheduleOptions

    def to_json(self) -> dict[str, Any]:
        return {"scheduleId": self.schedule_id, "options": self.options.to_json()}



# =========================================================================
# CommandParams/Region/CommandPolygon (com.irobot.data.missioncommand.datamodels)
# =========================================================================
#
# STATUS: NEU (11. Juli, achte Sitzung). Diese ganze Klassenfamilie fehlte
# im jadx-Ausgabebaum -- wie EditMapV1Request/ScheduleOptions stillschweigend
# uebersprungen. Systematisch gefunden durch einen Vollabgleich ALLER
# com.irobot.*-Klassen in der DEX gegen den jadx-Ausgabebaum (6755 fehlende
# Klassen insgesamt, weit ueberwiegend UI-Schicht/Compose-Screens -- diese
# Mission-Command-Untergruppe ist der einzige fuer die Bibliothek relevante
# Teil). Schliesst einen seit der ersten Sitzung offenen Punkt: RoutineCommand.
# params/regions/id_multipolys waren bisher rohe dicts.
#
# Kein @SerialName auf einem einzigen gefundenen Feld -- Wire-Schluessel
# vermutlich = Kotlin-Property-Name direkt (gleiches Muster wie bei
# EditMapV1Request/ScheduleOptions).


class RegionType(str, Enum):
    """UEBERARBEITET (25. Sitzung): die tatsaechlichen Wire-Werte sind
    KLEINGESCHRIEBEN ("rid"/"zid"), bestaetigt durch echte
    Missionshistorie-Daten (chairstacker, cmd.regions[].type). Die
    urspruengliche androguard-Lesung (RID/TID/ZID, grossgeschrieben)
    las korrekt die Enum-KONSTANTENNAMEN aus dem Bytecode, aber die
    tatsaechliche Serialisierung scheint sie klein zu schreiben --
    entweder eine @SerialName-Anmerkung, die beim ersten Scan nicht
    gefunden wurde, oder eine automatische Kleinschreibung im
    Serialisierer. Python-Member-Namen bleiben gross (Konvention),
    nur die WERTE wurden angepasst. "tid" bleibt unbestaetigt (kein
    TID in echten Daten gesehen -- nur RID und ZID kamen vor)."""

    RID = "rid"
    TID = "tid"
    ZID = "zid"


@dataclass(frozen=True)
class PadWetnessParam:
    """Bestaetigt (androguard): KEIN Enum (super = Object), sondern eine
    Klasse mit drei vordefinierten Konstanten-Instanzen (Damp, Moderate,
    Wet) und drei Int-Feldern (disposable, padPlate, reusable) -- vermutlich
    je Pad-Typ unterschiedliche Naessestufen-Kodierung. Exakte Werte pro
    Konstante nicht aus Bytecode-Feldliste ablesbar (nur Feldnamen/Typen,
    keine statischen Werte) -- als Platzhalter-Presets mit None belassen,
    NICHT geraten."""

    disposable: int | None = None
    pad_plate: int | None = None
    reusable: int | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.disposable is not None:
            body["disposable"] = self.disposable
        if self.pad_plate is not None:
            body["padPlate"] = self.pad_plate
        if self.reusable is not None:
            body["reusable"] = self.reusable
        return body

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PadWetnessParam:
        """NEU (32. Sitzung) -- bestaetigt aus echter get_settings()-
        Antwort (chairstacker): {"disposable": 3, "reusable": 1,
        "padPlate": 1}."""
        return cls(
            disposable=data.get("disposable"),
            pad_plate=data.get("padPlate"),
            reusable=data.get("reusable"),
        )


class CleaningMode(str, Enum):
    """Bestaetigt (androguard, MissionPreferenceValue$CleaningMode):
    5 Werte. Jeder hat zusaetzlich ein numerisches "mode"-Feld und ein
    "uid" -- hier nur die Namen als Enum, die numerischen Codes wurden
    nicht aus der Bytecode-Feldliste ablesbar (nur Feldtypen, keine
    statischen Werte)."""

    MOP = "Mop"
    MOPPING = "Mopping"
    VAC_THEN_MOP = "VacThenMop"
    VACUUM = "Vacuum"
    VACUUM_AND_MOP = "VacuumAndMop"


class CleaningPasses(str, Enum):
    """Bestaetigt (androguard, MissionPreferenceValue$CleaningPasses):
    nur 2 Werte."""

    DOUBLE = "Double"
    SINGLE = "Single"


class LiquidAmountLevel(str, Enum):
    """Bestaetigt (androguard, MissionPreferenceValue$LiquidAmount UND
    $ComboLiquidAmount -- beide haben identische 3 Werte High/Low/Normal,
    hier zusammengefasst da strukturell gleich)."""

    HIGH = "High"
    LOW = "Low"
    NORMAL = "Normal"


class SoftwareScrub(str, Enum):
    """Bestaetigt (androguard, MissionPreferenceValue$SoftwareScrub)."""

    OFF = "Off"
    ON = "On"


class VacuumPowerLevel(str, Enum):
    """Bestaetigt (androguard, MissionPreferenceValue$VacuumPower): 4
    Werte (mehr als CleaningMode etc.)."""

    HIGH = "High"
    LOW = "Low"
    NORMAL = "Normal"
    QUIET = "Quiet"


class MissionPreferenceSwitcherType(str, Enum):
    """Bestaetigt (androguard, MissionPreferenceType$Switcher): 4 Werte."""

    CAREFUL_DRIVE = "CarefulDrive"
    EDGE_CLEAN = "EdgeClean"
    OBSTACLE_DETECTION = "ObstacleDetection"
    PAD_WASH_AFTER = "PadWashAfter"


@dataclass(frozen=True)
class MissionPreferenceSwitcher:
    """Bestaetigt (androguard, MissionPreference$Switcher): isOn (Bool),
    type (MissionPreferenceType.Switcher)."""

    preference_type: MissionPreferenceSwitcherType
    is_on: bool

    def to_json(self) -> dict[str, Any]:
        return {"type": self.preference_type.value, "isOn": self.is_on}


@dataclass(frozen=True)
class MissionPreferenceSelector:
    """Bestaetigt (androguard, MissionPreference$Selector): possibleValues
    (List), selected (Int -- Index in possibleValues), type
    (MissionPreferenceType.Selector). MissionPreferenceType.Selector
    selbst ist KEIN Enum (hat ein Function0 "knownValues"-Feld) --
    dynamischer/offener als die Switcher-Variante, daher hier "type" als
    rohen String belassen statt eine moeglicherweise falsche geschlossene
    Enum-Liste vorzugeben."""

    preference_type: str
    possible_values: list[Any] = field(default_factory=list)
    selected: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"type": self.preference_type, "possibleValues": self.possible_values, "selected": self.selected}


@dataclass(frozen=True)
class CommandPolygonMetadata:
    """Bestaetigt (androguard): einziges Feld furnitureId (Int)."""

    furniture_id: int

    def to_json(self) -> dict[str, Any]:
        return {"furnitureId": self.furniture_id}


@dataclass(frozen=True)
class CommandPolygon:
    """Bestaetigt (androguard): id (String), metadata
    (CommandPolygonMetadata), poly (List -- vermutlich Liste von
    Positionen, Typ ueber Bytecode-Feldsignatur nicht auflösbar wegen
    Generics-Typloeschung, hier als List[Position] angenommen in Analogie
    zu allen anderen Polygon-aehnlichen Strukturen in dieser Datei)."""

    polygon_id: str
    poly: list[Position] = field(default_factory=list)
    metadata: CommandPolygonMetadata | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"id": self.polygon_id, "poly": [list(p) for p in self.poly]}
        if self.metadata is not None:
            body["metadata"] = self.metadata.to_json()
        return body


@dataclass(frozen=True)
class CommandParams:
    """Bestaetigt (androguard): ALLE 37 Felder direkt aus der DEX-
    Feldliste von CommandParams, jedes optional (Boxed Integer/Boolean
    in Kotlin = alle nullable). Das ist die vollstaendige Parameter-
    Oberflaeche fuer einen Missionsbefehl -- deckt u.a. Saugkraft
    (suctionLevel), Wischnaesse (padWetness), Teppich-Boost
    (carpetBoost), Raumbegrenzung (roomConfine), Zeitbox
    (timeboxMinutes), Fahrgeschwindigkeit fuer Steuerbefehle
    (velocityLeft/velocityRight) und viele mehr ab. Bedeutung einzelner,
    kryptischerer Felder (noKOZ, odoaMode, rankOverlap, gentleMode) nicht
    weiter untersucht -- Feldnamen 1:1 aus dem Bytecode uebernommen."""

    adaptive_cleaning: bool | None = None
    bin_pause: bool | None = None
    capture_mode: int | None = None
    carpet_boost: bool | None = None
    clean_score_id: str | None = None
    cleaning_profile: str | None = None
    eco_charge: bool | None = None
    execute_in_place: bool | None = None
    gentle_mode: int | None = None
    heated_water: int | None = None
    manual_update: bool | None = None
    monitor_mode: int | None = None
    no_koz: int | None = None
    no_auto_passes: bool | None = None
    """NEU (27. Sitzung) -- bestaetigt aus echten Daten: eingebettet in
    get_state()s cleanSchedule2[].cmdStr (ein string-serialisiertes,
    Python-repr-artiges Objekt, kein direktes JSON -- ungewoehnliche
    Fundstelle). Wire-Schluessel "noAutoPasses", beobachteter Wert
    true."""
    no_persistent_pass: bool | None = None
    odoa_mode: int | None = None
    open_only: bool | None = None
    operating_mode: int | None = None
    """NEU (25. Sitzung) -- bestaetigt aus echter Missionshistorie
    (chairstacker), Wire-Schluessel "operatingMode". Beobachtete Werte:
    2, 32 -- Bedeutung nicht weiter untersucht (vermutlich ein
    Betriebsmodus-Bitmuster, aehnlich cap.oMode aus get_state())."""
    pad_wash_after: int | None = None
    pad_wash_area: int | None = None
    pad_wetness: PadWetnessParam | None = None
    rank_overlap: int | None = None
    replay_of: str | None = None
    routine_type: str | None = None
    """NEU (26. Sitzung) -- bestaetigt aus echten room_metadata-Daten
    (chairstacker), beobachtet zusammen mit replay_of (Wert "REPLAY").
    Vermutlich der Unterscheidungswert, der anzeigt, dass dieser
    Parametersatz aus einer wiederholten frueheren Mission stammt statt
    aus einer neuen Konfiguration."""
    room_confine: bool | None = None
    rotate: int | None = None
    routine_modified: bool | None = None
    schedule_hold: bool | None = None
    scrub: int | None = None
    """KORRIGIERT (25. Sitzung): echter Wire-Schluessel ist "swScrub",
    nicht "scrub" -- bestaetigt aus echter Missionshistorie
    (chairstacker, cmd.regions[].params.swScrub). Der urspruengliche
    "scrub"-Schluessel war eine Bytecode-Vermutung ohne starke
    Bestaetigung (siehe Klassen-Docstring: "kryptischere Felder nicht
    weiter untersucht"). Python-Attributname bleibt "scrub" (keine
    API-Aenderung fuer Aufrufer), nur der Wire-Schluessel in
    to_json()/from_json() wurde korrigiert."""
    smart_clean_id: str | None = None
    speed: int | None = None
    stream_on_route: bool | None = None
    suction_level: int | None = None
    timebox_minutes: int | None = None
    translate: int | None = None
    two_pass: bool | None = None
    vac_high: bool | None = None
    velocity_left: int | None = None
    velocity_right: int | None = None

    def to_json(self) -> dict[str, Any]:
        """Nur gesetzte (nicht-None) Felder werden aufgenommen, unter
        ihrem Kotlin-Property-Namen (camelCase 1:1)."""
        raw = {
            "adaptiveCleaning": self.adaptive_cleaning,
            "binPause": self.bin_pause,
            "captureMode": self.capture_mode,
            "carpetBoost": self.carpet_boost,
            "cleanScoreId": self.clean_score_id,
            "profile": self.cleaning_profile,
            "ecoCharge": self.eco_charge,
            "executeInPlace": self.execute_in_place,
            "gentleMode": self.gentle_mode,
            "heatedWater": self.heated_water,
            "manualUpdate": self.manual_update,
            "monitorMode": self.monitor_mode,
            "noKOZ": self.no_koz,
            "noAutoPasses": self.no_auto_passes,
            "noPersistentPass": self.no_persistent_pass,
            "odoaMode": self.odoa_mode,
            "openOnly": self.open_only,
            "operatingMode": self.operating_mode,
            "padWashAfter": self.pad_wash_after,
            "padWashArea": self.pad_wash_area,
            "padWetness": self.pad_wetness.to_json() if self.pad_wetness is not None else None,
            "rankOverlap": self.rank_overlap,
            "replay_of": self.replay_of,
            "routine_type": self.routine_type,
            "roomConfine": self.room_confine,
            "rotate": self.rotate,
            "routineModified": self.routine_modified,
            "scheduleHold": self.schedule_hold,
            "swScrub": self.scrub,
            "smartCleanId": self.smart_clean_id,
            "speed": self.speed,
            "streamOnRoute": self.stream_on_route,
            "suctionLevel": self.suction_level,
            "timeboxMinutes": self.timebox_minutes,
            "translate": self.translate,
            "twoPass": self.two_pass,
            "vacHigh": self.vac_high,
            "velocityLeft": self.velocity_left,
            "velocityRight": self.velocity_right,
        }
        return {k: v for k, v in raw.items() if v is not None}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CommandParams:
        """NEU (11. Juli, neunte Sitzung) -- Kehrfunktion zu to_json(),
        fuer Antwortmodelle wie CleaningProfile, die CommandParams
        enthalten. pad_wetness wird bewusst nicht automatisch aus
        verschachteltem JSON aufgebaut (PadWetnessParam.from_json() gibt
        es nicht -- die drei Felder sind simpel genug, hier direkt
        inline gelesen)."""
        pad_wetness_data = data.get("padWetness")
        pad_wetness = None
        if pad_wetness_data:
            pad_wetness = PadWetnessParam(
                disposable=pad_wetness_data.get("disposable"),
                pad_plate=pad_wetness_data.get("padPlate"),
                reusable=pad_wetness_data.get("reusable"),
            )
        return cls(
            adaptive_cleaning=data.get("adaptiveCleaning"),
            bin_pause=data.get("binPause"),
            capture_mode=data.get("captureMode"),
            carpet_boost=data.get("carpetBoost"),
            clean_score_id=data.get("cleanScoreId"),
            cleaning_profile=data.get("profile"),
            eco_charge=data.get("ecoCharge"),
            execute_in_place=data.get("executeInPlace"),
            gentle_mode=data.get("gentleMode"),
            heated_water=data.get("heatedWater"),
            manual_update=data.get("manualUpdate"),
            monitor_mode=data.get("monitorMode"),
            no_koz=data.get("noKOZ"),
            no_auto_passes=data.get("noAutoPasses"),
            no_persistent_pass=data.get("noPersistentPass"),
            odoa_mode=data.get("odoaMode"),
            open_only=data.get("openOnly"),
            operating_mode=data.get("operatingMode"),
            pad_wash_after=data.get("padWashAfter"),
            pad_wash_area=data.get("padWashArea"),
            pad_wetness=pad_wetness,
            rank_overlap=data.get("rankOverlap"),
            replay_of=data.get("replay_of"),
            routine_type=data.get("routine_type"),
            room_confine=data.get("roomConfine"),
            rotate=data.get("rotate"),
            routine_modified=data.get("routineModified"),
            schedule_hold=data.get("scheduleHold"),
            scrub=data.get("swScrub"),
            smart_clean_id=data.get("smartCleanId"),
            speed=data.get("speed"),
            stream_on_route=data.get("streamOnRoute"),
            suction_level=data.get("suctionLevel"),
            timebox_minutes=data.get("timeboxMinutes"),
            translate=data.get("translate"),
            two_pass=data.get("twoPass"),
            vac_high=data.get("vacHigh"),
            velocity_left=data.get("velocityLeft"),
            velocity_right=data.get("velocityRight"),
        )


@dataclass(frozen=True)
class Region:
    """Bestaetigt (androguard): id (String), name (String), params
    (CommandParams), type (RegionType). Ersetzt das bisherige rohe-dict-
    Element in RoutineCommand.regions.

    KORRIGIERT/ERGAENZT (27. Sitzung): from_json() fehlte bisher komplett
    (Region wurde nur zum Senden gebaut). Echte Missionshistorie-Daten
    (chairstacker) zeigen beim LESEN den Schluessel "region_id", nicht
    "id" wie in to_json() beim SENDEN -- moeglicherweise zwei
    unterschiedliche Wire-Formen fuer denselben Zweck (Kommando-Echo in
    der Historie vs. eigene Sendeform), daher werden hier beide
    akzeptiert, "region_id" zuerst probiert."""

    region_id: str
    region_type: RegionType
    name: str | None = None
    params: CommandParams | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"id": self.region_id, "type": self.region_type.value}
        if self.name is not None:
            body["name"] = self.name
        if self.params is not None:
            body["params"] = self.params.to_json()
        return body

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Region:
        params_data = data.get("params")
        return cls(
            region_id=data.get("region_id") or data.get("id", ""),
            region_type=_enum_or_none(RegionType, data.get("type")) or RegionType.RID,
            name=data.get("name"),
            params=CommandParams.from_json(params_data) if params_data else None,
        )


# =========================================================================
# Missionshistorie-Antwortmodelle (com.irobot.data.restservices.missionhistory)
# =========================================================================
#
# STATUS: NEU (11. Juli, neunte Sitzung). Wie die missioncommand-Familie
# zuvor: komplett im jadx-Ausgabebaum gefehlt, ueber den systematischen
# DEX-Abgleich gefunden. get_mission_history() gab bisher rohes JSON
# zurueck -- jetzt gibt es parse_mission_history_entry() fuer die
# Top-Level-Felder.
#
# NACHTRAG (18. Sitzung): Die urspruengliche Aufwandsgrenze bei den 20
# MissionTimelineEvent-Unterereignistypen wurde aufgehoben -- alle 20
# sind jetzt typisiert (siehe MissionTimelineEvent weiter unten in
# dieser Datei, nach MissionHistoryEntry). timeline ist daher keine
# rohe dict-Struktur mehr, sondern list[MissionTimelineEvent] ueber
# parse_mission_timeline().


class DoneCode(str, Enum):
    """UEBERARBEITET (27. Sitzung): echte Missionshistorie (chairstacker)
    zeigt "ok" (kleingeschrieben) als done_code-Wert -- nicht "OK" wie
    urspruenglich aus androguard-Bytecode-Konstantennamen abgeleitet.
    Exakt dasselbe Muster wie bei RegionType (siehe dessen Docstring):
    Bytecode-Konstantennamen sind grossgeschrieben, tatsaechliche
    Wire-Serialisierung scheint durchgehend klein zu schreiben. NUR
    "ok" ist direkt bestaetigt -- die anderen 18 Werte wurden nach
    demselben Muster (durchgaengige Kleinschreibung wahrscheinlicher
    als gemischte Gross-/Kleinschreibung innerhalb eines Enums)
    mitgeaendert, aber NICHT einzeln bestaetigt. Falls sich einzelne
    als falsch herausstellen, bitte gezielt korrigieren, sobald echte
    Daten mit diesem konkreten Fehlercode vorliegen. `_enum_or_none()`
    faengt ohnehin jeden nicht passenden Wert ab und gibt den rohen
    String zurueck, statt abzustuerzen."""

    BATTERY = "battery"
    BATTERY_CANCEL = "battery_cancel"
    BUSY = "busy"
    CANCEL = "cancel"
    DND_END = "dnd_end"
    EMPTY = "empty"
    FULL = "full"
    INCOMPLETE = "incomplete"
    NONE_ = "none"
    OK = "ok"
    PLACE_DOCK = "place_dock"
    RETURN_HOME_END = "return_home_end"
    SCHEDULE_ERROR = "schedule_error"
    STUCK = "stuck"
    TIMEBOX_END = "timebox_end"
    USER_END = "user_end"
    USER_REBOOT = "user_reboot"
    USER_SLEEP = "user_sleep"
    USER_SPOT = "user_spot"


class PadCategory(str, Enum):
    """Bestaetigt (androguard): 7 Werte."""

    DRY = "DRY"
    INVALID = "INVALID"
    NO_PAD = "NO_PAD"
    PLATE = "PLATE"
    REUSABLE_DRY = "REUSABLE_DRY"
    REUSABLE_WET = "REUSABLE_WET"
    WET = "WET"


class RankOverlap(str, Enum):
    """Bestaetigt (androguard): 3 Werte."""

    DEEP_CLEAN = "DEEP_CLEAN"
    DETAIL_CLEAN = "DETAIL_CLEAN"
    EXTENDED_CLEAN = "EXTENDED_CLEAN"


class CoverageStrategy(str, Enum):
    """Bestaetigt (androguard): 3 Werte."""

    HYBRID_COVERAGE_PLANNER = "HYBRID_COVERAGE_PLANNER"
    RESERVED = "RESERVED"
    ROOM_SEGMENTATION = "ROOM_SEGMENTATION"


def _enum_or_none(enum_cls: type, value: Any) -> Any:
    """Hilfsfunktion: liefert enum_cls(value) wenn moeglich, sonst den
    rohen Wert zurueck (statt eine ValueError zu werfen) -- Server kann
    neue Werte einfuehren, die dieser Bibliotheksstand noch nicht kennt."""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return value


@dataclass(frozen=True)
class MissionCommandRecord:
    """KORRIGIERT (27. Sitzung): mapId/mapVersionId waren falsch geraten,
    bestaetigt falsch durch echte Missionshistorie (chairstacker) --
    die echten Feldnamen sind p2map_id und user_p2mapv_id (letzteres
    manchmal null). cleanAll wurde in den verfuegbaren echten Beispielen
    nie beobachtet (weder vorhanden noch widerlegt) -- Feldname
    unveraendert gelassen, da nicht bestaetigt falsch. regions jetzt
    ueber Region.from_json() typisiert statt roher Liste, da die
    Struktur (params/region_id/type) inzwischen bekannt ist -- params
    darin sind CommandParams-foermig.

    ERGAENZT (30. Sitzung): ein eigenes, TOP-LEVEL "params"-Feld fehlte
    komplett -- getrennt von regions[].params, manchmal gesetzt (z.B.
    {"profile": "light"}), manchmal explizit null. Uebersehen, obwohl
    die Daten schon lange vorlagen."""

    clean_all: bool | None = None
    command: str | None = None
    initiator: str | None = None
    map_id: str | None = None
    map_version_id: str | None = None
    ordered: int | None = None
    params: CommandParams | None = None
    regions: list["Region"] = field(default_factory=list)
    robot_id: str | None = None
    time: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionCommandRecord:
        params_data = data.get("params")
        return cls(
            clean_all=data.get("cleanAll"),
            command=data.get("command"),
            initiator=data.get("initiator"),
            map_id=data.get("p2map_id") or data.get("mapId"),
            map_version_id=data.get("user_p2mapv_id") or data.get("mapVersionId"),
            ordered=data.get("ordered"),
            params=CommandParams.from_json(params_data) if params_data else None,
            regions=[Region.from_json(r) for r in (data.get("regions") or [])],
            robot_id=data.get("robot_id") or data.get("robotId"),
            time=data.get("time"),
        )


@dataclass(frozen=True)
class MissionHistoryEntry:
    """Bestaetigt (androguard, MissionHistory): Top-Level-Felder der
    Missionshistorie-Antwort. `timeline` bleibt bewusst rohes JSON --
    siehe Modul-Docstring zur Aufwandsgrenze bei den 20
    Unterereignistypen. Nicht alle 30+ Bytecode-Felder wurden hier
    aufgenommen -- Fokus auf die fuer Auswertung nuetzlichsten (Zeiten,
    doneCode, Fehlercode, Flaechendeckung); seltener genutzte Felder
    (wifiChannel, startEndWlBars, etc.) sind ueber `raw` weiterhin
    zugaenglich."""

    mission_id: str | None = None
    robot_id: str | None = None
    start_time: int | None = None
    timestamp: int | None = None
    duration_m: int | None = None
    minutes_running: int | None = None
    minutes_paused: int | None = None
    minutes_charging: int | None = None
    minutes_done: int | None = None
    done_code: DoneCode | str | None = None
    done_raw: str | None = None
    error_code: int | None = None
    square_feet_covered: int | None = None
    number_of_evacuations: int | None = None
    number_of_dirt_detects: int | None = None
    docked_at_start: bool | None = None
    ended_on_dock: int | None = None
    command: MissionCommandRecord | None = None
    static_map_id: str | None = None
    coverage_strategy: CoverageStrategy | str | None = None
    rank_overlap: RankOverlap | str | None = None
    pad_category: PadCategory | str | None = None
    timeline: list["MissionTimelineEvent"] = field(default_factory=list)
    """NEU (18. Sitzung) -- alle 20 Unterereignistypen jetzt typisiert,
    siehe MissionTimelineEvent weiter unten in dieser Datei."""
    raw: dict[str, Any] = field(default_factory=dict)
    """Die komplette, unveraenderte Serverantwort fuer dieses Element --
    fuer alle Felder, die oben nicht einzeln aufgenommen wurden."""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionHistoryEntry:
        """KORRIGIERT (27. Sitzung): fast alle Feldnamen waren falsch
        geraten (camelCase-Vermutungen), bestaetigt falsch durch eine
        vollstaendige, echte Antwort (chairstacker). Die tatsaechlichen
        Felder sind ueberwiegend knappe Abkuerzungen, teils snake_case:
        robot_id (nicht robotId), runM (nicht minutesRunning), pauseM
        (nicht minutesPaused), chrgM (nicht minutesCharging), doneM
        (nicht minutesDone), sqft (nicht squareFeetCovered), evacs
        (nicht numberOfEvacuations), eDock (nicht endedOnDock), cmd
        (nicht command), done_raw (nicht doneRaw, UND mit Unterstrich).
        "done" (kurz) und "done_raw" scheinen denselben Wert doppelt zu
        fuehren (z.B. beide "ok") -- done_code liest jetzt "done", nicht
        das nie beobachtete "doneCode". errorCode/numberOfDirtDetects/
        staticMapId/rankOverlap/padCategory/coverageStrategy blieben in
        den verfuegbaren Beispieldaten unbeobachtet (keine Fehler- oder
        Mehrfachkarten-Faelle dabei) -- Feldnamen dafuer bewusst NICHT
        geaendert, da unbestaetigt, ob die urspruengliche Vermutung dort
        zufaellig richtig war oder nicht; falls sich das als falsch
        herausstellt, braucht es einen weiteren echten Beispielfall mit
        einem tatsaechlichen Fehler."""
        command_data = data.get("cmd") or data.get("command")
        timeline_data = data.get("timeline") or {}
        coverage_strategy = (timeline_data or {}).get("coverageStrategy")
        timeline_events = (
            timeline_data.get("finEvents") if isinstance(timeline_data, dict) else timeline_data
        )
        # KORRIGIERT (31. Sitzung): "events" existierte gar nicht in echten
        # Daten -- die reichen Unterereignisse stehen unter "finEvents",
        # eine separate, sparsame "event"-Liste (nur type+ts) existiert
        # daneben und wird hier bewusst NICHT verwendet (enthaelt keine
        # zusaetzliche Information gegenueber finEvents).
        return cls(
            mission_id=data.get("missionId"),
            robot_id=data.get("robot_id"),
            start_time=data.get("startTime"),
            timestamp=data.get("timestamp"),
            duration_m=data.get("durationM"),
            minutes_running=data.get("runM"),
            minutes_paused=data.get("pauseM"),
            minutes_charging=data.get("chrgM"),
            minutes_done=data.get("doneM"),
            done_code=_enum_or_none(DoneCode, data.get("done")),
            done_raw=data.get("done_raw"),
            error_code=data.get("errorCode"),
            square_feet_covered=data.get("sqft"),
            number_of_evacuations=data.get("evacs"),
            number_of_dirt_detects=data.get("numberOfDirtDetects"),
            docked_at_start=data.get("dockedAtStart"),
            ended_on_dock=data.get("eDock"),
            command=MissionCommandRecord.from_json(command_data) if command_data else None,
            static_map_id=data.get("staticMapId"),
            coverage_strategy=_enum_or_none(CoverageStrategy, coverage_strategy),
            rank_overlap=_enum_or_none(RankOverlap, data.get("rankOverlap")),
            pad_category=_enum_or_none(PadCategory, data.get("padCategory")),
            timeline=parse_mission_timeline(timeline_events),
            raw=data,
        )


def parse_mission_history(data: dict[str, Any] | list[dict[str, Any]]) -> list[MissionHistoryEntry]:
    """Wandelt die rohe get_mission_history()-Antwort in eine Liste
    typisierter MissionHistoryEntry-Objekte um. NEU (11. Juli, neunte
    Sitzung). Akzeptiert sowohl eine rohe Liste als auch ein dict mit
    einem umschliessenden Schluessel (Response-Umschlagform nicht
    bestaetigt -- daher beide Formen toleriert: {"missions": [...]}
    oder direkt [...])."""
    if isinstance(data, dict):
        entries = data.get("missions") or data.get("history") or []
    else:
        entries = data
    return [MissionHistoryEntry.from_json(e) for e in entries]


# =========================================================================
# CleaningProfile / DNDStatusResponse / HouseholdSetting / Routine-Defaults
# =========================================================================
#
# STATUS: NEU (11. Juli, neunte Sitzung). Wie oben: im jadx-Ausgabebaum
# gefehlt, ueber DEX-Abgleich gefunden.


class CleaningProfileType(str, Enum):
    """Bestaetigt (androguard, CleaningProfile$ProfileType): 4 Werte."""

    DEEP = "DEEP"
    LIGHT = "LIGHT"
    NORMAL = "NORMAL"
    SMART = "SMART"


@dataclass(frozen=True)
class CleaningProfile:
    """Bestaetigt (androguard): profile (ProfileType), commandParams
    (CommandParams -- selbe Klasse wie bei RoutineCommand/Region oben),
    regions (List -- Struktur nicht weiter untersucht, roh belassen)."""

    profile: CleaningProfileType | str | None = None
    command_params: CommandParams | None = None
    regions: list[Any] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CleaningProfile:
        params_data = data.get("commandParams")
        return cls(
            profile=_enum_or_none(CleaningProfileType, data.get("profile")),
            command_params=CommandParams.from_json(params_data) if params_data else None,
            regions=data.get("regions") or [],
        )


@dataclass(frozen=True)
class DNDStatusResponse:
    """Bestaetigt (androguard): dailyStart/dailyEnd (Integer, vermutlich
    Minuten seit Mitternacht), endsAt (Long, vermutlich Epoch-Millis fuer
    eine einmalige DND-Ausnahme), status (Map -- Struktur nicht
    untersucht). WICHTIG: DNDSchedule (die sealed-class-Variante mit
    DailySchedule/EndsAt als getrennten Typen) und DNDStatusResponse
    (diese flache Klasse) sind ZWEI VERSCHIEDENE Repraesentationen --
    DNDStatusResponse duerfte die tatsaechliche GET-Antwortform sein
    (direkt referenziert von DNDGetRequest-Aufrufern), DNDSchedule eher
    intern fuer den PUT-Request-Aufbau."""

    daily_start: int | None = None
    daily_end: int | None = None
    ends_at: int | None = None
    status: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DNDStatusResponse:
        return cls(
            daily_start=data.get("dailyStart"),
            daily_end=data.get("dailyEnd"),
            ends_at=data.get("endsAt"),
            status=data.get("status") or {},
        )


@dataclass(frozen=True)
class HouseholdSetting:
    """Bestaetigt (androguard): settingId, settingType (String),
    options (HouseholdSettingOptions -- diese Klasse selbst wurde nicht
    weiter untersucht, vermutlich ein generischer/polymorpher Container
    je nach settingType -- hier als rohes dict belassen)."""

    setting_id: str | None = None
    setting_type: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdSetting:
        return cls(
            setting_id=data.get("settingId"),
            setting_type=data.get("settingType"),
            options=data.get("options") or {},
        )


@dataclass(frozen=True)
class Routine:
    """Bestaetigt (androguard, routines/datamodels/Routine -- die
    Standard-Routinen-Antwort, ANDERS als favorites/datamodels/
    RoutineCommand oben): commandDefs (List -- in starker Analogie zu
    FavoriteV1.command_defs vermutlich List<RoutineCommand>, aber ueber
    Bytecode-Feldsignatur nicht generisch aufloesbar), lastRun,
    nameLocArgs/nameLocKey (Lokalisierungs-Strings fuer den
    UI-Anzeigenamen), timeEstimate/timeEstimateSeconds."""

    name: str | None = None
    command_defs: list[dict[str, Any]] = field(default_factory=list)
    last_run: int | None = None
    name_loc_key: str | None = None
    name_loc_args: list[str] = field(default_factory=list)
    time_estimate: int | None = None
    time_estimate_seconds: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Routine:
        return cls(
            name=data.get("name"),
            command_defs=data.get("commandDefs") or [],
            last_run=data.get("lastRun"),
            name_loc_key=data.get("nameLocKey"),
            name_loc_args=data.get("nameLocArgs") or [],
            time_estimate=data.get("timeEstimate"),
            time_estimate_seconds=data.get("timeEstimateSeconds"),
        )


def parse_default_routines(data: dict[str, Any] | list[dict[str, Any]]) -> list[Routine]:
    """Wandelt die rohe get_default_routines()-Antwort in eine Liste
    typisierter Routine-Objekte um. Umschlagform nicht bestaetigt --
    toleriert dieselben Varianten wie parse_mission_history()."""
    if isinstance(data, dict):
        entries = data.get("routines") or data.get("defaults") or []
    else:
        entries = data
    return [Routine.from_json(e) for e in entries]


# =========================================================================
# MissionTimelineEvent -- alle 20 Unterereignistypen (18. Sitzung)
# =========================================================================
#
# Schliesst die in der neunten Sitzung bewusst gezogene Aufwandsgrenze.
# Alle Felder bestaetigt (15 Klassen per jadx sauber dekompiliert, 4
# weitere -- PlanEvent/PolygonEvent/TravelEvent/TraversalEvent, plus die
# 4 zugehoerigen Enums PlanType/PlanUpcoming/TravelDestination/
# TraversalType -- per androguard, da jadx sie wie so oft stillschweigend
# uebersprungen hatte). MissionTimelineEvent selbst hat GENAU 20
# Unterereignis-Felder (androguard-bestaetigt) -- "relocalizing" und
# "tentativeLocation" teilen sich beide denselben Typ TentativeLocationEvent
# (zwei Felder, eine Klasse), daher reichen 19 Ereignisklassen fuer 20 Felder.
#
# Kein @SerialName auf einem einzigen gefundenen Feld in dieser ganzen
# Familie -- Wire-Schluessel = Kotlin-Property-Name direkt (camelCase),
# gleiches Muster wie ueberall sonst in dieser Datei.


class PlanType(str, Enum):
    """Bestaetigt (androguard, PlanEvent.type): 3 Werte."""

    ALL = "ALL"
    DRC = "DRC"
    TRAIN = "TRAIN"


class PlanUpcoming(str, Enum):
    """Bestaetigt (androguard, PlanEvent.upcoming-Listenelemente): 4 Werte."""

    POLY = "POLY"
    RID = "RID"
    WID = "WID"
    ZID = "ZID"


class TravelDestination(str, Enum):
    """Bestaetigt (androguard fuer Konstantennamen), Werte auf
    Kleinschreibung UMGESTELLT (31. Sitzung) -- echte Daten zeigen
    "dest": "dock"/"zone"/"room" (kleingeschrieben), dasselbe Muster
    wie RegionType/DoneCode. Nur "dock"/"zone"/"room" direkt
    beobachtet, "poly"/"waypoint" nach demselben Muster mitgeaendert."""

    DOCK = "dock"
    POLY = "poly"
    ROOM = "room"
    WAYPOINT = "waypoint"
    ZONE = "zone"


class TraversalType(str, Enum):
    """Bestaetigt (androguard fuer Konstantennamen), Wert auf
    Kleinschreibung umgestellt (31. Sitzung) -- echte Daten zeigen
    "type": "region" (kleingeschrieben) innerhalb des traversal-
    Unterobjekts. Nur REGION direkt beobachtet, ZONE nach demselben
    Muster mitgeaendert."""

    REGION = "region"
    ZONE = "zone"


@dataclass(frozen=True)
class CommandEvent:
    """Bestaetigt (jadx): command, initiator, time."""

    command: str | None = None
    initiator: str | None = None
    time: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CommandEvent:
        return cls(command=data.get("command"), initiator=data.get("initiator"), time=data.get("time"))


@dataclass(frozen=True)
class DiscoveryEvent:
    """Bestaetigt (jadx): mapId, mapVersion, regionId."""

    map_id: str | None = None
    map_version: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DiscoveryEvent:
        return cls(map_id=data.get("mapId"), map_version=data.get("mapVersion"), region_id=data.get("regionId"))


@dataclass(frozen=True)
class ErrorEvent:
    """Bestaetigt (jadx): einziges Feld value (vermutlich ein Fehlercode,
    analog zu MissionHistoryEntry.error_code)."""

    value: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ErrorEvent:
        return cls(value=data.get("value"))


@dataclass(frozen=True)
class EvacEvent:
    """Bestaetigt (jadx): error, state -- Auto-Evac-Vorgang (Absaugstation)."""

    error: int | None = None
    state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EvacEvent:
        return cls(error=data.get("error"), state=data.get("state"))


@dataclass(frozen=True)
class LiveViewEvent:
    """Bestaetigt (jadx): eventId, status."""

    event_id: str | None = None
    status: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LiveViewEvent:
        return cls(event_id=data.get("eventId"), status=data.get("status"))


@dataclass(frozen=True)
class PadDryEvent:
    """Bestaetigt (jadx): error, padDryState -- Wischpad-Trocknungszyklus."""

    error: int | None = None
    pad_dry_state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PadDryEvent:
        return cls(error=data.get("error"), pad_dry_state=data.get("padDryState"))


@dataclass(frozen=True)
class PadWashEvent:
    """UEBERARBEITET (31. Sitzung, programmatischer Vollabgleich): echte
    Daten zeigen flAmt (nicht fluidAmount), pwState (nicht
    padWashState) -- error/reason waren bereits korrekt."""

    error: int | None = None
    fluid_amount: int | None = None
    pad_wash_state: int | None = None
    reason: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PadWashEvent:
        return cls(
            error=data.get("error"),
            fluid_amount=data.get("flAmt") or data.get("fluidAmount"),
            pad_wash_state=data.get("pwState") or data.get("padWashState"),
            reason=data.get("reason"),
        )


@dataclass(frozen=True)
class PanoramaEvent:
    """Bestaetigt (jadx): eventId, mapId, mapVersion, panoramaId, status,
    waypointId -- Panoramaaufnahme waehrend der Kartierung."""

    event_id: str | None = None
    map_id: str | None = None
    map_version: str | None = None
    panorama_id: str | None = None
    status: int | None = None
    waypoint_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PanoramaEvent:
        return cls(
            event_id=data.get("eventId"),
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            panorama_id=data.get("panoramaId"),
            status=data.get("status"),
            waypoint_id=data.get("waypointId"),
        )


@dataclass(frozen=True)
class PlanEvent:
    """Bestaetigt (androguard, jadx hatte diese Klasse uebersprungen):
    mapId, mapVersion, ordered, type (PlanType), upcoming
    (List[PlanUpcoming]). "ordered" hier klar eine Intra-Event-Eigenschaft
    (Position innerhalb der upcoming-Liste) -- guter Beleg fuer dieselbe
    Lesart, die ha_roomba_plus fuer RoutineCommand.ordered bereits
    bestaetigt hatte (siehe dessen Docstring), diesmal in einem
    voellig anderen Kontext (historischer Bericht statt Live-Kommando)."""

    map_id: str | None = None
    map_version: str | None = None
    ordered: int | None = None
    plan_type: PlanType | str | None = None
    upcoming: list[PlanUpcoming | str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PlanEvent:
        return cls(
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            ordered=data.get("ordered"),
            plan_type=_enum_or_none(PlanType, data.get("type")),
            upcoming=[_enum_or_none(PlanUpcoming, v) for v in (data.get("upcoming") or [])],
        )


@dataclass(frozen=True)
class PolygonEvent:
    """Bestaetigt (androguard): area, areaCleaned, mapId, mapVersion,
    poly (List -- Struktur nicht weiter untersucht, roh belassen),
    polyId, regionId."""

    area: int | None = None
    area_cleaned: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    poly: list[Any] = field(default_factory=list)
    poly_id: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PolygonEvent:
        return cls(
            area=data.get("area"),
            area_cleaned=data.get("areaCleaned"),
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            poly=data.get("poly") or [],
            poly_id=data.get("polyId"),
            region_id=data.get("regionId"),
        )


@dataclass(frozen=True)
class RefillEvent:
    """Bestaetigt (jadx): error, fluidAmount, fluidReplenishmentState --
    Frischwasser-/Reinigungsloesung-Nachfuellvorgang."""

    error: int | None = None
    fluid_amount: int | None = None
    fluid_replenishment_state: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RefillEvent:
        return cls(
            error=data.get("error"),
            fluid_amount=data.get("fluidAmount"),
            fluid_replenishment_state=data.get("fluidReplenishmentState"),
        )


@dataclass(frozen=True)
class RoomEvent:
    """UEBERARBEITET (31. Sitzung, programmatischer Vollabgleich): die
    juengste jadx-Lesung (mapId/mapVersion/regionId) war falsch --
    echte finEvents-Daten zeigen die kurzen Formen p2mapId/p2mapvId/rid,
    konsistent mit dem Muster in Travel-/Traversal-/ZoneEvent. conPasses/
    passArea wurden in den verfuegbaren echten Beispielen nie beobachtet
    (weder bestaetigt noch widerlegt) -- Feldnamen dafuer unveraendert
    gelassen."""

    area: int | None = None
    con_passes: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    pass_area: int | None = None
    pass_count: int | None = None
    region_id: str | None = None
    status: int | None = None
    total_area: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomEvent:
        return cls(
            area=data.get("area"),
            con_passes=data.get("conPasses"),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            pass_area=data.get("passArea"),
            pass_count=data.get("passCount"),
            region_id=data.get("rid") or data.get("regionId"),
            status=data.get("status"),
            total_area=data.get("totalArea"),
        )


@dataclass(frozen=True)
class SubRoomEvent:
    """Bestaetigt (jadx): area, mapId, mapVersion, operatingMode, passArea,
    passCount, polyId, regionId, status, subRegionId, totalArea, zoneId --
    Fortschritt pro Teilraum/Zone innerhalb eines Raums."""

    area: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    operating_mode: int | None = None
    pass_area: int | None = None
    pass_count: int | None = None
    poly_id: str | None = None
    region_id: str | None = None
    status: int | None = None
    sub_region_id: str | None = None
    total_area: int | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SubRoomEvent:
        return cls(
            area=data.get("area"),
            map_id=data.get("mapId"),
            map_version=data.get("mapVersion"),
            operating_mode=data.get("operatingMode"),
            pass_area=data.get("passArea"),
            pass_count=data.get("passCount"),
            poly_id=data.get("polyId"),
            region_id=data.get("regionId"),
            status=data.get("status"),
            sub_region_id=data.get("subRegionId"),
            total_area=data.get("totalArea"),
            zone_id=data.get("zoneId"),
        )


@dataclass(frozen=True)
class TentativeLocationEvent:
    """UEBERARBEITET (31. Sitzung, programmatischer Vollabgleich): der
    reale Wire-Schluessel fuer dieses Ereignis ist "reloc", NICHT
    "relocalizing" oder "tentativeLocation" wie urspruenglich
    angenommen (siehe MissionTimelineEvent.from_json()). Feldnamen
    selbst ebenfalls korrigiert: confp2mapId/confp2mapvId (nicht
    confirmedMapId/confirmedMapVersion), p2mapId/p2mapvId (nicht
    mapId/mapVersion). regionId/confirmedRegionId nie in den
    verfuegbaren echten Beispielen beobachtet -- unveraendert
    gelassen. Wird weiterhin auf ZWEI MissionTimelineEvent-Feldern
    referenziert (relocalizing + tentativeLocation) -- ob
    "tentativeLocation" als eigener, tatsaechlich vorkommender
    Wire-Schluessel existiert, bleibt unbestaetigt."""

    confirmed_map_id: str | None = None
    confirmed_map_version: str | None = None
    confirmed_region_id: str | None = None
    map_id: str | None = None
    map_version: str | None = None
    region_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TentativeLocationEvent:
        return cls(
            confirmed_map_id=data.get("confp2mapId") or data.get("confirmedMapId"),
            confirmed_map_version=data.get("confp2mapvId") or data.get("confirmedMapVersion"),
            confirmed_region_id=data.get("confRid") or data.get("confirmedRegionId"),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            region_id=data.get("rid") or data.get("regionId"),
        )


@dataclass(frozen=True)
class TravelEvent:
    """UEBERARBEITET (31. Sitzung, programmatischer Vollabgleich): fast
    alle Feldnamen waren falsch -- echte Daten zeigen dest (nicht
    destination), p2mapId (nicht mapId), p2mapvId (nicht mapVersion),
    rid (nicht regionId), zid (nicht zoneId). polyId/waypointId nie in
    den verfuegbaren echten Beispielen beobachtet -- unveraendert
    gelassen."""

    destination: TravelDestination | str | None = None
    map_id: str | None = None
    map_version: str | None = None
    poly_id: str | None = None
    reason: int | None = None
    region_id: str | None = None
    status: int | None = None
    waypoint_id: str | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TravelEvent:
        return cls(
            destination=_enum_or_none(TravelDestination, data.get("dest") or data.get("destination")),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            poly_id=data.get("polyId"),
            reason=data.get("reason"),
            region_id=data.get("rid") or data.get("regionId"),
            status=data.get("status"),
            waypoint_id=data.get("waypointId"),
            zone_id=data.get("zid") or data.get("zoneId"),
        )


@dataclass(frozen=True)
class TraversalEvent:
    """UEBERARBEITET (31. Sitzung, programmatischer Vollabgleich): echte
    Daten zeigen p2mapId (nicht mapId), p2mapvId (nicht mapVersion),
    rid (nicht regionId) -- zoneId/zid nie in den verfuegbaren echten
    Beispielen beobachtet."""

    map_id: str | None = None
    map_version: str | None = None
    region_id: str | None = None
    traversal_type: TraversalType | str | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TraversalEvent:
        return cls(
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            region_id=data.get("rid") or data.get("regionId"),
            traversal_type=_enum_or_none(TraversalType, data.get("type")),
            zone_id=data.get("zid") or data.get("zoneId"),
        )


@dataclass(frozen=True)
class WaypointEvent:
    """Bestaetigt (jadx): mapId, mapVersion, waypointId."""

    map_id: str | None = None
    map_version: str | None = None
    waypoint_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WaypointEvent:
        return cls(map_id=data.get("mapId"), map_version=data.get("mapVersion"), waypoint_id=data.get("waypointId"))


@dataclass(frozen=True)
class WetOutEvent:
    """Bestaetigt (jadx): status, type -- Wischpad-Befeuchtungsvorgang."""

    status: int | None = None
    wet_out_type: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WetOutEvent:
        return cls(status=data.get("status"), wet_out_type=data.get("type"))


@dataclass(frozen=True)
class ZoneEvent:
    """UEBERARBEITET (31. Sitzung, programmatischer Vollabgleich): echte
    Daten zeigen p2mapId (nicht mapId), p2mapvId (nicht mapVersion),
    zid (nicht zoneId) -- passArea nie in den verfuegbaren echten
    Beispielen beobachtet."""

    area: int | None = None
    map_id: str | None = None
    map_version: str | None = None
    pass_area: int | None = None
    pass_count: int | None = None
    status: int | None = None
    total_area: int | None = None
    zone_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ZoneEvent:
        return cls(
            area=data.get("area"),
            map_id=data.get("p2mapId") or data.get("mapId"),
            map_version=data.get("p2mapvId") or data.get("mapVersion"),
            pass_area=data.get("passArea"),
            pass_count=data.get("passCount"),
            status=data.get("status"),
            total_area=data.get("totalArea"),
            zone_id=data.get("zid") or data.get("zoneId"),
        )


@dataclass(frozen=True)
class MissionTimelineEvent:
    """Bestaetigt (androguard, MissionTimelineEvent): startTime, endTime,
    type (String -- Diskriminator, welches der 20 Unterfelder gesetzt
    ist, kein @SerialName gefunden), sowie GENAU 20 optionale
    Unterereignis-Felder. Nur EIN Feld ist typischerweise pro Event
    gesetzt (passend zum jeweiligen "type"-Diskriminatorwert) -- alle
    anderen bleiben None."""

    start_time: int | None = None
    end_time: int | None = None
    event_type: str | None = None
    command: CommandEvent | None = None
    discovery: DiscoveryEvent | None = None
    error: ErrorEvent | None = None
    evac: EvacEvent | None = None
    live_view: LiveViewEvent | None = None
    pad_dry: PadDryEvent | None = None
    pad_wash: PadWashEvent | None = None
    panorama: PanoramaEvent | None = None
    plan: PlanEvent | None = None
    polygon: PolygonEvent | None = None
    refill: RefillEvent | None = None
    relocalizing: TentativeLocationEvent | None = None
    room: RoomEvent | None = None
    sub_room: SubRoomEvent | None = None
    tentative_location: TentativeLocationEvent | None = None
    travel: TravelEvent | None = None
    traversal: TraversalEvent | None = None
    waypoint: WaypointEvent | None = None
    wet_out: WetOutEvent | None = None
    zone: ZoneEvent | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MissionTimelineEvent:
        """KORRIGIERT (31. Sitzung, programmatischer Vollabgleich gegen
        echte Daten): startTime/endTime existieren in echten finEvents-
        Eintraegen NICHT -- die tatsaechlichen Zeitstempel-Schluessel
        sind "ts" (Ereigniszeit) und "ets" (vermutlich "event
        timestamp", oft nahe an ts). Beide alten Namen bleiben als
        Fallback, falls eine andere Antwortform sie doch nutzt. "reloc"
        ist der echte Schluessel fuer den Neulokalisierungs-Zustand
        (Wire-typische kurze Namensform, konsistent mit room/zone/
        travel/traversal/evac/padWash) -- bislang wurde nur
        "relocalizing"/"tentativeLocation" versucht, keins davon
        stimmt; "reloc" jetzt ergaenzt und befuellt dasselbe
        "relocalizing"-Attribut."""

        def _sub(key: str, parser: Any) -> Any:
            raw = data.get(key)
            return parser(raw) if raw is not None else None

        return cls(
            start_time=data.get("ts") or data.get("startTime"),
            end_time=data.get("ets") or data.get("endTime"),
            event_type=data.get("type"),
            command=_sub("command", CommandEvent.from_json),
            discovery=_sub("discovery", DiscoveryEvent.from_json),
            error=_sub("error", ErrorEvent.from_json),
            evac=_sub("evac", EvacEvent.from_json),
            live_view=_sub("liveView", LiveViewEvent.from_json),
            pad_dry=_sub("padDry", PadDryEvent.from_json),
            pad_wash=_sub("padWash", PadWashEvent.from_json),
            panorama=_sub("panorama", PanoramaEvent.from_json),
            plan=_sub("plan", PlanEvent.from_json),
            polygon=_sub("polygon", PolygonEvent.from_json),
            refill=_sub("refill", RefillEvent.from_json),
            relocalizing=_sub("reloc", TentativeLocationEvent.from_json) or _sub("relocalizing", TentativeLocationEvent.from_json),
            room=_sub("room", RoomEvent.from_json),
            sub_room=_sub("subRoom", SubRoomEvent.from_json),
            tentative_location=_sub("tentativeLocation", TentativeLocationEvent.from_json),
            travel=_sub("travel", TravelEvent.from_json),
            traversal=_sub("traversal", TraversalEvent.from_json),
            waypoint=_sub("waypoint", WaypointEvent.from_json),
            wet_out=_sub("wetOut", WetOutEvent.from_json),
            zone=_sub("zone", ZoneEvent.from_json),
        )


def parse_mission_timeline(data: dict[str, Any] | list[dict[str, Any]] | None) -> list[MissionTimelineEvent]:
    """Wandelt MissionHistoryEntry.raw["timeline"] in eine Liste
    typisierter MissionTimelineEvent-Objekte um. NEU (18. Sitzung).
    Toleriert sowohl eine rohe Liste als auch ein dict mit
    umschliessendem Schluessel (Umschlagform nicht bestaetigt, analog
    zu parse_mission_history())."""
    if data is None:
        return []
    if isinstance(data, dict):
        entries = data.get("events") or data.get("timeline") or []
    else:
        entries = data
    return [MissionTimelineEvent.from_json(e) for e in entries]


# =========================================================================
# P2MapVersion / RoomMetadataEntry / RobotSerialInfo (26. Sitzung)
# =========================================================================
#
# STATUS: NEU (26. Sitzung). Bestaetigt aus einer vollstaendigen, echten
# --dump-config-Antwort (chairstacker, Roomba 405). get_active_map_versions()
# und get_serial_number_data() gaben bisher rohes JSON zurueck (Docstring
# nur mit geratenen/teilweise falschen Feldnamen) -- jetzt mit der
# tatsaechlichen, live bestaetigten Struktur typisiert. Besonders wertvoll:
# rooms_metadata[].room_metadata.operating_mode_defaults' Werte sind
# CommandParams-foermig und lassen sich direkt mit
# CommandParams.from_json() parsen -- derselbe Typ wie fuer
# RoutineCommand.params/Region.params.


@dataclass(frozen=True)
class RoomMetadataEntry:
    """Bestaetigt (echte Live-Antwort): room_id + room_metadata mit
    last_operating_mode, operating_mode_defaults (dict, Schluessel =
    Operating-Mode-ID als String wie "512"/"32"/"2", Werte
    CommandParams-foermig), region_type, optional name (nur bei manchen
    Raeumen gesetzt, z.B. "Bathroom")."""

    room_id: str
    last_operating_mode: int | None = None
    operating_mode_defaults: dict[str, CommandParams] = field(default_factory=dict)
    region_type: RegionType | str | None = None
    name: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RoomMetadataEntry:
        meta = data.get("room_metadata") or {}
        defaults_raw = meta.get("operating_mode_defaults") or {}
        return cls(
            room_id=data.get("room_id", ""),
            last_operating_mode=meta.get("last_operating_mode"),
            operating_mode_defaults={k: CommandParams.from_json(v) for k, v in defaults_raw.items()},
            region_type=_enum_or_none(RegionType, meta.get("region_type")),
            name=meta.get("name"),
        )


@dataclass(frozen=True)
class P2MapVersion:
    """Bestaetigt (echte Live-Antwort, chairstacker): ersetzt die
    frueher falsche Docstring-Vermutung ("mindestens mapId/mapVersionId")
    -- der echte Primaerschluessel ist `p2map_id`, die Kartenversion
    heisst `active_p2mapv_id`. Ein Account kann mehrere P2MapVersion-
    Eintraege haben (im beobachteten Fall zwei: "Whole House" und
    "Master_Bathroom")."""

    p2map_id: str
    entity_type: str | None = None
    create_time: int | None = None
    robot_id: str | None = None
    sku: str | None = None
    active_p2mapv_id: str | None = None
    last_p2mapv_ts: int | None = None
    state: str | None = None
    visible: bool | None = None
    name: str | None = None
    rooms_metadata: list[RoomMetadataEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> P2MapVersion:
        return cls(
            p2map_id=data.get("p2map_id", ""),
            entity_type=data.get("entity_type"),
            create_time=data.get("create_time"),
            robot_id=data.get("robot_id"),
            sku=data.get("sku"),
            active_p2mapv_id=data.get("active_p2mapv_id"),
            last_p2mapv_ts=data.get("last_p2mapv_ts"),
            state=data.get("state"),
            visible=data.get("visible"),
            name=data.get("name"),
            rooms_metadata=[RoomMetadataEntry.from_json(r) for r in (data.get("rooms_metadata") or [])],
        )


def parse_active_map_versions(data: list[dict[str, Any]] | None) -> list[P2MapVersion]:
    """Wandelt die rohe get_active_map_versions()-Antwort in eine Liste
    typisierter P2MapVersion-Objekte um. NEU (26. Sitzung)."""
    if not data:
        return []
    return [P2MapVersion.from_json(entry) for entry in data]


@dataclass(frozen=True)
class RobotSerialInfo:
    """Bestaetigt (echte Live-Antwort, chairstacker,
    get_serial_number_data()). "family" beobachtet als "Roomba Combo"
    (Vakuum+Wisch-Kombigeraet), "series" als "G1". is_raas vermutlich
    "Robot as a Service" (Abo-/Leihmodell), is_smartcare vermutlich ein
    Wartungsvertrag-Flag -- beide Namen aus dem JSON uebernommen, ihre
    genaue Bedeutung nicht weiter untersucht."""

    robot_id: str | None = None
    serial_number: str | None = None
    built_as_sku: str | None = None
    family_variant: str | None = None
    is_raas: bool | None = None
    is_refurbished: bool | None = None
    is_smartcare: bool | None = None
    min_utc_reg_date: int | None = None
    name: str | None = None
    sku: str | None = None
    series: str | None = None
    family: str | None = None
    serial_history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotSerialInfo:
        return cls(
            robot_id=data.get("RobotID"),
            serial_number=data.get("SerialNumber"),
            built_as_sku=data.get("built_as_sku"),
            family_variant=data.get("family_variant"),
            is_raas=data.get("is_raas"),
            is_refurbished=data.get("is_refurbished"),
            is_smartcare=data.get("is_smartcare"),
            min_utc_reg_date=data.get("min_utc_reg_date"),
            name=data.get("name"),
            sku=data.get("sku"),
            series=data.get("series"),
            family=data.get("family"),
            serial_history=data.get("serial_history") or [],
        )


# =========================================================================
# RobotPart / RobotPartsInfo (27. Sitzung)
# =========================================================================
#
# STATUS: NEU. Bestaetigt aus echter get_robot_parts()-Antwort
# (chairstacker). Verschleissteil-/Wartungszaehler, z.B. fuer
# Wischpad-Waeschen, Absaugvorgaenge, oder zeitbasierte Nutzung (Filter/
# Buerste). counter_category beobachtet als "replacement" oder
# "maintenance"; reset_by als "user" oder "cloud".


@dataclass(frozen=True)
class RobotPart:
    """Bestaetigt (echte Live-Antwort): part_id, counter,
    minutes_remaining (-1 wenn nicht zeitbasiert), last_updated_ts
    (optional, nicht bei jedem Teil vorhanden), count_type (z.B.
    "combo_missions", "pad_washes_used", "minutes", "evacs"),
    count_remaining, count_used, counter_category ("replacement"/
    "maintenance"), reset_by ("user"/"cloud")."""

    part_id: str
    counter: int | None = None
    minutes_remaining: int | None = None
    last_updated_ts: int | None = None
    count_type: str | None = None
    count_remaining: int | None = None
    count_used: int | None = None
    counter_category: str | None = None
    reset_by: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotPart:
        return cls(
            part_id=data.get("part_id", ""),
            counter=data.get("counter"),
            minutes_remaining=data.get("minutes_remaining"),
            last_updated_ts=data.get("last_updated_ts"),
            count_type=data.get("count_type"),
            count_remaining=data.get("count_remaining"),
            count_used=data.get("count_used"),
            counter_category=data.get("counter_category"),
            reset_by=data.get("reset_by"),
        )


@dataclass(frozen=True)
class RobotPartsInfo:
    """Bestaetigt (echte Live-Antwort, get_robot_parts()): robot_id,
    num_parts, parts (Liste von RobotPart)."""

    robot_id: str | None = None
    num_parts: int | None = None
    parts: list[RobotPart] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotPartsInfo:
        return cls(
            robot_id=data.get("robot_id"),
            num_parts=data.get("num_parts"),
            parts=[RobotPart.from_json(p) for p in (data.get("parts") or [])],
        )


# =========================================================================
# Household / HouseholdRobot / HouseholdUser (28. Sitzung)
# =========================================================================
#
# STATUS: NEU. Bestaetigt aus echter get_user_households()-Antwort
# (chairstacker) -- der Endpunkt selbst war als "im aktuellen App-Code
# totes Gewebe, HTTP-Methode nur Konvention" dokumentiert, ANTWORTETE
# aber tatsaechlich korrekt. entity_id folgt einem "typ#id"-Muster
# ("robot#{blid}", "user#{cognito_id}").


@dataclass(frozen=True)
class HouseholdRobot:
    """Bestaetigt (echte Live-Antwort): household_id, entity_id
    (Format "robot#{robot_id}"), robot_id, creation_timestamp."""

    household_id: str | None = None
    entity_id: str | None = None
    robot_id: str | None = None
    creation_timestamp: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdRobot:
        return cls(
            household_id=data.get("household_id"),
            entity_id=data.get("entity_id"),
            robot_id=data.get("robot_id"),
            creation_timestamp=data.get("creation_timestamp"),
        )


@dataclass(frozen=True)
class HouseholdUser:
    """Bestaetigt (echte Live-Antwort): household_id, entity_id
    (Format "user#{cognito_id}"), cognito_id, creation_timestamp."""

    household_id: str | None = None
    entity_id: str | None = None
    cognito_id: str | None = None
    creation_timestamp: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HouseholdUser:
        return cls(
            household_id=data.get("household_id"),
            entity_id=data.get("entity_id"),
            cognito_id=data.get("cognito_id"),
            creation_timestamp=data.get("creation_timestamp"),
        )


@dataclass(frozen=True)
class Household:
    """Bestaetigt (echte Live-Antwort, get_user_households()):
    household_id, owner_cognito_id, household_name (beobachteter Wert
    "#AUTO_GENERATED_HOUSEHOLD#" -- legt nahe, dass die meisten Nutzer
    nie manuell einen Haushaltsnamen vergeben), has_precise_location,
    household_robots, household_users."""

    household_id: str | None = None
    owner_cognito_id: str | None = None
    household_name: str | None = None
    has_precise_location: bool | None = None
    household_robots: list[HouseholdRobot] = field(default_factory=list)
    household_users: list[HouseholdUser] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Household:
        return cls(
            household_id=data.get("household_id"),
            owner_cognito_id=data.get("owner_cognito_id"),
            household_name=data.get("household_name"),
            has_precise_location=data.get("has_precise_location"),
            household_robots=[HouseholdRobot.from_json(r) for r in (data.get("household_robots") or [])],
            household_users=[HouseholdUser.from_json(u) for u in (data.get("household_users") or [])],
        )


def parse_user_households(data: list[dict[str, Any]] | None) -> list[Household]:
    """Wandelt die rohe get_user_households()-Antwort in eine Liste
    typisierter Household-Objekte um. NEU (28. Sitzung)."""
    if not data:
        return []
    return [Household.from_json(entry) for entry in data]


# =========================================================================
# RobotSettings (32. Sitzung)
# =========================================================================
#
# STATUS: NEU. Bestaetigt aus echter get_settings()-Antwort (chairstacker,
# der "rw-settings"-benannte Shadow). Loest einen grossen Teil der zuvor
# in docs/API_REFERENCE.md als "entdeckt, aber unmodelliert" gelisteten
# Settings-Vokabelliste auf -- viele der dort nur als commandId vermuteten
# Einstellungen entsprechen direkt Feldern in dieser Antwort (SetChildLock
# -> childLock, SetAudioVolumePattern -> audio.volume,
# SetAutoEvacFrequency -> autoevacFreq, SetRobotLanguageV2 -> langs2,
# SetMapUploadAllowedCommand -> mapUploadAllowed, SetPadDryDuration ->
# padDryDur, u.a.). "langs2" bewusst als rohes dict belassen (verschachtelte
# Sprachlisten-Struktur, geringer Nutzen fuer ein eigenes Modell).


@dataclass(frozen=True)
class RobotSettings:
    """Bestaetigt (echte Live-Antwort, get_settings()): kompletter
    Inhalt des benannten "rw-settings"-Shadows fuer ein SMART-Tier-
    Geraet. Deckt u.a. Kindersicherung, Lautstaerke, Zeitzone,
    Wischpad-Einstellungen, Sprachliste, Auto-Evac-Frequenz und diverse
    "*Allowed"-Berechtigungs-Flags ab."""

    audio_volume: int | None = None
    autoevac_freq: int | None = None
    carpet_boost: bool | None = None
    child_lock: bool | None = None
    cloud_env: str | None = None
    country: str | None = None
    eco_charge: bool | None = None
    evac_allowed: bool | None = None
    map_upload_allowed: bool | None = None
    name: str | None = None
    no_auto_passes: bool | None = None
    nsmip: int | None = None
    pad_dry_allowed: int | None = None
    pad_dry_duration: int | None = None
    pad_wash_allowed: int | None = None
    pad_wash_area_interval: int | None = None
    pad_wash_return: int | None = None
    pad_wash_time_interval: int | None = None
    pad_wetness: PadWetnessParam | None = None
    sched_hold: bool | None = None
    scrub: int | None = None
    suction_level: int | None = None
    svc_deployment_id: str | None = None
    timezone: str | None = None
    two_pass: bool | None = None
    vac_high: bool | None = None
    languages_raw: dict[str, Any] | None = None
    """Rohes "langs2"-Objekt (aSlots, dLangs.langs/ver, sLang, sVer) --
    bewusst nicht weiter zerlegt, geringer Mehrwert fuer ein eigenes
    Modell."""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RobotSettings:
        audio = data.get("audio") or {}
        pad_wetness_data = data.get("padWetness")
        svc_endpoints = data.get("svcEndpoints") or {}
        return cls(
            audio_volume=audio.get("volume"),
            autoevac_freq=data.get("autoevacFreq"),
            carpet_boost=data.get("carpetBoost"),
            child_lock=data.get("childLock"),
            cloud_env=data.get("cloudEnv"),
            country=data.get("country"),
            eco_charge=data.get("ecoCharge"),
            evac_allowed=data.get("evacAllowed"),
            map_upload_allowed=data.get("mapUploadAllowed"),
            name=data.get("name"),
            no_auto_passes=data.get("noAutoPasses"),
            nsmip=data.get("nsmip"),
            pad_dry_allowed=data.get("padDryAllowed"),
            pad_dry_duration=data.get("padDryDur"),
            pad_wash_allowed=data.get("padWashAllowed"),
            pad_wash_area_interval=data.get("pwAreaInterval"),
            pad_wash_return=data.get("pwReturn"),
            pad_wash_time_interval=data.get("pwTimeInterval"),
            pad_wetness=PadWetnessParam.from_json(pad_wetness_data) if pad_wetness_data else None,
            sched_hold=data.get("schedHold"),
            scrub=data.get("swScrub"),
            suction_level=data.get("suctionLevel"),
            svc_deployment_id=svc_endpoints.get("svcDeplId"),
            timezone=data.get("timezone"),
            two_pass=data.get("twoPass"),
            vac_high=data.get("vacHigh"),
            languages_raw=data.get("langs2"),
        )
