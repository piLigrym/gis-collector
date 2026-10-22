"""Region boundary providers (Dependency Inversion: filters and tile planning only
see :class:`BoundaryProvider`).

Resolution chain used in production:

1. a local GeoJSON file (``Settings.boundary_file``) — offline / reproducible;
2. Nominatim lookup of the OSM relation (e.g. ``R71249`` for Chernihiv oblast);
3. the rectangular BBOX from the settings (coarse; logged as a warning).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry

from gis_collector.exceptions import BoundaryError, GisCollectorError
from gis_collector.http.client import HttpClient
from gis_collector.settings import Settings

logger = logging.getLogger(__name__)

NOMINATIM_LOOKUP = "https://nominatim.openstreetmap.org/lookup"


class BoundaryProvider(Protocol):
    def region(self, name: str) -> BaseGeometry:
        """WGS84 polygon (lon, lat) of the named region."""
        ...


def _geometry_from_geojson(document: Any) -> BaseGeometry:
    """Accept Geometry, Feature, FeatureCollection or Nominatim ``jsonv2`` lists."""
    if isinstance(document, list):  # Nominatim jsonv2
        if not document or "geojson" not in document[0]:
            raise BoundaryError("Nominatim returned no polygon")
        return shape(document[0]["geojson"])
    if not isinstance(document, dict):
        raise BoundaryError("Unsupported boundary document")
    kind = document.get("type")
    if kind == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in document.get("features", []) if f.get("geometry")]
        if not geoms:
            raise BoundaryError("Boundary FeatureCollection is empty")
        from shapely.ops import unary_union

        return unary_union(geoms)
    if kind == "Feature":
        return shape(document["geometry"])
    return shape(document)


class FileBoundaryProvider:
    def __init__(self, path: Path) -> None:
        self._path = path

    def region(self, name: str) -> BaseGeometry:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            return _geometry_from_geojson(document)
        except (OSError, ValueError, KeyError) as exc:
            raise BoundaryError(f"Cannot read boundary file {self._path}: {exc}") from exc


class NominatimBoundaryProvider:
    def __init__(self, http: HttpClient, relation_ids: Mapping[str, int]) -> None:
        self._http = http
        self._relation_ids = dict(relation_ids)

    def region(self, name: str) -> BaseGeometry:
        relation_id = self._relation_ids.get(name)
        if relation_id is None:
            raise BoundaryError(f"No OSM relation id configured for region {name!r}")
        try:
            response = self._http.get(
                NOMINATIM_LOOKUP,
                params={"osm_ids": f"R{relation_id}", "format": "geojson", "polygon_geojson": 1},
            )
            return _geometry_from_geojson(response.json())
        except GisCollectorError as exc:
            raise BoundaryError(f"Nominatim lookup for R{relation_id} failed: {exc}") from exc


class BBoxBoundaryProvider:
    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        self._geometry = box(*bbox)

    def region(self, name: str) -> BaseGeometry:
        return self._geometry


class ChainBoundaryProvider:
    """Tries providers in order; caches the first successful answer per region."""

    def __init__(self, providers: Sequence[BoundaryProvider]) -> None:
        self._providers = list(providers)
        self._cache: dict[str, BaseGeometry] = {}
        self._lock = threading.Lock()

    def region(self, name: str) -> BaseGeometry:
        with self._lock:
            cached = self._cache.get(name)
            if cached is not None:
                return cached
            errors: list[str] = []
            for provider in self._providers:
                try:
                    geometry = provider.region(name)
                except BoundaryError as exc:
                    errors.append(f"{type(provider).__name__}: {exc}")
                    logger.warning("Boundary provider failed: %s", exc)
                    continue
                if isinstance(provider, BBoxBoundaryProvider):
                    logger.warning(
                        "Using the rectangular BBOX instead of the real boundary of %r: "
                        "features from neighbouring regions may slip through",
                        name,
                    )
                self._cache[name] = geometry
                return geometry
            raise BoundaryError(f"No boundary for {name!r}: {'; '.join(errors)}")


def build_boundary_provider(settings: Settings, http: HttpClient) -> BoundaryProvider:
    providers: list[BoundaryProvider] = []
    if settings.boundary_file is not None:
        providers.append(FileBoundaryProvider(settings.boundary_file))
    providers.append(NominatimBoundaryProvider(http, settings.region_relation_ids))
    if settings.allow_bbox_boundary_fallback:
        providers.append(BBoxBoundaryProvider(settings.region_bbox))
    return ChainBoundaryProvider(providers)
