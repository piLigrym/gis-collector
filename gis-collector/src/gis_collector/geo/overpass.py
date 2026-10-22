"""Overpass JSON (``out geom``) → GeoJSON geometry.

Element shapes handled (see specs/overpass.json):

* ``node``     – ``lat``/``lon``
* ``way``      – ``geometry`` = ``[{"lat":..,"lon":..}, ...]``; closed ways of "area"
                 features become Polygons, everything else LineStrings
* ``relation`` – ``members[]`` with their own ``geometry``; ``multipolygon`` and
                 ``boundary`` relations are assembled into (Multi)Polygons, other
                 relations into a GeometryCollection of their members
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from gis_collector.geo.geojson import GeoJSON, from_shapely, is_valid_lonlat, make_point

logger = logging.getLogger(__name__)

#: Tag keys that make a *closed* way an area (simplified osmtogeojson rules).
AREA_KEYS = frozenset(
    {
        "landuse",
        "building",
        "ruins",
        "leisure",
        "amenity",
        "natural",
        "military",
        "industrial",
        "man_made",
        "shop",
        "tourism",
        "historic",
        "place",
        "boundary",
        "area:highway",
    }
)

Coord = tuple[float, float]


def _coords(points: Sequence[Mapping[str, Any] | None] | None) -> list[Coord]:
    out: list[Coord] = []
    for point in points or ():
        if not point:  # ``null`` entries appear when a member lies outside the bbox
            continue
        lon, lat = point.get("lon"), point.get("lat")
        if (
            isinstance(lon, int | float)
            and isinstance(lat, int | float)
            and is_valid_lonlat(lon, lat)
        ):
            out.append((float(lon), float(lat)))
    return out


def is_area(tags: Mapping[str, Any]) -> bool:
    """Does a closed way with these tags describe an area (not a ring-shaped line)?"""
    area = tags.get("area")
    if area == "yes":
        return True
    if area == "no":
        return False
    return any(key in AREA_KEYS for key in tags)


def _way_geometry(element: Mapping[str, Any]) -> GeoJSON | None:
    coords = _coords(element.get("geometry"))
    if len(coords) < 2:
        return None
    tags = element.get("tags") or {}
    closed = len(coords) >= 4 and coords[0] == coords[-1]
    if closed and is_area(tags):
        return from_shapely(Polygon(coords))
    return from_shapely(LineString(coords))


def _lines(members: Sequence[Mapping[str, Any]], roles: set[str]) -> list[LineString]:
    lines: list[LineString] = []
    for member in members:
        if member.get("type") != "way" or (member.get("role") or "") not in roles:
            continue
        coords = _coords(member.get("geometry"))
        if len(coords) >= 2:
            lines.append(LineString(coords))
    return lines


def _faces(lines: list[LineString]) -> BaseGeometry | None:
    """Union of all closed rings that can be built from (possibly split) ways."""
    if not lines:
        return None
    polygons = list(polygonize(unary_union(lines)))
    return unary_union(polygons) if polygons else None


def _multipolygon_geometry(element: Mapping[str, Any]) -> GeoJSON | None:
    members = element.get("members") or []
    outer = _faces(_lines(members, {"outer", ""}))
    if outer is None:
        return None
    inner = _faces(_lines(members, {"inner"}))
    result = outer.difference(inner) if inner is not None else outer
    if result.is_empty:
        return None
    if not isinstance(result, Polygon | MultiPolygon):
        polygons = [g for g in getattr(result, "geoms", []) if isinstance(g, Polygon)]
        if not polygons:
            return None
        result = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    return from_shapely(result)


def _relation_geometry(element: Mapping[str, Any]) -> GeoJSON | None:
    tags = element.get("tags") or {}
    members = element.get("members") or []
    if tags.get("type") in {"multipolygon", "boundary"} or (
        is_area(tags) and any(m.get("role") in {"outer", "inner"} for m in members)
    ):
        geometry = _multipolygon_geometry(element)
        if geometry is not None:
            return geometry
        logger.debug(
            "Relation %s: no closed outer ring, falling back to members", element.get("id")
        )

    parts: list[BaseGeometry] = []
    for member in members:
        if member.get("type") == "node":
            lon, lat = member.get("lon"), member.get("lat")
            if (
                isinstance(lon, int | float)
                and isinstance(lat, int | float)
                and make_point(float(lon), float(lat))
            ):
                parts.append(Point(float(lon), float(lat)))
        elif member.get("type") == "way":
            coords = _coords(member.get("geometry"))
            if len(coords) >= 2:
                parts.append(LineString(coords))
    if not parts:
        return None
    return from_shapely(GeometryCollection(parts))


def element_geometry(element: Mapping[str, Any]) -> GeoJSON | None:
    """GeoJSON geometry (WGS84) of an Overpass element, or ``None``."""
    kind = element.get("type")
    if kind == "node":
        lon, lat = element.get("lon"), element.get("lat")
        if isinstance(lon, int | float) and isinstance(lat, int | float):
            return make_point(float(lon), float(lat))
        return None
    if kind == "way":
        return _way_geometry(element)
    if kind == "relation":
        return _relation_geometry(element)
    return None
