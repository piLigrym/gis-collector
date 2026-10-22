"""DREAM public API: cursor-paginated *index* + per-record *detail* endpoints.

Flow (see specs/dream_damage_chernihiv.json):

1. page through the index with ``?from=<external.updated of the last record>``
   until a short / empty page (1000 records per page, not configurable);
2. fetch each record's detail in parallel;
3. keep only records located in the target region — the server has no region
   filter, so it is applied client-side by KATOTTG prefix (``UA74``);
4. coordinates are present for few records; when absent, ``geometry`` is ``None``
   and the KATOTTG codes / address stay in the properties for later geocoding.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote

from gis_collector.concurrency import parallel_map
from gis_collector.exceptions import DecodeError, HttpStatusError
from gis_collector.geo.geojson import GeoJSON, is_valid_lonlat
from gis_collector.jsonpath import extract, extract_one
from gis_collector.models import ClientFilterSpec, EndpointSpec, Feature
from gis_collector.projection import AttributeProjector
from gis_collector.strategies.base import (
    CollectionStrategy,
    CollectionTask,
    RunContext,
    is_mapping,
    iter_container_items,
)

logger = logging.getLogger(__name__)

_IDENTIFIERS_SUFFIX = ".identifiers[*]"
_OBJECT_GEOMETRIES = "dream_object_geometries"


# ------------------------------------------------------------------------ filtering


def region_codes(payload: Any, spec: ClientFilterSpec) -> list[str]:
    """Codes of the filter's scheme found in *payload* (OCDS ``gazetteer`` shapes)."""
    codes: list[str] = []

    def add(identifier: Any, parent_scheme: str | None) -> None:
        scheme, code = parent_scheme, identifier
        if isinstance(identifier, Mapping):
            scheme = identifier.get("scheme", parent_scheme)
            code = identifier.get("id")
        if code is None or isinstance(code, Mapping | list):
            return
        if spec.scheme is None or scheme in (None, spec.scheme):
            codes.append(str(code))

    if spec.field.endswith(_IDENTIFIERS_SUFFIX):
        # gazetteer = {"scheme": "UA-CATOTTG", "identifiers": ["UA74...", ...]}
        for parent in extract(payload, spec.field.removesuffix(_IDENTIFIERS_SUFFIX)):
            if isinstance(parent, Mapping):
                for identifier in parent.get("identifiers") or []:
                    add(identifier, parent.get("scheme"))
    else:
        for value in extract(payload, spec.field):
            add(value, None)
    return codes


def matches_region(payload: Any, spec: ClientFilterSpec | None) -> tuple[bool, list[str]]:
    if spec is None:
        return True, []
    codes = region_codes(payload, spec)
    if spec.match == "prefix":
        ok = any(code.startswith(spec.value) for code in codes)
    else:
        ok = spec.value in codes
    return ok, codes


# ------------------------------------------------------------------------- geometry


def coordinates_geometry(payload: Any, path: str | None) -> GeoJSON | None:
    """Point / MultiPoint from ``...geometry.coordinates`` values (strings, [lon, lat])."""
    if not path:
        return None
    points: list[list[float]] = []
    for coordinates in extract(payload, path):
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        try:
            lon, lat = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            continue
        if is_valid_lonlat(lon, lat) and [lon, lat] not in points:
            points.append([lon, lat])
    if not points:
        return None
    if len(points) == 1:
        return {"type": "Point", "coordinates": points[0]}
    return {"type": "MultiPoint", "coordinates": points}


# ---------------------------------------------------------------------------- strategy


class DreamStrategy(CollectionStrategy):
    name = "cursor_index_detail"

    def supports(self, endpoint: EndpointSpec) -> bool:
        return endpoint.role == "index"

    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        index, source, settings = task.endpoint, task.source, ctx.settings
        detail = self._find_detail(index, task.siblings)
        headers = ctx.headers(source, index)

        entries = list(self._iter_index(index, headers, ctx))
        if settings.dream_max_items is not None:
            entries = entries[: settings.dream_max_items]
        logger.info("[%s] %s: %d index records", source.source_id, index.url, len(entries))

        def fetch(identifier: str) -> Any:
            url = detail.url.replace("{id}", quote(identifier, safe=""))
            try:
                return ctx.http.get(url, headers=headers).json()
            except HttpStatusError as exc:
                if exc.status_code == 404:
                    logger.debug("detail %s disappeared (404)", identifier)
                    return None
                raise

        payloads = parallel_map(
            fetch,
            entries,
            workers=settings.max_workers,
            max_error_ratio=settings.max_error_ratio,
            label=f"detail[{source.source_id}]",
        )

        geometry_path = detail.geometry.path if detail.geometry else None
        exclude = {geometry_path} if geometry_path else set()
        projector = AttributeProjector(detail.attributes, exclude=exclude)
        object_geometries: dict[str, GeoJSON] = ctx.shared.setdefault(_OBJECT_GEOMETRIES, {})
        kept = 0

        for identifier, payload in zip(entries, payloads, strict=True):
            if not is_mapping(payload):
                continue
            in_region, codes = matches_region(payload, detail.filter)
            if not in_region:
                continue
            geometry = coordinates_geometry(payload, geometry_path)
            geometry_source = "native" if geometry else "none"
            if geometry is None:
                geometry = self._geometry_of_related_object(payload, object_geometries)
                if geometry:
                    geometry_source = "related_object"
            if geometry and "approaches" not in str(geometry_path):  # an *object*
                for key in (
                    extract_one(payload, "internal.id"),
                    extract_one(payload, "internal.code"),
                ):
                    if key:
                        object_geometries[str(key)] = geometry
            kept += 1
            yield ctx.factory.build(
                source.source_id,
                detail,
                geometry,
                projector.project(payload),
                feature_id=extract_one(payload, "internal.id", identifier),
                extra_meta={"katottg": codes, "geometry_source": geometry_source},
            )
        logger.info("[%s] %s: %d records in region", source.source_id, detail.url, kept)

    # ----------------------------------------------------------------------------

    @staticmethod
    def _find_detail(index: EndpointSpec, siblings: list[EndpointSpec]) -> EndpointSpec:
        prefix = index.url.rstrip("/") + "/"
        for candidate in siblings:
            if candidate.role == "detail" and candidate.url.startswith(prefix):
                return candidate
        raise DecodeError(f"No detail endpoint paired with index {index.url}")

    @staticmethod
    def _iter_index(index: EndpointSpec, headers: dict[str, str], ctx: RunContext) -> Iterator[str]:
        details = index.pagination_details
        cursor_param = (details.cursor_param if details else None) or "from"
        cursor_path = (details.cursor_value if details else None) or "$.data[-1].external.updated"
        page_size = (details.page_size if details else None) or 1000
        seen: set[str] = set()
        cursor: str | None = None

        for _page in range(ctx.settings.max_index_pages):
            params = dict(index.params)
            params.pop(cursor_param, None)  # the spec value is a placeholder description
            if cursor is not None:
                params[cursor_param] = cursor
            payload = ctx.http.get(index.url, params=params or None, headers=headers).json()
            records = list(iter_container_items(payload, index.container or "$.data"))
            fresh = 0
            for record in records:
                identifier = extract_one(record, "internal.id")
                if identifier is not None and str(identifier) not in seen:
                    seen.add(str(identifier))
                    fresh += 1
                    yield str(identifier)
            if len(records) < page_size or not records:
                return
            next_cursor = extract_one(payload, cursor_path)
            if next_cursor is None or next_cursor == cursor or fresh == 0:
                logger.warning(
                    "%s: cursor does not advance (%r); stopping to avoid an endless loop",
                    index.url,
                    next_cursor,
                )
                return
            cursor = str(next_cursor)
        logger.warning("%s: stopped at max_index_pages", index.url)

    @staticmethod
    def _geometry_of_related_object(
        payload: Any, object_geometries: Mapping[str, GeoJSON]
    ) -> GeoJSON | None:
        """Ideas without own coordinates inherit them from the object they refer to."""
        for related in extract(payload, "cdu_response.relatedProcesses[*]"):
            if isinstance(related, Mapping) and related.get("relationship") == "object":
                geometry = object_geometries.get(str(related.get("identifier")))
                if geometry:
                    return geometry
        return None
