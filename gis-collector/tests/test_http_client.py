from __future__ import annotations

import httpx
import pytest

from gis_collector.exceptions import DecodeError, HttpStatusError, TransportError
from gis_collector.http.client import HttpxClient, Response
from gis_collector.http.rate_limiter import RateLimiter
from gis_collector.settings import Settings


def make_client(handler, sleeps: list[float] | None = None, **settings) -> HttpxClient:
    sleeps = sleeps if sleeps is not None else []
    return HttpxClient(
        Settings(default_requests_per_second=1000, backoff_base_s=1.0, **settings),
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )


def test_retries_5xx_then_succeeds_and_honours_retry_after() -> None:
    statuses = iter([429, 503, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        headers = {"Retry-After": "7"} if status == 429 else {}
        return httpx.Response(status, json={"ok": True}, headers=headers)

    response = make_client(handler, sleeps).get("https://example.org/x")
    assert response.json() == {"ok": True}
    assert sleeps[0] == 7.0 and len(sleeps) == 2


def test_gives_up_after_max_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(504)

    with pytest.raises(HttpStatusError) as info:
        make_client(handler, max_retries=2).get("https://example.org/x")
    assert info.value.status_code == 504 and calls == 3


def test_client_errors_are_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    with pytest.raises(HttpStatusError):
        make_client(handler).get("https://example.org/x")
    assert calls == 1


def test_transport_errors_are_retried_then_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(TransportError):
        make_client(handler, max_retries=1).get("https://example.org/x")


def test_no_accept_header_and_custom_user_agent_on_post() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={})

    make_client(handler).post_form(
        "https://overpass-api.de/api/interpreter",
        data={"data": "[out:json];"},
        headers={"User-Agent": "GIS-Collector-Bot/1.0 (contact: admin@vimgip.gov.ua)"},
    )
    headers = seen["headers"]
    assert "accept" not in headers
    assert headers["user-agent"].startswith("GIS-Collector-Bot/1.0")
    assert headers["content-type"] == "application/x-www-form-urlencoded"
    assert seen["body"] == "data=%5Bout%3Ajson%5D%3B"


def test_invalid_json_raises_decode_error() -> None:
    with pytest.raises(DecodeError):
        Response(200, b"<html>", "u").json()


def test_rate_limiter_spaces_requests() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(2.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.acquire()
    assert sleeps == [0.5, 0.5]
