"""HTTP layer.

Strategies depend only on the :class:`HttpClient` protocol (Dependency Inversion);
:class:`HttpxClient` is the production implementation with retries, ``Retry-After``
support, exponential back-off with jitter and per-host rate limiting.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from gis_collector.exceptions import DecodeError, HttpStatusError, TransportError
from gis_collector.http.rate_limiter import HostRateLimiter
from gis_collector.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Response:
    """Minimal immutable HTTP response used across the package."""

    status_code: int
    content: bytes
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DecodeError(f"Invalid JSON from {self.url}: {exc}") from exc


@runtime_checkable
class HttpClient(Protocol):
    """What strategies need from an HTTP client."""

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response: ...

    def post_form(
        self,
        url: str,
        *,
        data: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> Response: ...

    def close(self) -> None: ...


class HttpxClient:
    """Production :class:`HttpClient` built on ``httpx.Client`` (thread-safe)."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rate_limiter: HostRateLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._limiter = rate_limiter or HostRateLimiter(
            settings.default_requests_per_second, settings.host_requests_per_second
        )
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                settings.read_timeout_s,
                connect=settings.connect_timeout_s,
            ),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
            transport=transport,
        )
        # Overpass answered 406 to the generic ``Accept: */*`` header sent by
        # default clients (see specs/overpass.json): never send Accept implicitly.
        if "accept" in self._client.headers:
            del self._client.headers["accept"]

    # ------------------------------------------------------------------ public

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        return self._request("GET", url, params=params, headers=headers)

    def post_form(
        self,
        url: str,
        *,
        data: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        return self._request("POST", url, data=data, headers=headers)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpxClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------------------------------------------------------------- internals

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        host = urlsplit(url).netloc
        attempts = self._settings.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            self._limiter.acquire(host)
            try:
                raw = self._client.request(
                    method,
                    url,
                    params=dict(params) if params else None,
                    data=dict(data) if data else None,
                    headers=dict(headers) if headers else None,
                )
            except httpx.TransportError as exc:  # timeouts, connection resets, DNS ...
                last_error = TransportError(f"{method} {url}: {exc!r}")
                logger.warning(
                    "Transport error (%d/%d) %s %s: %r", attempt, attempts, method, url, exc
                )
                delay = self._backoff(attempt, None)
            else:
                response = Response(
                    status_code=raw.status_code,
                    content=raw.content,
                    url=str(raw.url),
                    headers=dict(raw.headers),
                )
                if response.status_code < 400:
                    return response
                error = HttpStatusError(response.status_code, url, response.text)
                if response.status_code not in self._settings.retry_statuses:
                    raise error
                last_error = error
                logger.warning(
                    "HTTP %d (%d/%d) %s %s", response.status_code, attempt, attempts, method, url
                )
                delay = self._backoff(attempt, response.headers.get("retry-after"))

            if attempt < attempts:
                logger.debug("Sleeping %.1fs before retry", delay)
                self._sleep(delay)

        assert last_error is not None
        raise last_error

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        cap = self._settings.backoff_max_s
        if retry_after:
            try:
                return min(float(retry_after), cap)
            except ValueError:
                pass  # HTTP-date form is rare for these APIs; fall back to back-off
        base = self._settings.backoff_base_s * (2 ** (attempt - 1))
        return float(min(base, cap) * (0.5 + random.random() / 2))  # noqa: S311 - jitter only
