"""XYZ (slippy-map / Web Mercator) tile mathematics."""

from __future__ import annotations

import math
from collections.abc import Iterator

from shapely import prepared
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from gis_collector.exceptions import GeometryError

MAX_LAT = 85.05112878  # Web Mercator limit

Tile = tuple[int, int, int]  # z, x, y


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Tile (x, y) containing a WGS84 point at zoom *z*."""
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile_x_to_lon(x: float, z: int) -> float:
    return float(x / (2**z) * 360.0 - 180.0)


def tile_y_to_lat(y: float, z: int) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / (2**z)))))


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a tile in degrees."""
    return (
        tile_x_to_lon(x, z),
        tile_y_to_lat(y + 1, z),
        tile_x_to_lon(x + 1, z),
        tile_y_to_lat(y, z),
    )


def tile_point_to_lonlat(
    z: int, x: int, y: int, px: float, py: float, extent: int
) -> tuple[float, float]:
    """Tile-local coordinates (0..extent, y down) → WGS84 (lon, lat)."""
    return (
        tile_x_to_lon(x + px / extent, z),
        tile_y_to_lat(y + py / extent, z),
    )


def tiles_covering(region: BaseGeometry, z: int, *, max_tiles: int) -> list[Tile]:
    """All tiles at zoom *z* whose footprint intersects *region* (WGS84 geometry)."""
    west, south, east, north = region.bounds
    x_min, y_max = lonlat_to_tile(west, south, z)
    x_max, y_min = lonlat_to_tile(east, north, z)
    candidates = (x_max - x_min + 1) * (y_max - y_min + 1)
    if candidates > max_tiles * 4:
        raise GeometryError(
            f"Region needs ~{candidates} candidate tiles at z{z}; lower the zoom "
            f"or raise Settings.max_tiles"
        )
    ready = prepared.prep(region)
    tiles: list[Tile] = []
    for tile in _iter_candidates(z, x_min, x_max, y_min, y_max):
        _, tx, ty = tile
        if ready.intersects(box(*tile_bounds(z, tx, ty))):
            tiles.append(tile)
            if len(tiles) > max_tiles:
                raise GeometryError(
                    f"More than {max_tiles} tiles at z{z} intersect the region; "
                    f"lower the zoom or raise Settings.max_tiles"
                )
    return tiles


def _iter_candidates(z: int, x_min: int, x_max: int, y_min: int, y_max: int) -> Iterator[Tile]:
    for y in range(y_min, y_max + 1):
        for x in range(x_min, x_max + 1):
            yield z, x, y
