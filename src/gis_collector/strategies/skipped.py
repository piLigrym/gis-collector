"""Endpoints that are deliberately not collected on their own."""

from __future__ import annotations

from collections.abc import Iterator

from gis_collector.models import EndpointSpec, Feature
from gis_collector.strategies.base import CollectionStrategy, CollectionTask, RunContext


class MetadataStrategy(CollectionStrategy):
    """``public_metadata`` endpoints (e.g. styles.json) describe the source, not objects.

    They are *consumed* by other strategies (the tile strategy reads zoom limits).
    """

    name = "metadata"

    def supports(self, endpoint: EndpointSpec) -> bool:
        return endpoint.access_class == "public_metadata"

    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        return iter(())

    def skip_reason(self, endpoint: EndpointSpec) -> str | None:
        return "metadata endpoint (used as helper, not a data source)"


class ConsumedDetailStrategy(CollectionStrategy):
    """``role: detail`` endpoints are fetched by the paired index endpoint."""

    name = "consumed_detail"

    def supports(self, endpoint: EndpointSpec) -> bool:
        return endpoint.role == "detail"

    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        return iter(())

    def skip_reason(self, endpoint: EndpointSpec) -> str | None:
        return "detail endpoint (collected through its index endpoint)"
