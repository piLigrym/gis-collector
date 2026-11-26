"""GeoJSON helpers on top of shapely: validation, repair, orientation, rounding."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import shapely
from shapely.errors import ShapelyError
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.validation import make_valid

logger = logging.getLogger(__name__)

GeoJSON = dict[str, Any]


def is_valid_lonlat(lon: float, lat: float) -> bool:
    return (
        math.isfinite(lon)
        and math.isfinite(lat)
        and -180.0 <= lon <= 180.0
        and -90.0 <= lat <= 90.0
    )


def make_point(lon: float, lat: float) -> GeoJSON | None:
    """GeoJSON Point or ``None`` when the coordinates are not valid WGS84."""
    if not is_valid_lonlat(lon, lat):
        return None
    return {"type": "Point", "coordinates": [lon, lat]}


def round_coordinates(value: Any, ndigits: int) -> Any:
    """Recursively round every number of a GeoJSON coordinates structure."""
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, list | tuple):
        return [round_coordinates(item, ndigits) for item in value]
    return value


def to_shapely(geometry: GeoJSON) -> BaseGeometry:
    return shape(geometry)


def from_shapely(geometry: BaseGeometry) -> GeoJSON:
    """Shapely → GeoJSON dict with plain lists (JSON friendly)."""
    result: GeoJSON = json.loads(shapely.to_geojson(geometry))
    return result


def _orient_rfc7946(geometry: BaseGeometry) -> BaseGeometry:
    """Exterior rings counter-clockwise, holes clockwise (RFC 7946 §3.1.6)."""
    if isinstance(geometry, Polygon):
        return orient(geometry, sign=1.0)
    if isinstance(geometry, MultiPolygon):
        return MultiPolygon([orient(p, sign=1.0) for p in geometry.geoms])
    return geometry


def _polygonal_part(geometry: BaseGeometry) -> BaseGeometry:
    """Keep only polygons of a (possibly mixed) result of ``make_valid``."""
    if isinstance(geometry, Polygon | MultiPolygon):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons: list[Polygon] = []
        for part in geometry.geoms:
            if isinstance(part, Polygon):
                polygons.append(part)
            elif isinstance(part, MultiPolygon):
                polygons.extend(part.geoms)
        if polygons:
            return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    return geometry


def normalize_geometry(geometry: GeoJSON | None, *, precision: int = 7) -> GeoJSON | None:
    """Validate, repair and canonicalise a GeoJSON geometry.

    Returns ``None`` (and logs at debug level) when the geometry is unusable:
    empty, malformed, or outside the WGS84 range. Invalid polygons are repaired
    with ``make_valid``; rings are re-oriented per RFC 7946; coordinates rounded.
    """
    if not geometry:
        return None
    try:
        geom = shape(geometry)
    except (ValueError, TypeError, KeyError, AttributeError, ShapelyError) as exc:
        logger.debug("Unparseable geometry dropped: %s", exc)
        return None
    if geom.is_empty:
        return None
    min_x, min_y, max_x, max_y = geom.bounds
    if not (is_valid_lonlat(min_x, min_y) and is_valid_lonlat(max_x, max_y)):
        logger.debug("Geometry outside WGS84 range dropped: bounds=%s", geom.bounds)
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
        if isinstance(geometry.get("type"), str) and "Polygon" in geometry["type"]:
            geom = _polygonal_part(geom)
        if geom.is_empty:
            return None
    geom = _orient_rfc7946(geom)
    result = from_shapely(geom)
    if "coordinates" in result:
        result["coordinates"] = round_coordinates(result["coordinates"], precision)
    elif result.get("type") == "GeometryCollection":
        for part in result["geometries"]:
            part["coordinates"] = round_coordinates(part.get("coordinates"), precision)
    return result


def representative_lonlat(geometry: GeoJSON) -> tuple[float, float] | None:
    """A point guaranteed to lie on the geometry (used for region filtering)."""
    try:
        geom = shape(geometry)
    except (ValueError, TypeError, KeyError, AttributeError, ShapelyError):
        return None
    if geom.is_empty:
        return None
    if isinstance(geom, Point):
        return geom.x, geom.y
    point = geom.representative_point()
    return point.x, point.y
