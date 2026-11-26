"""Single place where output :class:`Feature` objects are created."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from gis_collector.geo.geojson import GeoJSON, normalize_geometry
from gis_collector.models import EndpointSpec, Feature
from gis_collector.settings import Settings


class FeatureFactory:
    """Normalises geometry and attaches provenance (``properties["_meta"]``)."""

    def __init__(self, settings: Settings, *, started_at: datetime | None = None) -> None:
        self._settings = settings
        self._fetched_at = (started_at or datetime.now(UTC)).isoformat(timespec="seconds")

    def build(
        self,
        source_id: str,
        endpoint: EndpointSpec,
        geometry: GeoJSON | None,
        properties: dict[str, Any],
        *,
        layer: str | None = None,
        feature_id: Any = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> Feature:
        props = dict(properties)
        if self._settings.include_meta:
            meta: dict[str, Any] = {
                "endpoint": endpoint.url,
                "fetched_at": self._fetched_at,
            }
            if layer:
                meta["layer"] = layer
            if feature_id is not None:
                meta["feature_id"] = feature_id
            if extra_meta:
                meta.update(extra_meta)
            props["_meta"] = meta
        return Feature(
            source_id=source_id,
            geometry=normalize_geometry(geometry, precision=self._settings.coordinate_precision),
            properties=props,
        )
