"""Bounded parallel map with a failure budget."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor

from gis_collector.exceptions import FailureBudgetExceeded

logger = logging.getLogger(__name__)


def parallel_map[T, R](
    func: Callable[[T], R],
    items: Sequence[T],
    *,
    workers: int,
    max_error_ratio: float = 0.2,
    min_samples: int = 20,
    label: str = "task",
) -> list[R | None]:
    """Apply *func* to *items* with up to *workers* threads, preserving order.

    A failed item yields ``None`` (the error is logged). When, after at least
    *min_samples* finished items, the share of failures exceeds
    *max_error_ratio*, :class:`FailureBudgetExceeded` is raised and pending work
    is cancelled — a systemic failure (blocked IP, server down) must not turn
    into thousands of useless retries.
    """
    results: list[R | None] = [None] * len(items)
    if not items:
        return results

    done = 0
    failed = 0

    def check_budget() -> None:
        if done >= min_samples and failed / done > max_error_ratio:
            raise FailureBudgetExceeded(
                f"{label}: {failed}/{done} sub-requests failed (> {max_error_ratio:.0%})"
            )

    def record(index: int, outcome: Callable[[], R]) -> None:
        nonlocal done, failed
        done += 1
        try:
            results[index] = outcome()
        except FailureBudgetExceeded:
            raise
        except Exception as exc:
            failed += 1
            logger.warning("%s #%d failed: %s", label, index, exc)
        check_budget()

    if workers <= 1:
        for index, item in enumerate(items):
            record(index, lambda item=item: func(item))  # type: ignore[misc]
        return results

    window = workers * 4  # bounded in-flight work, cheap cancellation
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collector") as pool:
        pending: dict[int, Future[R]] = {}
        next_index = 0
        try:
            while next_index < len(items) or pending:
                while next_index < len(items) and len(pending) < window:
                    pending[next_index] = pool.submit(func, items[next_index])
                    next_index += 1
                index = min(pending)  # consume in submission order
                future = pending.pop(index)
                record(index, future.result)
        except BaseException:
            for future in pending.values():
                future.cancel()
            raise
    return results
