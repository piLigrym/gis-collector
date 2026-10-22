"""Strategy contract and the per-run context shared by all strategies."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeGuard

from gis_collector.auth import AuthProvider
from gis_collector.feature_factory import FeatureFactory
from gis_collector.geo.boundary import BoundaryProvider
from gis_collector.http.client import HttpClient
from gis_collector.jsonpath import extract
from gis_collector.models import EndpointSpec, Feature, SourceSpec
from gis_collector.settings import Settings


@dataclass(slots=True)
class RunContext:
    """Everything a strategy may need; created once per collection run."""

    settings: Settings
    http: HttpClient
    auth: AuthProvider
    boundary: BoundaryProvider
    factory: FeatureFactory
    sleep: Callable[[float], None] = time.sleep
    #: Cross-endpoint scratch space of one run (e.g. cadastral numbers found in tiles).
    shared: dict[str, Any] = field(default_factory=dict)

    def headers(self, source: SourceSpec, endpoint: EndpointSpec) -> dict[str, str]:
        """Auth headers merged with headers declared in the spec (spec wins)."""
        merged = dict(self.auth.headers_for(source))
        merged.update(endpoint.headers)
        return merged


@dataclass(frozen=True, slots=True)
class CollectionTask:
    """One endpoint to collect, plus its siblings (for index/detail pairs, styles ...)."""

    source: SourceSpec
    endpoint: EndpointSpec

    @property
    def siblings(self) -> list[EndpointSpec]:
        return self.source.endpoints


class CollectionStrategy(ABC):
    """Knows how to collect one *kind* of endpoint (Open/Closed: add, don't modify)."""

    name: ClassVar[str]

    @abstractmethod
    def supports(self, endpoint: EndpointSpec) -> bool:
        """Can this strategy handle the endpoint?"""

    @abstractmethod
    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        """Yield features for the task. May raise :class:`GisCollectorError`."""

    def skip_reason(self, endpoint: EndpointSpec) -> str | None:  # noqa: ARG002
        """Non-``None`` for strategies that deliberately collect nothing."""
        return None


# --------------------------------------------------------------------- shared helpers


def param_variants(task: CollectionTask, settings: Settings) -> list[dict[str, Any]]:
    """Parameter sets to run: the primary one plus, for opted-in sources, the second sample.

    ``second_sample_params`` is a *second real dataset* for some sources
    (ecozagroza: air_pollution, earthdata: VNP14) and only a test query for others
    (overpass). ``Settings.param_variant_sources`` says which is which.
    """
    endpoint = task.endpoint
    variants = [dict(endpoint.params)]
    if endpoint.second_sample_params and task.source.source_id in settings.param_variant_sources:
        variants.append({**endpoint.params, **endpoint.second_sample_params})
    return variants


def iter_container_items(payload: Any, container: str | None) -> Iterator[Any]:
    """Yield the records of a response according to the spec's ``container`` path."""
    nodes: Iterable[Any] = extract(payload, container or "$")
    for node in nodes:
        if isinstance(node, list):
            yield from node
        elif node is not None:
            yield node


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_mapping(value: Any) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping)
