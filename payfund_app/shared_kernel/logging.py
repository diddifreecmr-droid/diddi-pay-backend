"""Structured JSON logging helpers for payment lifecycle events."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from payfund_app.core.config import get_settings
from payfund_app.core.observability.context import get_request_id
from payfund_app.core.observability.redaction import redact
from payfund_app.core.observability.tracing import current_trace_fields

logger = logging.getLogger("payfund")

_RESERVED_FIELDS = {
    "ts",
    "level",
    "message",
    "service",
    "environment",
    "release",
    "request_id",
    "trace_id",
    "span_id",
}


def configure_logging() -> None:
    """Keep stdout logs; optionally mirror the same redacted JSON to a bounded file."""

    settings = get_settings()
    # Alembic's logging configuration may disable loggers created before migrations run.
    logger.disabled = False
    logger.setLevel(settings.log_level)
    logger.propagate = False
    if not any(getattr(handler, "_diddipay_stream", False) for handler in logger.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        stream._diddipay_stream = True  # type: ignore[attr-defined]
        logger.addHandler(stream)
    path = settings.observability_log_file
    if path and not any(
        getattr(handler, "_diddipay_file", None) == path for handler in logger.handlers
    ):
        file_handler = RotatingFileHandler(
            path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        file_handler._diddipay_file = path  # type: ignore[attr-defined]
        logger.addHandler(file_handler)


def build_log_payload(level: str, message: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Build the common log envelope before serialization."""

    settings = get_settings()
    safe_fields = {key: value for key, value in fields.items() if key not in _RESERVED_FIELDS}
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "level": level.lower(),
        "message": message,
        "service": settings.observability_service_name,
        "environment": settings.observability_environment,
        "release": settings.observability_release_sha,
        "request_id": get_request_id(),
        **current_trace_fields(),
        **safe_fields,
    }
    return redact(payload)


def emit(level: str, message: str, **fields) -> None:
    if not get_settings().observability_enabled:
        return
    payload = build_log_payload(level, message, fields)
    text = json.dumps(payload, default=str, ensure_ascii=True)
    normalized_level = level.lower()
    if normalized_level == "error":
        logger.error(text)
    elif normalized_level == "warning":
        logger.warning(text)
    elif normalized_level == "debug":
        logger.debug(text)
    else:
        logger.info(text)
