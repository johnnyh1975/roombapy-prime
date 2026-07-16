"""Geometry primitives (GeoJSON: Position/Point/LineString/Polygon/MultiPolygon).

Part of roombapy_prime.models (split into a package for navigability,
session 55). See roombapy_prime/models/__init__.py for the full
picture and docs/PRIME_APP_GAP_ANALYSIS_2026-07-11.md for the
evidence trail behind any individual field."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


Position = tuple[float, float]  # (x, y) -- z isn't used anywhere so far


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
    """coordinates: list of rings, each ring a list of Position.
    First ring = outer boundary, further ones = holes (standard
    GeoJSON, never observed here in the wild with more than one
    ring)."""

    coordinates: list[list[Position]]

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "Polygon",
            "coordinates": [[_position_to_raw(p) for p in ring] for ring in self.coordinates],
        }


@dataclass(frozen=True)
class MultiPolygon:
    """coordinates: list of Polygon -- confirmed in
    MultiPolygon.java (extends Geometry, type="MultiPolygon",
    coordinates: List<Polygon>). Only needed for read models
    (BorderInfo, CoverageInfo) -- no edit command uses this so far."""

    coordinates: list[Polygon]

    def to_geojson(self) -> dict[str, Any]:
        return {"type": "MultiPolygon", "coordinates": [p.to_geojson()["coordinates"] for p in self.coordinates]}


def _point_from_geojson(data: dict[str, Any]) -> Point:
    coords = data.get("coordinates") or [0.0, 0.0]
    return Point(coordinates=tuple(coords[:2]))


def _linestring_from_geojson(data: dict[str, Any]) -> LineString:
    coords = data.get("coordinates") or []
    return LineString(coordinates=[tuple(p) for p in coords])


def _polygon_from_geojson(data: dict[str, Any]) -> Polygon:
    coords = data.get("coordinates") or []
    return Polygon(coordinates=[[tuple(p) for p in ring] for ring in coords])


def _multipolygon_from_geojson(data: dict[str, Any]) -> MultiPolygon:
    coords = data.get("coordinates") or []
    return MultiPolygon(coordinates=[Polygon(coordinates=[[tuple(p) for p in ring] for ring in poly]) for poly in coords])


