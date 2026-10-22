"""Test doubles: an in-memory HTTP client and a static boundary provider."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry.base import BaseGeometry

from gis_collector.exceptions import HttpStatusError
from gis_collector.http.client import Response


@dataclass(frozen=True)
class Call:
    method: str
    url: str
    params: Mapping[str, Any] | None
    data: Mapping[str, Any] | None
    headers: Mapping[str, str] | None


Handler = Callable[[Call], Any]
Matcher = str | re.Pattern[str] | Callable[[Call], bool]


def json_response(obj: Any, url: str = "test://") -> Response:
    return Response(200, json.dumps(obj).encode(), url, {"content-type": "application/json"})


@dataclass
class FakeHttpClient:
    """Routes are matched in registration order; unmatched requests fail the test."""

    routes: list[tuple[str, Matcher, Any]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def add(self, method: str, matcher: Matcher, handler: Any) -> FakeHttpClient:
        """*matcher*: exact URL, compiled regex (fullmatch) or predicate on the call.

        *handler*: callable(Call) → Response | JSON-able | bytes, or a static value.
        A handler may raise :class:`HttpStatusError`.
        """
        self.routes.append((method, matcher, handler))
        return self

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        return self._dispatch(Call("GET", url, params, None, headers))

    def post_form(
        self, url: str, *, data: Mapping[str, Any], headers: Mapping[str, str] | None = None
    ) -> Response:
        return self._dispatch(Call("POST", url, None, data, headers))

    def close(self) -> None:  # pragma: no cover - nothing to release
        pass

    def calls_to(self, fragment: str) -> list[Call]:
        return [c for c in self.calls if fragment in c.url]

    # ------------------------------------------------------------------

    def _dispatch(self, call: Call) -> Response:
        self.calls.append(call)
        for method, matcher, handler in self.routes:
            if method != call.method or not self._matches(matcher, call):
                continue
            result = handler(call) if callable(handler) else handler
            if isinstance(result, Response):
                return result
            if isinstance(result, bytes):
                return Response(200, result, call.url, {})
            return json_response(result, call.url)
        raise AssertionError(
            f"Unexpected request: {call.method} {call.url} {call.params or call.data}"
        )

    @staticmethod
    def _matches(matcher: Matcher, call: Call) -> bool:
        if isinstance(matcher, str):
            return call.url == matcher
        if isinstance(matcher, re.Pattern):
            return matcher.fullmatch(call.url) is not None
        return matcher(call)


def not_found(call: Call) -> Response:
    raise HttpStatusError(404, call.url, "not found")


class StaticBoundary:
    """BoundaryProvider returning one fixed geometry for every region name."""

    def __init__(self, geometry: BaseGeometry) -> None:
        self._geometry = geometry

    def region(self, name: str) -> BaseGeometry:
        return self._geometry
