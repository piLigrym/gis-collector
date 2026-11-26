"""Logging configuration helpers (stdlib ``logging`` only)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_PACKAGE_LOGGER = "gis_collector"


class JsonFormatter(logging.Formatter):
    """Formats records as one JSON object per line (log-shipper friendly)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str | int = "INFO", *, json_format: bool = False) -> None:
    """Configure the package logger. Idempotent: replaces previous handlers.

    Logs go to *stderr* so that stdout stays clean for the collected data.
    """
    logger = logging.getLogger(_PACKAGE_LOGGER)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter()
        if json_format
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    # httpx logs every request at INFO; keep it quiet unless debugging.
    logging.getLogger("httpx").setLevel(logging.WARNING if level != "DEBUG" else logging.DEBUG)
