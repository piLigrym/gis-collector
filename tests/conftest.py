from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fakes import FakeHttpClient, StaticBoundary
from world import CHERNIHIV_REGION

from gis_collector import GisCollector, Settings, SourceSpec, SpecRepository

SPECS_DIR = Path(__file__).resolve().parent.parent / "specs"


@pytest.fixture(scope="session")
def specs() -> dict[str, SourceSpec]:
    return {s.source_id: s for s in SpecRepository(SPECS_DIR).load_all()}


@pytest.fixture
def fake() -> FakeHttpClient:
    return FakeHttpClient()


@pytest.fixture
def make_collector(fake: FakeHttpClient) -> Callable[..., GisCollector]:
    def factory(**settings_overrides: object) -> GisCollector:
        settings = Settings(max_workers=2, **settings_overrides)  # type: ignore[arg-type]
        return GisCollector(
            settings, http=fake, boundary=StaticBoundary(CHERNIHIV_REGION), sleep=lambda _s: None
        )

    return factory
