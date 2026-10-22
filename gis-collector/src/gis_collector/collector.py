"""Orchestrator: specs → strategies → filtered, normalised features."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection, Iterable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from gis_collector.auth import AuthProvider, SettingsAuthProvider
from gis_collector.exceptions import GisCollectorError, StrategyNotFoundError
from gis_collector.feature_factory import FeatureFactory
from gis_collector.filters import FeatureFilter, RegionPolygonFilter
from gis_collector.geo.boundary import BoundaryProvider, build_boundary_provider
from gis_collector.http.client import HttpClient, HttpxClient
from gis_collector.models import EndpointSpec, Feature, SourceSpec
from gis_collector.settings import Settings
from gis_collector.strategies.base import CollectionStrategy, CollectionTask, RunContext
from gis_collector.strategies.registry import StrategyRegistry, build_default_registry

logger = logging.getLogger(__name__)


class EndpointReport(BaseModel):
    source_id: str
    endpoint: str
    strategy: str | None = None
    status: Literal["ok", "skipped", "failed"]
    features: int = 0
    duration_s: float = 0.0
    detail: str | None = None  # skip reason or error message


class CollectionReport(BaseModel):
    started_at: datetime
    finished_at: datetime
    features: list[Feature] = Field(default_factory=list)
    endpoints: list[EndpointReport] = Field(default_factory=list)

    @property
    def failed(self) -> list[EndpointReport]:
        return [e for e in self.endpoints if e.status == "failed"]

    @property
    def ok(self) -> bool:
        return not self.failed


class PlannedEndpoint(BaseModel):
    source_id: str
    endpoint: str
    strategy: str | None
    skip_reason: str | None = None
    problem: str | None = None


class GisCollector:
    """Collects every endpoint of the given specifications.

    Usage::

        with GisCollector(Settings()) as collector:
            features = collector.collect(SpecRepository(Path("specs")).load_all())
        rows = [f.model_dump() for f in features]   # [{"source_id", "geometry", "properties"}]

    The collector never touches a database; persistence is the caller's concern.
    All collaborators are injectable (HTTP client, registry, auth, boundary).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http: HttpClient | None = None,
        registry: StrategyRegistry | None = None,
        auth: AuthProvider | None = None,
        boundary: BoundaryProvider | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or Settings()
        self._owns_http = http is None
        self._http: HttpClient = http or HttpxClient(self.settings, sleep=sleep)
        self._registry = registry or build_default_registry()
        self._auth = auth or SettingsAuthProvider(self.settings)
        self._boundary = boundary or build_boundary_provider(self.settings, self._http)
        self._sleep = sleep

    # --------------------------------------------------------------- lifecycle

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> GisCollector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --------------------------------------------------------------------- API

    def collect(
        self,
        specs: Iterable[SourceSpec],
        *,
        include: Collection[str] | None = None,
        exclude: Collection[str] = (),
    ) -> list[Feature]:
        """Run the collection and return only the features (see :meth:`run`)."""
        return self.run(specs, include=include, exclude=exclude).features

    def run(
        self,
        specs: Iterable[SourceSpec],
        *,
        include: Collection[str] | None = None,
        exclude: Collection[str] = (),
    ) -> CollectionReport:
        """Collect everything and return features plus a per-endpoint report."""
        started = datetime.now(UTC)
        ctx = RunContext(
            settings=self.settings,
            http=self._http,
            auth=self._auth,
            boundary=self._boundary,
            factory=FeatureFactory(self.settings, started_at=started),
            sleep=self._sleep,
        )
        report = CollectionReport(started_at=started, finished_at=started)
        for source in self._select(specs, include, exclude):
            if not source.accessible:
                report.endpoints.append(
                    EndpointReport(
                        source_id=source.source_id,
                        endpoint="*",
                        status="skipped",
                        detail="source marked accessible=false",
                    )
                )
                logger.warning("[%s] marked inaccessible, skipped", source.source_id)
                continue
            for endpoint in source.endpoints:
                report.endpoints.append(self._run_endpoint(source, endpoint, ctx, report))
        report.finished_at = datetime.now(UTC)
        logger.info(
            "Done: %d features, %d endpoints ok, %d failed",
            len(report.features),
            sum(e.status == "ok" for e in report.endpoints),
            len(report.failed),
        )
        return report

    def plan(
        self,
        specs: Iterable[SourceSpec],
        *,
        include: Collection[str] | None = None,
        exclude: Collection[str] = (),
    ) -> list[PlannedEndpoint]:
        """Dry run: which strategy would handle each endpoint (no network access)."""
        planned: list[PlannedEndpoint] = []
        for source in self._select(specs, include, exclude):
            for endpoint in source.endpoints:
                try:
                    strategy = self._registry.resolve(endpoint)
                    planned.append(
                        PlannedEndpoint(
                            source_id=source.source_id,
                            endpoint=endpoint.label,
                            strategy=strategy.name,
                            skip_reason=strategy.skip_reason(endpoint),
                        )
                    )
                except StrategyNotFoundError as exc:
                    planned.append(
                        PlannedEndpoint(
                            source_id=source.source_id,
                            endpoint=endpoint.label,
                            strategy=None,
                            problem=str(exc),
                        )
                    )
        return planned

    # ---------------------------------------------------------------- internals

    @staticmethod
    def _select(
        specs: Iterable[SourceSpec], include: Collection[str] | None, exclude: Collection[str]
    ) -> list[SourceSpec]:
        selected = [
            s
            for s in specs
            if (include is None or s.source_id in include) and s.source_id not in exclude
        ]
        if include:
            missing = set(include) - {s.source_id for s in selected} - set(exclude)
            if missing:
                logger.warning("Requested sources not found: %s", sorted(missing))
        return selected

    def _run_endpoint(
        self,
        source: SourceSpec,
        endpoint: EndpointSpec,
        ctx: RunContext,
        report: CollectionReport,
    ) -> EndpointReport:
        started = time.monotonic()
        entry = EndpointReport(source_id=source.source_id, endpoint=endpoint.label, status="ok")
        try:
            strategy = self._registry.resolve(endpoint)
            entry.strategy = strategy.name
            reason = strategy.skip_reason(endpoint)
            if reason:
                entry.status, entry.detail = "skipped", reason
                logger.info("[%s] %s skipped: %s", source.source_id, endpoint.label, reason)
                return entry
            features = self._collect_endpoint(strategy, CollectionTask(source, endpoint), ctx)
            report.features.extend(features)
            entry.features = len(features)
        except Exception as exc:  # isolate endpoint failures; strict mode re-raises
            entry.status, entry.detail = "failed", f"{type(exc).__name__}: {exc}"
            if isinstance(exc, GisCollectorError):
                logger.error("[%s] %s failed: %s", source.source_id, endpoint.label, exc)
            else:
                logger.exception("[%s] %s crashed", source.source_id, endpoint.label)
            if self.settings.strict:
                raise
        finally:
            entry.duration_s = round(time.monotonic() - started, 3)
        return entry

    def _collect_endpoint(
        self, strategy: CollectionStrategy, task: CollectionTask, ctx: RunContext
    ) -> list[Feature]:
        feature_filter = self._build_filter(task.source)
        features: list[Feature] = []
        dropped = 0
        for feature in strategy.collect(task, ctx):
            if feature_filter is None or feature_filter.accept(feature):
                features.append(feature)
            else:
                dropped += 1
        if dropped:
            logger.info(
                "[%s] region filter dropped %d of %d features",
                task.source.source_id,
                dropped,
                dropped + len(features),
            )
        return features

    def _build_filter(self, source: SourceSpec) -> FeatureFilter | None:
        if source.area_constraint is not None:
            region_name = source.area_constraint.name
        elif source.source_id in self.settings.region_filter_sources:
            region_name = self.settings.default_region_name
        else:
            return None
        return RegionPolygonFilter(self._boundary.region(region_name))
