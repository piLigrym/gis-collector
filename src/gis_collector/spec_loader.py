"""Loading and validating ``specs/*.json``."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from gis_collector.exceptions import SpecError
from gis_collector.models import SourceSpec

logger = logging.getLogger(__name__)


class SpecRepository:
    """Reads source specifications from a directory of JSON files."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def load_file(self, path: Path) -> SourceSpec:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SpecError(f"Cannot read spec {path}: {exc}") from exc
        except ValueError as exc:
            raise SpecError(f"Invalid JSON in spec {path}: {exc}") from exc
        try:
            return SourceSpec.model_validate(raw)
        except ValidationError as exc:
            raise SpecError(f"Invalid spec {path.name}: {exc}") from exc

    def load_all(self, *, strict: bool = False) -> list[SourceSpec]:
        """Load every ``*.json`` file. Invalid files are skipped unless *strict*."""
        if not self._directory.is_dir():
            raise SpecError(f"Specs directory not found: {self._directory}")
        specs: list[SourceSpec] = []
        for path in sorted(self._directory.glob("*.json")):
            try:
                specs.append(self.load_file(path))
            except SpecError as exc:
                if strict:
                    raise
                logger.error("Skipping %s: %s", path.name, exc)
        seen: set[str] = set()
        for spec in specs:
            if spec.source_id in seen:
                raise SpecError(f"Duplicate source_id {spec.source_id!r}")
            seen.add(spec.source_id)
        warn_about_overlaps(specs)
        return specs


def warn_about_overlaps(specs: Iterable[SourceSpec]) -> None:
    """Warn when different sources query the same endpoint (duplicates in the output)."""
    by_target: dict[tuple[str, str], set[str]] = defaultdict(set)
    for spec in specs:
        for endpoint in spec.endpoints:
            by_target[(endpoint.method, endpoint.url)].add(spec.source_id)
    for (method, url), source_ids in by_target.items():
        if len(source_ids) > 1 and "{" not in url:
            logger.warning(
                "Sources %s all query %s %s — objects may be collected twice; "
                "use --exclude to skip one of them",
                sorted(source_ids),
                method,
                url,
            )
