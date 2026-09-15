"""Prometheus HTTP metrics and the protected scrape endpoint."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from payfund_app.core.config import get_settings
from payfund_app.core.errors import AppError, NotFound, Unauthenticated


HTTP_REQUESTS_TOTAL = Counter(
    "diddipay_http_requests_total",
    "HTTP requests completed by DiddiPay.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "diddipay_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
APPLICATION_ERRORS_TOTAL = Counter(
    "diddipay_application_errors_total",
    "Unhandled application errors observed by the HTTP middleware.",
    ("method", "route", "exception_type"),
)


def observe_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    HTTP_REQUESTS_TOTAL.labels(method, route, str(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method, route).observe(duration_seconds)


def observe_application_error(*, method: str, route: str, exception_type: str) -> None:
    APPLICATION_ERRORS_TOTAL.labels(method, route, exception_type).inc()


router = APIRouter(tags=["internal-observability"])


@router.get("/internal/metrics", include_in_schema=False)
def metrics(
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    settings = get_settings()
    if not settings.metrics_enabled:
        raise NotFound("Endpoint de métriques désactivé.")
    if not settings.metrics_token:
        raise AppError(
            "Token de métriques non configuré.",
            code="METRICS_CONFIGURATION_MISSING",
            status_code=503,
        )
    scheme, separator, credential = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(credential, settings.metrics_token)
    ):
        raise Unauthenticated("Token de métriques invalide.")
    return Response(
        content=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
