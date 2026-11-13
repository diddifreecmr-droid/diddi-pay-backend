"""Prometheus HTTP metrics and the protected scrape endpoint."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

from payfund_app.core.config import get_settings
from payfund_app.core.errors import AppError, NotFound, Unauthenticated

HTTP_REQUESTS_TOTAL = Counter(
    "diddi_http_server_requests_total",
    "HTTP requests completed by DiddiPay.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "diddi_http_server_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
APPLICATION_ERRORS_TOTAL = Counter(
    "diddipay_application_errors_total",
    "Unhandled application errors observed by the HTTP middleware.",
    ("method", "route", "exception_type"),
)

TELEMETRY_DROPPED = Counter(
    "diddi_telemetry_dropped_total", "Telemetry rejected or lost", ("signal", "reason")
)
JOBS = Counter("diddi_jobs_total", "Completed worker cycles", ("job_type", "outcome"))
JOB_DURATION = Histogram(
    "diddi_job_duration_seconds",
    "Worker cycle duration",
    ("job_type",),
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300),
)
HEARTBEAT = Gauge(
    "diddi_worker_heartbeat_timestamp_seconds", "Last successful loop", ("worker_type",)
)
QUEUE_PENDING = Gauge("diddi_queue_pending", "Pending and leased messages", ("queue",))
QUEUE_AGE = Gauge(
    "diddi_queue_oldest_age_seconds", "Age of oldest undelivered message", ("queue",)
)
QUEUE_DEAD = Gauge("diddi_queue_dead_letter", "Dead letters", ("queue",))
BUILD = Info("diddi_build", "Process identity and immutable build version")


def initialize_identity() -> None:
    from payfund_app.core.observability.identity import resource_attributes

    BUILD.info(
        {key.replace(".", "_"): value for key, value in resource_attributes().items()}
    )


def normalize_method(method: str) -> str:
    return (
        method
        if method
        in {
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
            "CONNECT",
            "TRACE",
        }
        else "OTHER"
    )


def observe_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    HTTP_REQUESTS_TOTAL.labels(normalize_method(method), route, str(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(normalize_method(method), route).observe(
        duration_seconds
    )


def observe_application_error(*, method: str, route: str, exception_type: str) -> None:
    APPLICATION_ERRORS_TOTAL.labels(normalize_method(method), route, "unhandled").inc()


router = APIRouter(tags=["internal-observability"])


def permitted_peer(host: str) -> bool:
    from ipaddress import ip_address, ip_network

    configured = get_settings().metrics_allowed_cidrs
    if not configured:
        return True  # Legacy local profile; central deployment requires explicit CIDRs.
    try:
        address = ip_address(host)
        return any(
            address in ip_network(item.strip())
            for item in configured.split(",")
            if item.strip()
        )
    except ValueError:
        return False


@router.get("/internal/metrics", include_in_schema=False)
def metrics(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    settings = get_settings()
    if not request.client or not permitted_peer(request.client.host):
        raise Unauthenticated("Réseau de collecte non autorisé.")
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
        or not hmac.compare_digest(credential.encode(), settings.metrics_token.encode())
    ):
        raise Unauthenticated("Token de métriques invalide.")
    return Response(
        content=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
