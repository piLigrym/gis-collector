"""Endpoints with ``{placeholder}`` URLs that are not tile coordinates (e.g. ``{cadnum}``).

Such endpoints are *enrichment* lookups, not listings: they need concrete values.
Values come from (in priority order):

1. ``Settings.template_values[source_id]`` — explicit list of ``{placeholder: value}``;
2. values discovered earlier in the same run (tile attributes with the same name),
   limited by ``Settings.template_enrich_limit``;
3. the spec's ``second_sample_params`` when ``Settings.template_use_samples`` is set.

Without values the endpoint is skipped (logged), which is not an error.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from gis_collector.concurrency import parallel_map
from gis_collector.exceptions import HttpStatusError
from gis_collector.geo.extractors import SpecGeometryExtractor
from gis_collector.models import EndpointSpec, Feature
from gis_collector.projection import AttributeProjector
from gis_collector.strategies.base import (
    CollectionStrategy,
    CollectionTask,
    RunContext,
    is_mapping,
)

logger = logging.getLogger(__name__)


class TemplatedStrategy(CollectionStrategy):
    name = "templated_lookup"

    def supports(self, endpoint: EndpointSpec) -> bool:
        return (
            bool(endpoint.placeholders) and not endpoint.is_tile_template and endpoint.role is None
        )

    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        endpoint, source = task.endpoint, task.source
        value_sets = self._value_sets(task, ctx)
        if not value_sets:
            logger.info(
                "[%s] %s skipped: no values for %s (see Settings.template_*)",
                source.source_id,
                endpoint.url,
                list(endpoint.placeholders),
            )
            return

        extractor = SpecGeometryExtractor(endpoint.geometry)
        projector = AttributeProjector(endpoint.attributes, exclude=extractor.consumed_keys)
        headers = ctx.headers(source, endpoint)

        def fetch(values: dict[str, Any]) -> Any:
            url = endpoint.url
            for name, value in values.items():
                url = url.replace("{" + name + "}", quote(str(value), safe=":"))
            try:
                return ctx.http.get(url, params=endpoint.params or None, headers=headers).json()
            except HttpStatusError as exc:
                if exc.status_code == 404:
                    return None
                raise

        payloads = parallel_map(
            fetch,
            value_sets,
            workers=ctx.settings.max_workers,
            max_error_ratio=ctx.settings.max_error_ratio,
            label=f"lookup[{source.source_id}]",
        )
        for values, payload in zip(value_sets, payloads, strict=True):
            if not is_mapping(payload):
                continue
            yield ctx.factory.build(
                source.source_id,
                endpoint,
                extractor(payload),
                projector.project(payload),
                feature_id=next(iter(values.values())),
            )

    # ----------------------------------------------------------------------------

    @staticmethod
    def _value_sets(task: CollectionTask, ctx: RunContext) -> list[dict[str, Any]]:
        endpoint, settings = task.endpoint, ctx.settings
        names = endpoint.placeholders

        explicit = settings.template_values.get(task.source.source_id)
        if explicit:
            return [dict(v) for v in explicit if set(names) <= set(v)]

        if settings.template_enrich_limit > 0 and len(names) == 1:
            discovered = ctx.shared.get("values", {}).get(names[0], [])
            unique = list(dict.fromkeys(discovered))[: settings.template_enrich_limit]
            if unique:
                return [{names[0]: value} for value in unique]

        sample = endpoint.second_sample_params
        if settings.template_use_samples and sample and set(names) <= set(sample):
            return [{name: sample[name] for name in names}]
        return []
