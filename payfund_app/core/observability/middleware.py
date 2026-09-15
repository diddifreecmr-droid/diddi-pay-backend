"""ASGI middleware for correlation headers, access logs, and HTTP metrics."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from payfund_app.core.config import get_settings
from payfund_app.core.observability.context import bind_request_id, reset_request_id
from payfund_app.core.observability.metrics import (
    observe_application_error,
    observe_http_request,
)
from payfund_app.shared_kernel.logging import emit


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _header_value(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value.decode("latin-1")
    return None


def _request_id(headers: list[tuple[bytes, bytes]]) -> str:
    candidate = _header_value(headers, b"x-request-id")
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def _route_label(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


class ObservabilityMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope.get("headers", []))
        context_token = bind_request_id(request_id)
        started_at = time.perf_counter()
        method = str(scope.get("method") or "UNKNOWN").upper()
        status_code = 500

        async def send_with_request_id(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            route = _route_label(scope)
            if get_settings().metrics_enabled:
                observe_application_error(
                    method=method,
                    route=route,
                    exception_type=type(exc).__name__,
                )
            emit(
                "error",
                "http.request.failed",
                method=method,
                route=route,
                exception_type=type(exc).__name__,
            )
            raise
        finally:
            duration_seconds = time.perf_counter() - started_at
            route = _route_label(scope)
            if get_settings().metrics_enabled:
                observe_http_request(
                    method=method,
                    route=route,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )
            emit(
                "info",
                "http.request.completed",
                method=method,
                route=route,
                status_code=status_code,
                duration_ms=round(duration_seconds * 1000, 3),
            )
            reset_request_id(context_token)
