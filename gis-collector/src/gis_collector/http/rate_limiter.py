"""Thread-safe, per-host rate limiting (minimum spacing between requests)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping


class RateLimiter:
    """Allows at most ``rate`` acquisitions per second across all threads."""

    def __init__(
        self,
        rate: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            self._sleep(wait)


class HostRateLimiter:
    """One :class:`RateLimiter` per host with a default rate and overrides."""

    def __init__(
        self,
        default_rate: float,
        overrides: Mapping[str, float] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._default = default_rate
        self._overrides = dict(overrides or {})
        self._clock = clock
        self._sleep = sleep
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def acquire(self, host: str) -> None:
        with self._lock:
            limiter = self._limiters.get(host)
            if limiter is None:
                rate = self._overrides.get(host, self._default)
                limiter = RateLimiter(rate, clock=self._clock, sleep=self._sleep)
                self._limiters[host] = limiter
        limiter.acquire()
