"""
Centralized JSON logging configuration for the store intelligence API.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


class JsonLogFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "log_fields") and isinstance(record.log_fields, dict):
            payload.update(record.log_fields)
        if record.exc_info and record.levelno >= logging.ERROR:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    """Configure the root logger to emit structured JSON to stdout."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)


def log_structured(
    logger: logging.Logger,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    """Write a structured log entry with arbitrary JSON fields."""
    record = logger.makeRecord(
        logger.name,
        level,
        "(structured)",
        0,
        message,
        (),
        None,
    )
    record.log_fields = fields  # type: ignore[attr-defined]
    logger.handle(record)
