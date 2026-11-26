"""Vector-tile (MVT / PBF) endpoints with ``{z}/{x}/{y}`` URL templates."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from shapely.ops import unary_union

from gis_collector.concurrency import parallel_map
from gis_collector.exceptions import GisCollectorError, HttpStatusError
from gis_collector.geo.geojson import GeoJSON, from_shapely, round_coordinates, to_shapely
from gis_collector.geo.mvt import DecodedFeature, decode_tile
from gis_collector.geo.tiles import Tile, tiles_covering
from gis_collector.models import EndpointSpec, Feature, SourceSpec
from gis_collector.projection import AttributeProjector
from gis_collector.strategies.base import CollectionStrategy, CollectionTask, RunContext

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Accumulator:
    """All tile fragments of one logical object (polygons are cut at tile borders)."""

    layer: str
    feature_id: int | None
    properties: dict[str, Any]
    geometries: list[GeoJSON] = field(default_factory=list)


class TileStrategy(CollectionStrategy):
    name = "mvt_tiles"

    def supports(self, endpoint: EndpointSpec) -> bool:
        return endpoint.is_tile_template

    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        endpoint, source, settings = task.endpoint, task.source, ctx.settings
        layers = set(endpoint.geometry.layers) if endpoint.geometry else set()
        primary_layer = next(iter(layers), None)
        zoom = self._zoom(source, endpoint, primary_layer, ctx)

        region_name = (
            source.area_constraint.name if source.area_constraint else settings.default_region_name
        )
        region = ctx.boundary.region(region_name)
        tiles = tiles_covering(region, zoom, max_tiles=settings.max_tiles)
        logger.info("[%s] %s: %d tiles at z%d", source.source_id, endpoint.url, len(tiles), zoom)

        headers = ctx.headers(source, endpoint)

        def fetch(tile: Tile) -> list[DecodedFeature]:
            z, x, y = tile
            url = endpoint.url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            try:
                response = ctx.http.get(url, params=endpoint.params or None, headers=headers)
            except HttpStatusError as exc:
                if exc.status_code in (404, 410):  # empty / missing tile
                    return []
                raise
            if not response.content:
                return []
            return decode_tile(response.content, z, x, y, layers=layers or None)

        tile_results = parallel_map(
            fetch,
            tiles,
            workers=settings.max_workers,
            max_error_ratio=settings.max_error_ratio,
            label=f"tile[{source.source_id}]",
        )

        merged = self._merge(tile_results, ctx)
        logger.info("[%s] %s → %d unique objects", source.source_id, endpoint.url, len(merged))

        for key, acc in merged.items():
            projector = AttributeProjector(
                endpoint.attributes_for_layer(acc.layer), include_undeclared=True
            )
            properties = projector.project(acc.properties)
            id_field = settings.tile_id_fields.get(acc.layer)
            if id_field and id_field in properties:
                ctx.shared.setdefault("values", {}).setdefault(id_field, []).append(
                    properties[id_field]
                )
            yield ctx.factory.build(
                source.source_id,
                endpoint,
                self._combine(acc.geometries),
                properties,
                layer=acc.layer,
                feature_id=key[2] if key[0] == "id" else acc.feature_id,
                extra_meta={"tile_zoom": zoom},
            )

    # ----------------------------------------------------------------------------

    def _merge(
        self, tile_results: list[list[DecodedFeature] | None], ctx: RunContext
    ) -> dict[tuple[str, str, Any], _Accumulator]:
        merged: dict[tuple[str, str, Any], _Accumulator] = {}
        for decoded in tile_results:
            for item in decoded or ():
                key = self._identity(item, ctx)
                acc = merged.get(key)
                if acc is None:
                    acc = merged[key] = _Accumulator(
                        item.layer, item.feature_id, dict(item.properties)
                    )
                if item.geometry:
                    acc.geometries.append(item.geometry)
        return merged

    @staticmethod
    def _identity(item: DecodedFeature, ctx: RunContext) -> tuple[str, str, Any]:
        id_field = ctx.settings.tile_id_fields.get(item.layer)
        if id_field and item.properties.get(id_field) not in (None, ""):
            return ("id", item.layer, item.properties[id_field])
        if item.feature_id is not None:
            return ("mvt", item.layer, item.feature_id)
        fingerprint = json.dumps(
            [item.properties, round_coordinates((item.geometry or {}).get("coordinates"), 6)],
            sort_keys=True,
            default=str,
        )
        return ("hash", item.layer, fingerprint)

    @staticmethod
    def _combine(geometries: list[GeoJSON]) -> GeoJSON | None:
        if not geometries:
            return None
        if len(geometries) == 1:
            return geometries[0]
        return from_shapely(unary_union([to_shapely(g) for g in geometries]))

    # ----------------------------------------------------------------------------

    def _zoom(
        self, source: SourceSpec, endpoint: EndpointSpec, layer: str | None, ctx: RunContext
    ) -> int:
        settings = ctx.settings
        zoom = settings.tile_zoom_by_layer.get(layer or "", settings.default_tile_zoom)
        if not settings.use_style_zoom_limits:
            return zoom
        limits = self._style_limits(source, endpoint, ctx)
        if limits is None:
            return zoom
        low, high = limits
        clamped = max(low, min(high, zoom))
        if clamped != zoom:
            logger.info(
                "[%s] zoom %d clamped to %d by styles.json", source.source_id, zoom, clamped
            )
        return clamped

    @staticmethod
    def _style_limits(
        source: SourceSpec, endpoint: EndpointSpec, ctx: RunContext
    ) -> tuple[int, int] | None:
        """min/max zoom of the tile *source* declared in the sibling styles.json."""
        metadata = [e for e in source.endpoints if e.access_class == "public_metadata"]
        if not metadata:
            return None
        style_ep = metadata[0]
        cache: dict[str, Any] = ctx.shared.setdefault("styles", {})
        if style_ep.url not in cache:
            try:
                cache[style_ep.url] = ctx.http.get(
                    style_ep.url, headers=ctx.headers(source, style_ep)
                ).json()
            except GisCollectorError as exc:
                logger.warning("styles.json unavailable (%s); using configured zoom", exc)
                cache[style_ep.url] = None
        style = cache[style_ep.url]
        if not isinstance(style, dict):
            return None
        marker = urlsplit(endpoint.url).path.split("{z}")[0]
        for tile_source in (style.get("sources") or {}).values():
            tiles = tile_source.get("tiles") or [] if isinstance(tile_source, dict) else []
            if any(marker in str(url) for url in tiles):
                low, high = tile_source.get("minzoom"), tile_source.get("maxzoom")
                if isinstance(low, int | float) or isinstance(high, int | float):
                    return int(low) if low is not None else 0, int(high) if high is not None else 22
        return None
