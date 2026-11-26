"""Exception hierarchy of the collector.

Every error raised on purpose by the package derives from :class:`GisCollectorError`,
so callers can catch a single base class.
"""

from __future__ import annotations


class GisCollectorError(Exception):
    """Base class for all collector errors."""


class SpecError(GisCollectorError):
    """A specification file is missing, unreadable or structurally invalid."""


class StrategyNotFoundError(GisCollectorError):
    """No collection strategy is able to handle an endpoint."""


class TransportError(GisCollectorError):
    """Network-level failure (DNS, connection, timeout) after all retries."""


class HttpStatusError(GisCollectorError):
    """The server answered with an unacceptable HTTP status."""

    def __init__(self, status_code: int, url: str, body_preview: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.body_preview = body_preview
        super().__init__(f"HTTP {status_code} for {url}: {body_preview[:200]}")


class DecodeError(GisCollectorError):
    """A response body could not be decoded (JSON, MVT, Overpass ...)."""


class RemoteRuntimeError(GisCollectorError):
    """The remote service reported a runtime problem inside a 200 response.

    Overpass does this: ``{"remark": "runtime error: Query timed out ..."}``.
    Such responses are retryable.
    """


class GeometryError(GisCollectorError):
    """Geometry is malformed and cannot be repaired."""


class BoundaryError(GisCollectorError):
    """The region boundary could not be obtained."""


class FailureBudgetExceeded(GisCollectorError):
    """Too many parallel sub-requests failed; the endpoint is aborted."""
