"""Structured JSON logging helpers for payment lifecycle events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
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
    """Configure one predictable JSON-only handler for DiddiPay application logs."""

    settings = get_settings()
    logger.setLevel(settings.log_level)
    logger.propagate = False
    if any(getattr(handler, "_diddipay_json", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._diddipay_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)


def build_log_payload(level: str, message: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Build the common log envelope before serialization."""

    settings = get_settings()
    safe_fields = {key: value for key, value in fields.items() if key not in _RESERVED_FIELDS}
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
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
