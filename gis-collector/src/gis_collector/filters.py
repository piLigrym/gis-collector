"""Post-collection feature filters (Strategy pattern)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from shapely import prepared
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from gis_collector.geo.geojson import representative_lonlat
from gis_collector.models import Feature

logger = logging.getLogger(__name__)


class FeatureFilter(Protocol):
    def accept(self, feature: Feature) -> bool: ...


class RegionPolygonFilter:
    """Keeps features whose representative point lies inside the region polygon.

    Used where a BBOX (or a tile grid) over-selects: neighbouring oblasts,
    Belarus, tiles that only touch the region.
    """

    def __init__(self, region: BaseGeometry, *, keep_without_geometry: bool = False) -> None:
        self._region = prepared.prep(region)
        self._keep_without_geometry = keep_without_geometry

    def accept(self, feature: Feature) -> bool:
        if feature.geometry is None:
            return self._keep_without_geometry
        point = representative_lonlat(feature.geometry)
        if point is None:
            return self._keep_without_geometry
        return bool(self._region.intersects(Point(*point)))


class CompositeFilter:
    """Logical AND of several filters."""

    def __init__(self, filters: Sequence[FeatureFilter]) -> None:
        self._filters = list(filters)

    def accept(self, feature: Feature) -> bool:
        return all(f.accept(feature) for f in self._filters)
