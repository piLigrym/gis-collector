"""gis_collector — spec-driven GIS parser.

>>> from pathlib import Path
>>> from gis_collector import GisCollector, Settings, SpecRepository
>>> specs = SpecRepository(Path("specs")).load_all()
>>> with GisCollector(Settings()) as collector:          # doctest: +SKIP
...     rows = [f.model_dump() for f in collector.collect(specs)]
"""

from gis_collector.collector import CollectionReport, EndpointReport, GisCollector
from gis_collector.exceptions import GisCollectorError
from gis_collector.logging_config import configure_logging
from gis_collector.models import Feature, SourceSpec
from gis_collector.settings import Settings
from gis_collector.spec_loader import SpecRepository

__version__ = "1.0.0"

__all__ = [
    "CollectionReport",
    "EndpointReport",
    "Feature",
    "GisCollector",
    "GisCollectorError",
    "Settings",
    "SourceSpec",
    "SpecRepository",
    "__version__",
    "configure_logging",
]
