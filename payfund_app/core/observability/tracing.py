"""OpenTelemetry tracing setup at the infrastructure boundary."""

from __future__ import annotations

import os
import re
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from requests import Response

from payfund_app.core.config import get_settings
from payfund_app.core.observability.context import get_request_id
from payfund_app.core.observability.identity import resource_attributes
from payfund_app.core.observability.metrics import TELEMETRY_DROPPED


class RateLimitedOTLPExporter(OTLPSpanExporter):
    """Honor Retry-After across bounded SDK batches and count OTLP partial rejection."""

    _not_before = 0.0

    def _export(self, serialized_data, timeout_sec=None):
        if time.time() < self._not_before:
            response = Response()
            response.status_code = 429
            response.reason = "Collector throttled"
            return response
        response = super()._export(serialized_data, timeout_sec)
        if response.status_code in {429, 503}:
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    delay = 0
            if 0 < delay < float("inf"):
                self._not_before = time.time() + delay
        if response.ok and response.content:
            result = ExportTraceServiceResponse()
            try:
                result.ParseFromString(response.content)
                rejected = result.partial_success.rejected_spans
                if rejected > 0:
                    TELEMETRY_DROPPED.labels("traces", "collector_rejected").inc(
                        rejected
                    )
            except Exception:  # noqa: BLE001 - malformed telemetry response cannot affect business
                TELEMETRY_DROPPED.labels("traces", "invalid_response").inc()
        return response


class SafeBatchProcessor(BatchSpanProcessor):
    """Strip free text before the bounded SDK queue, including auto-recorded exceptions.

    Queue accounting uses the installed 1.44 SDK internals; covered by saturation test.
    """

    def on_end(self, span):
        if not span.context.trace_flags.sampled:
            return
        allowed = {
            "http.request.method",
            "http.method",
            "http.response.status_code",
            "http.status_code",
            "http.route",
            "messaging.operation",
            "messaging.system",
            "messaging.attempt",
        }
        attrs = {k: v for k, v in span.attributes.items() if k in allowed}
        name = "http.server" if span.kind == trace.SpanKind.SERVER else "operation"
        if span.kind == trace.SpanKind.CLIENT:
            name = "http.client"
        if span.kind == trace.SpanKind.CONSUMER:
            name = "message.process"
        if span.kind == trace.SpanKind.PRODUCER:
            name = "outbox.enqueue"
        safe = ReadableSpan(
            name=name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=attrs,
            kind=span.kind,
            status=trace.Status(span.status.status_code),
            start_time=span.start_time,
            end_time=span.end_time,
            links=tuple(trace.Link(link.context) for link in span.links),
            instrumentation_scope=span.instrumentation_scope,
        )
        if len(self._batch_processor._queue) >= 2048:
            TELEMETRY_DROPPED.labels("traces", "queue_full").inc()
        super().on_end(safe)


class CountingExporter(SpanExporter):
    def __init__(self, delegate):
        self.delegate = delegate

    def export(self, spans):
        try:
            result = self.delegate.export(spans)
        except Exception:  # noqa: BLE001 - isolate telemetry/probe failure
            result = SpanExportResult.FAILURE
        if result != SpanExportResult.SUCCESS:
            TELEMETRY_DROPPED.labels("traces", "export_failed").inc(len(spans))
        return result

    def shutdown(self):
        self.delegate.shutdown()


def shutdown_tracing(provider):
    # The SDK exporter may be inside its retry deadline: never hold process exit indefinitely.
    def finish():
        provider.force_flush(timeout_millis=4500)
        provider.shutdown()

    thread = threading.Thread(target=finish, daemon=True)
    thread.start()
    thread.join(5)


def otlp_endpoint(settings):
    specific = settings.tracing_otlp_traces_endpoint or os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", ""
    )
    generic = settings.tracing_otlp_endpoint or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", ""
    )
    endpoint = specific or (generic.rstrip("/") + "/v1/traces" if generic else "")
    parsed = urlparse(endpoint)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "OTLP endpoint must be configured without credentials or query"
        )
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and settings.tracing_allow_insecure_local
    ):
        raise ValueError(
            "OTLP requires HTTPS or an explicitly isolated local transport"
        )
    if not specific and generic.rstrip("/").endswith("/v1/traces"):
        raise ValueError("Use TRACING_OTLP_TRACES_ENDPOINT for a signal-specific URL")
    return endpoint


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
    request_id = get_request_id()
    if request_id:
        request.headers["x-request-id"] = request_id
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
    app: FastAPI | None = None, *, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Instrument inbound and outbound HTTP when tracing is enabled."""

    settings = get_settings()
    if not settings.tracing_enabled:
        return None

    provider = TracerProvider(
        shutdown_on_exit=False,
        resource=Resource.create(resource_attributes()),
        span_limits=SpanLimits(
            max_attributes=32, max_events=0, max_links=8, max_attribute_length=256
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.tracing_sample_rate)),
    )
    span_exporter = exporter or RateLimitedOTLPExporter(
        endpoint=otlp_endpoint(settings),
        timeout=5,
    )
    provider.add_span_processor(
        SafeBatchProcessor(
            CountingExporter(span_exporter),
            max_queue_size=2048,
            max_export_batch_size=256,
            schedule_delay_millis=5000,
            export_timeout_millis=5000,
        )
    )
    trace.set_tracer_provider(provider)
    if app is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=provider,
            excluded_urls="/internal/metrics,/payfund/v1/health,/payfund/v1/ready",
            server_request_hook=_fastapi_request_hook,
        )
    HTTPXClientInstrumentor().instrument(
        tracer_provider=provider,
        request_hook=_httpx_request_hook,
        async_request_hook=_async_httpx_request_hook,
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


async def _async_httpx_request_hook(span, request):
    _httpx_request_hook(span, request)
