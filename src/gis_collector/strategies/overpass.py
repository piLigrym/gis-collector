"""Overpass API endpoints (POST ``data=<Overpass QL>``)."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

from gis_collector.exceptions import GisCollectorError, RemoteRuntimeError
from gis_collector.geo.overpass import element_geometry
from gis_collector.http.client import HttpClient
from gis_collector.models import EndpointSpec, Feature
from gis_collector.projection import AttributeProjector
from gis_collector.strategies.base import (
    CollectionStrategy,
    CollectionTask,
    RunContext,
    is_mapping,
    iter_container_items,
)

logger = logging.getLogger(__name__)

#: Attributes that are represented by ``Feature.geometry`` rather than properties.
_GEOMETRY_ATTRIBUTES = frozenset({"lat", "lon", "nodes", "geometry", "members"})

_SLOTS_NOW = re.compile(r"(\d+)\s+slots?\s+available\s+now", re.IGNORECASE)
_WAIT_SECONDS = re.compile(r"in\s+(\d+)\s+seconds", re.IGNORECASE)


class OverpassSlotWaiter:
    """Politely waits for a free query slot using ``/api/status``.

    The public instance allows 2 concurrent queries per IP; exceeding it yields
    429/504. Failure to read the status is never fatal.
    """

    def __init__(self, max_wait_s: float, sleep: Callable[[float], None] = time.sleep) -> None:
        self._max_wait_s = max_wait_s
        self._sleep = sleep

    def wait(self, http: HttpClient, interpreter_url: str) -> None:
        parts = urlsplit(interpreter_url)
        status_url = f"{parts.scheme}://{parts.netloc}/api/status"
        try:
            text = http.get(status_url).text
        except GisCollectorError as exc:
            logger.debug("Overpass status unavailable: %s", exc)
            return
        match = _SLOTS_NOW.search(text)
        if match and int(match.group(1)) > 0:
            return
        if "rate limit: 0" in text.lower():
            return
        wait = _WAIT_SECONDS.search(text)
        if wait:
            delay = min(float(wait.group(1)) + 1.0, self._max_wait_s)
            logger.info("Overpass: no free slot, waiting %.0fs", delay)
            self._sleep(delay)


class OverpassStrategy(CollectionStrategy):
    name = "overpass"

    def supports(self, endpoint: EndpointSpec) -> bool:
        return (
            endpoint.method == "POST"
            and "data" in endpoint.params
            and urlsplit(endpoint.url).path.rstrip("/").endswith("/interpreter")
        )

    def collect(self, task: CollectionTask, ctx: RunContext) -> Iterator[Feature]:
        endpoint, source = task.endpoint, task.source
        payload = self._query(endpoint, ctx)
        projector = AttributeProjector(endpoint.attributes, exclude=_GEOMETRY_ATTRIBUTES)
        seen: set[tuple[str, int]] = set()
        skipped_untagged = 0
        produced = 0

        for element in iter_container_items(payload, endpoint.container or "$.elements"):
            if not is_mapping(element):
                continue
            key = (str(element.get("type")), int(element.get("id", -1)))
            if key in seen:  # the same object can be returned by several sub-queries
                continue
            seen.add(key)
            if ctx.settings.skip_untagged_elements and not element.get("tags"):
                skipped_untagged += 1  # skeleton nodes/ways from ``>; out skel``
                continue
            produced += 1
            yield ctx.factory.build(
                source.source_id,
                endpoint,
                element_geometry(element),
                projector.project(element),
                feature_id=f"{key[0]}/{key[1]}",
            )
        logger.info(
            "[%s] overpass → %d features (%d untagged skipped)",
            source.source_id,
            produced,
            skipped_untagged,
        )

    # ----------------------------------------------------------------------------

    def _query(self, endpoint: EndpointSpec, ctx: RunContext) -> object:
        settings = ctx.settings
        waiter = OverpassSlotWaiter(settings.overpass_max_slot_wait_s, ctx.sleep)
        last_error: GisCollectorError | None = None
        for attempt in range(1, settings.overpass_attempts + 1):
            if settings.overpass_check_status:
                waiter.wait(ctx.http, endpoint.url)
            response = ctx.http.post_form(
                endpoint.url, data=endpoint.params, headers=dict(endpoint.headers)
            )
            data = response.json()
            remark = data.get("remark") if isinstance(data, dict) else None
            if isinstance(remark, str) and "runtime error" in remark.lower():
                last_error = RemoteRuntimeError(remark)
                logger.warning(
                    "Overpass runtime error (%d/%d): %s",
                    attempt,
                    settings.overpass_attempts,
                    remark,
                )
                ctx.sleep(min(settings.backoff_base_s * 2**attempt, settings.backoff_max_s))
                continue
            return data
        assert last_error is not None
        raise last_error
