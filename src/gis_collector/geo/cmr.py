"""NASA CMR (``granules.json``) spatial extents → GeoJSON.

CMR serialises spatial data as space separated strings in **lat lon** order
(``"50.1 30.2 50.3 30.4 ..."``) — the opposite of GeoJSON. The spec says
``x: lon, y: lat``, which describes the *output* convention; the decoder
therefore converts, and defensively swaps when the first value cannot be a
latitude.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gis_collector.geo.geojson import GeoJSON, is_valid_lonlat

Pair = list[float]

SPATIAL_KEYS = ("polygons", "boxes", "points", "lines")


def _pairs(text: str) -> list[Pair]:
    """Parse ``"lat lon lat lon ..."`` into ``[[lon, lat], ...]``."""
    try:
        numbers = [float(token) for token in text.split()]
    except ValueError:
        return []
    if len(numbers) < 2 or len(numbers) % 2:
        return []
    pairs = list(zip(numbers[0::2], numbers[1::2], strict=True))  # (lat, lon)
    if any(abs(first) > 90 for first, _ in pairs):  # not a latitude → already lon,lat
        pairs = [(second, first) for first, second in pairs]
    return [[lon, lat] for lat, lon in pairs if is_valid_lonlat(lon, lat)]


def _ring(text: str) -> list[Pair] | None:
    ring = _pairs(text)
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring if len(ring) >= 4 else None


def _polygons(entry: Mapping[str, Any]) -> list[list[list[Pair]]]:
    polygons: list[list[list[Pair]]] = []
    for polygon in entry.get("polygons") or []:
        rings = [r for r in (_ring(text) for text in polygon) if r]
        if rings:
            polygons.append(rings)  # first ring = exterior, the rest = holes
    for box in entry.get("boxes") or []:
        try:
            south, west, north, east = (float(v) for v in box.split())
        except ValueError:
            continue
        ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
        if all(is_valid_lonlat(lon, lat) for lon, lat in ring):
            polygons.append([ring])
    return polygons


def granule_geometry(entry: Mapping[str, Any]) -> GeoJSON | None:
    """Spatial extent of a CMR granule entry as GeoJSON (polygon > line > point)."""
    polygons = _polygons(entry)
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    if polygons:
        return {"type": "MultiPolygon", "coordinates": polygons}

    lines = [line for line in (_pairs(t) for t in entry.get("lines") or []) if len(line) >= 2]
    if len(lines) == 1:
        return {"type": "LineString", "coordinates": lines[0]}
    if lines:
        return {"type": "MultiLineString", "coordinates": lines}

    points = [p[0] for p in (_pairs(t) for t in entry.get("points") or []) if p]
    if len(points) == 1:
        return {"type": "Point", "coordinates": points[0]}
    if points:
        return {"type": "MultiPoint", "coordinates": points}
    return None


def has_spatial_keys(item: Mapping[str, Any]) -> bool:
    return any(key in item for key in SPATIAL_KEYS)
