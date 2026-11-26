"""Strategy registry: maps an endpoint to the strategy that can collect it."""

from __future__ import annotations

from collections.abc import Iterable

from gis_collector.exceptions import StrategyNotFoundError
from gis_collector.models import EndpointSpec
from gis_collector.strategies.base import CollectionStrategy
from gis_collector.strategies.dream import DreamStrategy
from gis_collector.strategies.json_api import JsonApiStrategy
from gis_collector.strategies.overpass import OverpassStrategy
from gis_collector.strategies.skipped import ConsumedDetailStrategy, MetadataStrategy
from gis_collector.strategies.templated import TemplatedStrategy
from gis_collector.strategies.tiles import TileStrategy


class StrategyRegistry:
    """Ordered list of strategies; the first one that ``supports`` an endpoint wins."""

    def __init__(self, strategies: Iterable[CollectionStrategy] = ()) -> None:
        self._strategies: list[CollectionStrategy] = list(strategies)

    def register(self, strategy: CollectionStrategy, *, first: bool = False) -> None:
        if first:
            self._strategies.insert(0, strategy)
        else:
            self._strategies.append(strategy)

    def resolve(self, endpoint: EndpointSpec) -> CollectionStrategy:
        for strategy in self._strategies:
            if strategy.supports(endpoint):
                return strategy
        raise StrategyNotFoundError(f"No strategy for endpoint {endpoint.label}")

    @property
    def strategies(self) -> list[CollectionStrategy]:
        return list(self._strategies)


def build_default_registry() -> StrategyRegistry:
    """Order matters: specific strategies first, the generic JSON one last."""
    return StrategyRegistry(
        [
            MetadataStrategy(),
            DreamStrategy(),
            ConsumedDetailStrategy(),
            TileStrategy(),
            OverpassStrategy(),
            TemplatedStrategy(),
            JsonApiStrategy(),
        ]
    )
