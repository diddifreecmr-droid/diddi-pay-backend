"""Structured JSON logging helpers for payment lifecycle events."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime
from typing import Any

from payfund_app.core.config import get_settings
from payfund_app.core.observability.context import get_request_id
from payfund_app.core.observability.file_logging import BoundedFileHandler
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
    logging.captureWarnings(True)
    sys.excepthook = lambda kind, value, traceback: logger.critical(
        "runtime.unhandled", exc_info=(kind, value, traceback)
    )
    threading.excepthook = lambda args: logger.critical(
        "runtime.thread.failed",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
    # Alembic's logging configuration may disable loggers created before migrations run.
    logger.disabled = False
    logger.setLevel(settings.log_level)
    logger.propagate = False
    if not any(
        getattr(handler, "_diddipay_stream", False) for handler in logger.handlers
    ):
        stream = logging.StreamHandler()
        stream.setFormatter(JsonFormatter())
        stream._diddipay_stream = True  # type: ignore[attr-defined]
        logger.addHandler(stream)
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    for existing in root.handlers:
        existing.setFormatter(JsonFormatter())
    if not any(getattr(handler, "_diddi_root", False) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler._diddi_root = True
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "sqlalchemy.engine",
        "alembic",
    ):
        external = logging.getLogger(name)
        external.handlers.clear()
        external.propagate = True
    from payfund_app.core.observability.metrics import initialize_identity

    initialize_identity()
    path = settings.observability_log_file
    for handler in list(logger.handlers):
        if isinstance(handler, BoundedFileHandler) and (
            handler._diddipay_file != path or handler.stopping.is_set()
        ):
            logger.removeHandler(handler)
            root.removeHandler(handler)
            handler.close()
    for handler in list(root.handlers):
        if isinstance(handler, BoundedFileHandler) and handler not in logger.handlers:
            root.removeHandler(handler)
            handler.close()
    if path:
        file_handler = next(
            (h for h in logger.handlers if isinstance(h, BoundedFileHandler)), None
        )
        if file_handler is None:
            file_handler = BoundedFileHandler(path)
            file_handler.setFormatter(JsonFormatter())
            logger.addHandler(file_handler)
        # payfund does not propagate; each event reaches this shared sink once.
        root.addHandler(file_handler)
    from payfund_app.core.observability.otlp_logging import (
        OTLPJsonHandler,
        logs_endpoint,
    )

    endpoint = logs_endpoint(settings) if settings.otel_logs_enabled else ""
    for handler in list(logger.handlers):
        if isinstance(handler, OTLPJsonHandler) and (
            handler.stopped or handler.endpoint != endpoint
        ):
            logger.removeHandler(handler)
            root.removeHandler(handler)
            handler.close()
    for handler in list(root.handlers):
        if isinstance(handler, OTLPJsonHandler) and handler not in logger.handlers:
            root.removeHandler(handler)
            handler.close()
    if endpoint:
        handler = next(
            (h for h in logger.handlers if isinstance(h, OTLPJsonHandler)), None
        )
        if handler is None:
            handler = OTLPJsonHandler(endpoint)
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)
        root.addHandler(handler)


def build_log_payload(
    level: str, message: str, fields: dict[str, Any]
) -> dict[str, Any]:
    from payfund_app.core.observability.identity import resource_attributes

    safe_fields = {
        key: value for key, value in fields.items() if key not in _RESERVED_FIELDS
    }
    aliases = {
        "method": "http.request.method",
        "route": "http.route",
        "status_code": "http.response.status_code",
        "exception_type": "error.type",
        "error": "error.message",
    }
    safe_fields = {aliases.get(key, key): value for key, value in safe_fields.items()}
    if "error.type" in safe_fields:
        safe_fields.setdefault("error.code", "operation_failed")
        safe_fields.setdefault("error.message", "Operation failed")
    normalized = {"warning": "warn", "critical": "fatal"}.get(
        level.lower(), level.lower()
    )
    payload = {
        "schema_version": "1.0.0",
        "timestamp": datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": normalized
        if normalized in {"debug", "info", "warn", "error", "fatal"}
        else "info",
        "event": message
        if re.fullmatch(r"[a-z][a-z0-9_.]{0,127}", message)
        else "application.event",
        "message": redact(message),
        "resource": resource_attributes(),
        "attributes": redact(safe_fields),
    }
    for key, value in {
        "request_id": get_request_id(),
        **current_trace_fields(),
    }.items():
        if value:
            payload[key] = value
    return payload


def serialize_payload(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    if "[TRUNCATED]" in text or "[MAX_DEPTH]" in text:
        payload["attributes"]["truncated"] = True
        text = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    if len(text.encode("utf-8")) > 65536:
        payload["message"] = payload["message"][:1024]
        payload["attributes"] = {"truncated": True}
        text = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            if getattr(record, "diddi_structured", False):
                return record.getMessage()
            fields = {"logger": record.name}
            if record.exc_info:
                fields.update(
                    {
                        "error.type": record.exc_info[0].__name__,
                        "error.code": "unhandled",
                        "error.message": str(record.exc_info[1]),
                    }
                )
            # Server access logs carry raw URLs and IPs. The ASGI middleware owns access events.
            message = (
                "server.access"
                if record.name == "uvicorn.access"
                else record.getMessage()
            )
            payload = build_log_payload(record.levelname, "library.event", fields)
            payload["message"] = redact(message)
            return serialize_payload(payload)
        except Exception:  # noqa: BLE001 - logging must not fail business operations
            from payfund_app.core.observability.metrics import TELEMETRY_DROPPED

            TELEMETRY_DROPPED.labels("logs", "serialization").inc()
            return serialize_payload(
                build_log_payload("error", "logging.serialization.failed", {})
            )


def emit(level: str, message: str, **fields) -> None:
    if not get_settings().observability_enabled:
        return
    try:
        text = serialize_payload(build_log_payload(level, message, fields))
        numeric = {
            "debug": logging.DEBUG,
            "warning": logging.WARNING,
            "warn": logging.WARNING,
            "error": logging.ERROR,
            "fatal": logging.CRITICAL,
        }.get(level.lower(), logging.INFO)
        logger.log(numeric, text, extra={"diddi_structured": True})
    except Exception:  # noqa: BLE001 - logging must not fail business operations
        from payfund_app.core.observability.metrics import TELEMETRY_DROPPED

        TELEMETRY_DROPPED.labels("logs", "serialization").inc()
