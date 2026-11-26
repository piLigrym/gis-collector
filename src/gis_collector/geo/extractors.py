"""Geometry extraction for plain JSON items, driven by ``endpoint.geometry``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from gis_collector.geo.cmr import granule_geometry, has_spatial_keys
from gis_collector.geo.geojson import GeoJSON, make_point
from gis_collector.jsonpath import extract_one
from gis_collector.models import GeometrySpec


class GeometryExtractor(Protocol):
    def __call__(self, item: Mapping[str, Any]) -> GeoJSON | None: ...


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SpecGeometryExtractor:
    """Picks the right way to read geometry from an item, in order:

    1. ``geometry.path`` pointing to a GeoJSON geometry object;
    2. ``geometry.x`` / ``geometry.y`` naming two coordinate fields;
    3. CMR-style spatial keys (``polygons`` / ``boxes`` / ``points`` / ``lines``).
    """

    def __init__(self, spec: GeometrySpec | None) -> None:
        self._spec = spec

    def __call__(self, item: Mapping[str, Any]) -> GeoJSON | None:
        spec = self._spec
        if spec is None:
            return None
        if spec.path:
            node = extract_one(item, spec.path)
            if isinstance(node, dict) and "type" in node and "coordinates" in node:
                return node
            return None
        if spec.x and spec.y and spec.x in item and spec.y in item:
            lon, lat = _to_float(item[spec.x]), _to_float(item[spec.y])
            if lon is None or lat is None:
                return None
            return make_point(lon, lat)
        if has_spatial_keys(item):
            return granule_geometry(item)
        return None

    @property
    def consumed_keys(self) -> frozenset[str]:
        """Item keys that carry geometry and need not be repeated in properties."""
        spec = self._spec
        if spec is None:
            return frozenset()
        keys: set[str] = set()
        if spec.path:
            keys.add(spec.path.removeprefix("$.").split(".")[0].split("[")[0])
        if spec.x and spec.y:
            keys.update({spec.x, spec.y})
        if spec.type == "polygon":
            keys.update({"polygons", "boxes", "points", "lines"})
        return frozenset(keys)
