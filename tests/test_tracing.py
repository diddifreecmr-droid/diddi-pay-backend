"""OBS-5 distributed trace propagation and log correlation."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from payfund_app.core.config import get_settings
from payfund_app.core.observability.tracing import (
    _fastapi_request_hook,
    _httpx_request_hook,
    configure_tracing,
    current_trace_fields,
)
from payfund_app.shared_kernel.logging import build_log_payload


TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
PARENT_SPAN_ID = "b7ad6b7169203331"


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_tracing_is_disabled_without_runtime_flag(monkeypatch) -> None:
    monkeypatch.setenv("TRACING_ENABLED", "false")
    get_settings.cache_clear()

    assert configure_tracing(FastAPI()) is None
    get_settings.cache_clear()


def test_fastapi_extracts_incoming_traceparent() -> None:
    provider, exporter = _provider()
    traced_app = FastAPI()

    @traced_app.get("/trace")
    def trace_context():
        return current_trace_fields()

    FastAPIInstrumentor.instrument_app(
        traced_app,
        tracer_provider=provider,
        server_request_hook=_fastapi_request_hook,
    )
    response = TestClient(traced_app).get(
        "/trace?phone=%2B237600000000",
        headers={"traceparent": f"00-{TRACE_ID}-{PARENT_SPAN_ID}-01"},
    )
    FastAPIInstrumentor.uninstrument_app(traced_app)
    provider.shutdown()

    assert response.status_code == 200
    assert response.json()["trace_id"] == TRACE_ID
    assert exporter.get_finished_spans()
    assert "+237600000000" not in str(
        [span.attributes for span in exporter.get_finished_spans()]
    )


def test_httpx_injects_traceparent_into_provider_call() -> None:
    provider, exporter = _provider()
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["traceparent"] = request.headers["traceparent"]
        return httpx.Response(200)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://provider.test"
    )
    HTTPXClientInstrumentor.instrument_client(
        client,
        tracer_provider=provider,
        request_hook=_httpx_request_hook,
    )
    with provider.get_tracer("test").start_as_current_span("payment-provider-call"):
        client.get("/transaction/verify/dpi_private-reference?email=private@example.com")
    HTTPXClientInstrumentor.uninstrument_client(client)
    provider.shutdown()

    assert captured["traceparent"].startswith("00-")
    assert len(captured["traceparent"].split("-")) == 4
    attributes = str([span.attributes for span in exporter.get_finished_spans()])
    assert "dpi_private-reference" not in attributes
    assert "private@example.com" not in attributes


def test_log_trace_fields_cannot_be_overridden() -> None:
    provider, _ = _provider()
    with provider.get_tracer("test").start_as_current_span("log-correlation"):
        expected = current_trace_fields()
        payload = build_log_payload(
            "info",
            "payment.test",
            {"trace_id": "forged", "span_id": "forged"},
        )
    provider.shutdown()

    assert payload["trace_id"] == expected["trace_id"]
    assert payload["span_id"] == expected["span_id"]
