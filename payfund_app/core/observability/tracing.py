"""OpenTelemetry tracing setup at the infrastructure boundary."""

from __future__ import annotations

import re

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from payfund_app.core.config import get_settings


_UUID_PATH_RE = re.compile(
    r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])"
)
_PROVIDER_REFERENCE_RE = re.compile(r"(?i)\bdpi_[a-z0-9_-]+\b")


def _safe_path(path: str) -> str:
    path = _UUID_PATH_RE.sub("{id}", path)
    return _PROVIDER_REFERENCE_RE.sub("{provider_reference}", path)


def _fastapi_request_hook(span, scope) -> None:
    if not span or not span.is_recording():
        return
    path = _safe_path(str(scope.get("path") or "/"))
    span.set_attribute("url.full", path)
    span.set_attribute("http.url", path)
    span.set_attribute("http.target", path)
    if scope.get("query_string"):
        span.set_attribute("url.query", "[REDACTED]")


def _httpx_request_hook(span, request) -> None:
    if not span or not span.is_recording():
        return
    url = request.url
    authority = url.host
    if url.port and url.port not in {80, 443}:
        authority = f"{authority}:{url.port}"
    safe_url = f"{url.scheme}://{authority}{_safe_path(url.path)}"
    span.set_attribute("url.full", safe_url)
    span.set_attribute("http.url", safe_url)


def configure_tracing(
    app: FastAPI, *, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Instrument inbound and outbound HTTP when tracing is enabled."""

    settings = get_settings()
    if not settings.tracing_enabled:
        return None

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.observability_service_name,
                "deployment.environment.name": settings.observability_environment,
                "service.version": settings.observability_release_sha,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.tracing_sample_rate)),
    )
    span_exporter = exporter or OTLPSpanExporter(
        endpoint=f"{settings.tracing_otlp_endpoint.rstrip('/')}/v1/traces",
        timeout=5,
    )
    provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="/internal/metrics,/payfund/v1/health,/payfund/v1/ready",
        server_request_hook=_fastapi_request_hook,
    )
    HTTPXClientInstrumentor().instrument(
        tracer_provider=provider,
        request_hook=_httpx_request_hook,
    )
    return provider


def current_trace_fields() -> dict[str, str | None]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {"trace_id": None, "span_id": None}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }
