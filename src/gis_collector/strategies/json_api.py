"""Plain JSON REST endpoints: single response or ``page_number`` pagination.

Covers ecozagroza (flat array of sensors with lat/lon fields) and NASA CMR
(``$.feed.entry`` with polygon footprints), and any future endpoint of this shape.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from gis_collector.geo.extractors import SpecGeometryExtractor
from gis_collector.models import EndpointSpec, Feature
from gis_collector.projection import AttributeProjector
from gis_collector.strategies.base import (
    CollectionStrategy,
    CollectionTask,
    RunContext,
    as_int,
    is_mapping,
    iter_container_items,
    param_variants,
)

logger = logging.getLogger(__name__)


class JsonApiStrategy(CollectionStrategy):
    name = "json_api"

    def supports(self, endpoint: EndpointSpec) -> bool:
        return (
            not endpoint.placeholders
            and endpoint.role is None
            and endpoint.pagination in {"none", "page_number"}
        )

    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        endpoint, source = task.endpoint, task.source
        extractor = SpecGeometryExtractor(endpoint.geometry)
        projector = AttributeProjector(endpoint.attributes, exclude=extractor.consumed_keys)
        headers = ctx.headers(source, endpoint)
        variants = param_variants(task, ctx.settings)

        for params in variants:
            count = 0
            for item in self._iter_items(endpoint, params, headers, ctx):
                if not is_mapping(item):
                    continue
                count += 1
                yield ctx.factory.build(
                    source.source_id,
                    endpoint,
                    extractor(item),
                    projector.project(item),
                    feature_id=item.get("id"),
                    extra_meta={"params": params} if len(variants) > 1 else None,
                )
            logger.info(
                "[%s] %s params=%s → %d records", source.source_id, endpoint.url, params, count
            )
            if (
                endpoint.pagination == "none"
                and endpoint.expected_count is not None
                and count != endpoint.expected_count
            ):
                logger.warning(
                    "[%s] expected_count=%d but received %d",
                    source.source_id,
                    endpoint.expected_count,
                    count,
                )

    # ----------------------------------------------------------------------------

    def _iter_items(
        self,
        endpoint: EndpointSpec,
        params: dict[str, Any],
        headers: dict[str, str],
        ctx: RunContext,
    ) -> Iterator[Any]:
        paged = endpoint.pagination == "page_number"
        details = endpoint.pagination_details
        page_param = (details.page_param if details else None) or "page_num"  # CMR default
        page_size = as_int(params.get("page_size")) or (details.page_size if details else None)
        yielded = 0
        page = 1
        while True:
            query = dict(params)
            if paged:
                query[page_param] = page
            if endpoint.method == "POST":
                response = ctx.http.post_form(endpoint.url, data=query, headers=headers)
            else:
                response = ctx.http.get(endpoint.url, params=query, headers=headers)
            items = list(iter_container_items(response.json(), endpoint.container))
            yield from items
            yielded += len(items)
            if not paged or not items:
                return
            # For a paged, newest-first catalogue ``expected_count`` is the target size.
            if endpoint.expected_count is not None and yielded >= endpoint.expected_count:
                return
            if page_size and len(items) < page_size:
                return
            if page >= ctx.settings.max_pages:
                logger.warning("%s: stopped at max_pages=%d", endpoint.url, page)
                return
            page += 1
