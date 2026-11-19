"""Executable local C01–C10 evidence. No central ingestion claim."""

import asyncio
import json
import logging
import threading
import time
from datetime import UTC
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY, generate_latest

from payfund_app.core.config import get_settings
from payfund_app.core.observability import health
from payfund_app.core.observability.async_context import (
    capture_context,
    processing_context,
)
from payfund_app.core.observability.context import (
    bind_request_id,
    get_request_id,
    reset_request_id,
)
from payfund_app.core.observability.identity import resource_attributes
from payfund_app.core.observability.middleware import ObservabilityMiddleware
from payfund_app.core.observability.tracing import (
    CountingExporter,
    SafeBatchProcessor,
    otlp_endpoint,
)
from payfund_app.core.observability.worker import worker_cycle
from payfund_app.shared_kernel.logging import (
    JsonFormatter,
    build_log_payload,
    emit,
    serialize_payload,
)


def test_identity_and_log_bounds(monkeypatch):
    monkeypatch.setenv("OBSERVABILITY_ENVIRONMENT", "test")
    get_settings.cache_clear()
    payload = build_log_payload(
        "warning", "validation.event", {"many": ["é" * 2000] * 100}
    )
    encoded = serialize_payload(payload)
    actual = json.loads(encoded)
    assert len(encoded.encode()) <= 65536
    assert actual["attributes"]["truncated"]
    assert actual["timestamp"].endswith("Z")
    assert actual["level"] == "warn"
    assert actual["resource"] == resource_attributes()
    assert len(actual["resource"]) == 8
    assert "trace_id" not in actual and "request_id" not in actual


def test_library_exception_redaction_and_serialization_failure():
    from payfund_app.core.observability.redaction import redact

    for raw in (
        '{"password": "synthetic-secret"}',
        "{'api_key': 'synthetic-secret'}",
        "Cookie: session=synthetic-secret",
    ):
        assert "synthetic-secret" not in redact(raw)
    record = logging.LogRecord(
        "httpx",
        logging.ERROR,
        "",
        1,
        "failed https://host/private?token=synthetic-secret email=person@example.com password=private-value",
        (),
        None,
    )
    actual = JsonFormatter().format(record)
    assert "synthetic-secret" not in actual and "person@example.com" not in actual
    assert "private-value" not in actual
    assert json.loads(actual)["event"] == "library.event"

    class Bad:
        def __str__(self):
            raise RuntimeError("never serialize")

    before = (
        REGISTRY.get_sample_value(
            "diddi_telemetry_dropped_total",
            {"signal": "logs", "reason": "serialization"},
        )
        or 0
    )
    emit("error", "validation.failure", broken=Bad())
    assert (
        REGISTRY.get_sample_value(
            "diddi_telemetry_dropped_total",
            {"signal": "logs", "reason": "serialization"},
        )
        == before + 1
    )


def test_http_cardinality_and_statuses(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")
    get_settings.cache_clear()
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/validation/{identifier}")
    def get(identifier: str):
        return {"ok": True}

    @app.get("/validation-error")
    def error():
        raise RuntimeError("synthetic error")

    client = TestClient(app, raise_server_exceptions=False)
    for i in range(100):
        assert client.get(f"/validation/identifier-{i}").status_code == 200
        assert client.get(f"/unknown-{i}").status_code == 404
    assert client.get("/validation-error").status_code == 500
    assert client.request("UNBOUNDED", "/anything").status_code == 404
    body = generate_latest().decode()
    assert "identifier-99" not in body and "unknown-99" not in body
    assert 'route="/validation/{identifier}",status_code="200"' in body
    assert 'route="/_unmatched",status_code="404"' in body
    assert 'route="/validation-error",status_code="500"' in body
    assert 'method="OTHER"' in body
    assert 'diddi_http_server_request_duration_seconds_bucket{le="0.005"' in body


def test_auth_missing_config_closed_and_alias(monkeypatch):
    from payfund_app.main import app

    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "")
    get_settings.cache_clear()
    client = TestClient(app)
    assert client.get("/internal/metrics").status_code == 503
    assert (
        client.get("/payfund/v1/health", headers={"X-Correlation-ID": "alias"}).headers[
            "x-request-id"
        ]
        == "alias"
    )
    assert (
        client.get(
            "/payfund/v1/health",
            headers={"X-Request-ID": "first", "X-Correlation-ID": "second"},
        ).headers["x-request-id"]
        == "first"
    )
    assert (
        client.get("/payfund/v1/health", headers={"X-Request-ID": "bad:colon"}).headers[
            "x-request-id"
        ]
        != "bad:colon"
    )
    assert get_request_id() is None


@pytest.mark.parametrize(
    "status,code", [("ready", 200), ("degraded", 200), ("unavailable", 503)]
)
def test_health_semantics(monkeypatch, status, code):
    from payfund_app.main import app

    monkeypatch.setattr(health, "check_dependencies", lambda: status)
    client = TestClient(app)
    assert client.get("/payfund/v1/health").status_code == 200
    response = client.get("/payfund/v1/ready")
    assert response.status_code == code
    assert response.json() == {"status": status}


def test_blocked_probe_is_bounded_and_single_flight(monkeypatch):
    release = threading.Event()
    calls = []

    def blocked():
        calls.append(1)
        release.wait(5)
        return "ready"

    monkeypatch.setattr(health, "check_dependencies", blocked)

    async def concurrent():
        return await asyncio.gather(*[health.readiness() for _ in range(5)])

    started = time.monotonic()
    try:
        assert asyncio.run(concurrent()) == ["unavailable"] * 5
        assert time.monotonic() - started < 3.1
        assert len(calls) == 1
    finally:
        release.set()


def test_otlp_endpoint_rules():
    settings = SimpleNamespace(
        tracing_otlp_traces_endpoint="",
        tracing_otlp_endpoint="https://collector.test/base/",
        tracing_allow_insecure_local=False,
    )
    assert otlp_endpoint(settings) == "https://collector.test/base/v1/traces"
    settings.tracing_otlp_traces_endpoint = "https://collector.test/v1/traces"
    assert otlp_endpoint(settings) == settings.tracing_otlp_traces_endpoint
    settings.tracing_otlp_traces_endpoint = ""
    settings.tracing_otlp_endpoint = "https://collector.test/v1/traces"
    with pytest.raises(ValueError):
        otlp_endpoint(settings)
    settings.tracing_otlp_endpoint = "http://collector.test"
    with pytest.raises(ValueError):
        otlp_endpoint(settings)


def test_span_sanitizing_and_bounded_queue():
    release = threading.Event()
    entered = threading.Event()

    class Exporter(SpanExporter):
        def __init__(self):
            self.spans = []

        def export(self, spans):
            entered.set()
            release.wait(5)
            self.spans.extend(spans)
            return SpanExportResult.SUCCESS

    exporter = Exporter()
    provider = TracerProvider()
    processor = SafeBatchProcessor(
        CountingExporter(exporter), max_queue_size=2048, max_export_batch_size=1
    )
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("first"):
        pass
    assert entered.wait(1)
    for i in range(2200):
        with tracer.start_as_current_span("token=synthetic-secret") as span:
            span.set_attribute(
                "url.full", "https://example.test?token=synthetic-secret"
            )
            span.set_attribute("http.route", "/safe/{id}")
            span.record_exception(ValueError("synthetic-secret"))
            span.set_status(trace.Status(trace.StatusCode.ERROR, "synthetic-secret"))
    assert len(processor._batch_processor._queue) <= 2048
    assert (
        REGISTRY.get_sample_value(
            "diddi_telemetry_dropped_total",
            {"signal": "traces", "reason": "queue_full"},
        )
        >= 152
    )
    release.set()
    assert provider.force_flush(5000)
    assert "synthetic-secret" not in " ".join(span.to_json() for span in exporter.spans)
    provider.shutdown()


def test_async_attempts_link_origin_and_reset(monkeypatch):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    token = bind_request_id("original-request")
    with provider.get_tracer("test").start_as_current_span("publish") as origin:
        carrier = capture_context()
        original_context = origin.get_span_context()
    reset_request_id(token)
    for attempt in (1, 2):
        with processing_context(carrier, attempt=attempt):
            assert get_request_id() == "original-request"
        assert get_request_id() is None
    with processing_context({"traceparent": "bad", "request_id": "invalid id"}):
        assert get_request_id() is None
    spans = exporter.get_finished_spans()
    attempts = [span for span in spans if span.links]
    assert len(attempts) == 2
    assert attempts[0].context.span_id != attempts[1].context.span_id
    assert all(
        span.parent is None
        and span.links[0].context.trace_id == original_context.trace_id
        and span.links[0].context.span_id == original_context.span_id
        for span in attempts
    )
    provider.shutdown()


def test_worker_heartbeat_only_after_success():
    with worker_cycle():
        pass
    labels = {"worker_type": "payment"}
    before = REGISTRY.get_sample_value(
        "diddi_worker_heartbeat_timestamp_seconds", labels
    )
    with pytest.raises(RuntimeError), worker_cycle():
        raise RuntimeError("blocked/failed")
    assert (
        REGISTRY.get_sample_value("diddi_worker_heartbeat_timestamp_seconds", labels)
        == before
    )


def test_two_http_services_parentage_logs_and_context_cleanup():
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    from payfund_app.core.observability.tracing import _httpx_request_hook

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    downstream = FastAPI()
    downstream.add_middleware(ObservabilityMiddleware)
    captured = []

    @downstream.get("/downstream")
    def downstream_route():
        captured.append(build_log_payload("info", "validation.downstream", {}))
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(downstream, tracer_provider=provider)
    second_client = TestClient(downstream)

    def transport(request):
        result = second_client.get("/downstream", headers=dict(request.headers))
        return httpx.Response(result.status_code, json=result.json())

    outbound = httpx.Client(transport=httpx.MockTransport(transport))
    HTTPXClientInstrumentor.instrument_client(
        outbound, tracer_provider=provider, request_hook=_httpx_request_hook
    )
    upstream = FastAPI()
    upstream.add_middleware(ObservabilityMiddleware)

    @upstream.get("/upstream")
    def upstream_route():
        captured.append(build_log_payload("info", "validation.upstream", {}))
        return outbound.get("https://synthetic.test/downstream").json()

    FastAPIInstrumentor.instrument_app(upstream, tracer_provider=provider)
    first_client = TestClient(upstream)
    try:
        for request_id in ("one", "two"):
            response = first_client.get(
                "/upstream",
                headers={"X-Request-ID": request_id, "traceparent": "malformed"},
            )
            assert response.status_code == 200
        spans = exporter.get_finished_spans()
        for a, b in (captured[:2], captured[2:]):
            assert a["request_id"] == b["request_id"]
            assert a["trace_id"] == b["trace_id"]
            upstream_span = next(
                s for s in spans if format(s.context.span_id, "016x") == a["span_id"]
            )
            downstream_span = next(
                s for s in spans if format(s.context.span_id, "016x") == b["span_id"]
            )
            client_span = next(
                s
                for s in spans
                if s.kind == trace.SpanKind.CLIENT
                and s.context.trace_id == upstream_span.context.trace_id
            )
            assert client_span.parent.span_id == upstream_span.context.span_id
            assert downstream_span.parent.span_id == client_span.context.span_id
        assert captured[0]["trace_id"] != captured[2]["trace_id"]
        assert get_request_id() is None
    finally:
        HTTPXClientInstrumentor.uninstrument_client(outbound)
        FastAPIInstrumentor.uninstrument_app(upstream)
        FastAPIInstrumentor.uninstrument_app(downstream)
        outbound.close()
        provider.shutdown()


def test_durable_outbox_context_retry_preserves_business_payload(session, monkeypatch):
    import uuid
    from datetime import datetime, timedelta

    from payfund_app.modules.payments.application.deliveries import (
        PaymentEventDeliveryUseCases,
    )
    from payfund_app.modules.payments.application.ports import CallbackTarget
    from payfund_app.modules.payments.infra.callback_delivery import (
        HttpSignedCallbackSender,
    )
    from payfund_app.modules.payments.infra.repositories import PaymentOutboxRepository
    from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    repo = PaymentOutboxRepository(session)
    payload = {"status": "succeeded"}
    token = bind_request_id("durable-request")
    with provider.get_tracer("test").start_as_current_span("publication") as origin:
        row = repo.enqueue(
            client_id="synthetic",
            event_type="payment.succeeded",
            aggregate_id=uuid.uuid4(),
            payload=payload,
        )
        origin_trace = origin.get_span_context().trace_id
    reset_request_id(token)
    session.commit()
    session.expire_all()
    assert row.telemetry_context["request_id"] == "durable-request"
    requests = []

    def send(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200)

    delivery = PaymentEventDeliveryUseCases(
        repo,
        SqlAlchemyUnitOfWork(session),
        HttpSignedCallbackSender(httpx.Client(transport=httpx.MockTransport(send))),
        {"synthetic": CallbackTarget("https://synthetic.test/callback", "test-secret")},
    )
    assert delivery.run().retried == 1
    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert delivery.run().delivered == 1
    assert delivery.run().scanned == 0
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert json.loads(requests[0].content)["data"] == payload
    assert requests[0].headers["x-request-id"] == "durable-request"
    assert requests[0].headers["traceparent"] != requests[1].headers["traceparent"]
    attempts = [
        s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.CONSUMER
    ]
    assert len(attempts) == 2 and all(
        s.links[0].context.trace_id == origin_trace for s in attempts
    )
    assert get_request_id() is None
    provider.shutdown()


def test_metrics_network_filter_ignores_forwarded_headers(monkeypatch):
    from payfund_app.main import app

    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "synthetic-" * 4)
    monkeypatch.setenv("METRICS_ALLOWED_CIDRS", "10.23.0.0/24")
    get_settings.cache_clear()
    token = {
        "Authorization": "Bearer " + "synthetic-" * 4,
        "X-Forwarded-For": "10.23.0.2",
    }
    assert (
        TestClient(app, client=("192.0.2.1", 1234))
        .get("/internal/metrics", headers=token)
        .status_code
        == 401
    )
    assert (
        TestClient(app, client=("10.23.0.2", 1234))
        .get("/internal/metrics", headers=token)
        .status_code
        == 200
    )


def test_otlp_retry_after_and_partial_rejection(monkeypatch):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceResponse,
    )
    from requests import Response

    from payfund_app.core.observability.tracing import RateLimitedOTLPExporter

    called = []
    response = Response()
    response.status_code = 429
    response.headers["Retry-After"] = "60"

    def fake_export(*args):
        called.append(1)
        return response

    monkeypatch.setattr(OTLPSpanExporter, "_export", fake_export)
    exporter = RateLimitedOTLPExporter(endpoint="https://synthetic.test/v1/traces")
    assert exporter._export(b"", 1).status_code == 429
    assert exporter._export(b"", 1).status_code == 429
    assert len(called) == 1
    exporter._not_before = 0
    response.status_code = 200
    body = ExportTraceServiceResponse()
    body.partial_success.rejected_spans = 3
    response._content = body.SerializeToString()
    before = (
        REGISTRY.get_sample_value(
            "diddi_telemetry_dropped_total",
            {"signal": "traces", "reason": "collector_rejected"},
        )
        or 0
    )
    exporter._export(b"", 1)
    assert (
        REGISTRY.get_sample_value(
            "diddi_telemetry_dropped_total",
            {"signal": "traces", "reason": "collector_rejected"},
        )
        == before + 3
    )
    exporter.shutdown()


def test_stream_duration_includes_body_completion(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")
    get_settings.cache_clear()
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [],
        "route": SimpleNamespace(path="/synthetic-stream"),
    }
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0.04)
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    labels = {"method": "GET", "route": "/synthetic-stream"}
    before = (
        REGISTRY.get_sample_value(
            "diddi_http_server_request_duration_seconds_sum", labels
        )
        or 0
    )
    asyncio.run(ObservabilityMiddleware(app)(scope, receive, send))
    after = REGISTRY.get_sample_value(
        "diddi_http_server_request_duration_seconds_sum", labels
    )
    assert after - before >= 0.04
    assert get_request_id() is None


def test_readiness_real_dependency_decisions(monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "test")
    monkeypatch.setenv("PAYMENT_EVENT_TRANSPORT", "http_callback")
    get_settings.cache_clear()
    monkeypatch.setattr(
        health,
        "_engine",
        lambda: SimpleNamespace(
            connect=lambda: nullcontext(SimpleNamespace(execute=lambda _: None))
        ),
    )

    def failed(*args):
        raise ConnectionError("dependency down")

    monkeypatch.setattr(health.redis.Redis, "ping", failed)
    assert health.check_dependencies() == "degraded"
    monkeypatch.setenv("PAYMENT_EVENT_TRANSPORT", "redis_stream")
    get_settings.cache_clear()
    assert health.check_dependencies() == "unavailable"
    monkeypatch.setattr(health, "_engine", failed)
    assert health.check_dependencies() == "unavailable"


def test_worker_scrape_fails_closed(monkeypatch):
    from payfund_app.core.observability.worker import start_metrics_server

    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "")
    monkeypatch.setenv("METRICS_ALLOWED_CIDRS", "127.0.0.1/32")
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "worker_metrics_port", 0)
    server = start_metrics_server()
    try:
        with httpx.Client(trust_env=False) as client:
            url = f"http://127.0.0.1:{server.server_port}/internal/metrics"
            assert client.get(url).status_code == 503
            monkeypatch.setattr(settings, "metrics_token", "synthetic-token-" * 3)
            assert client.get(url).status_code == 401
            assert (
                client.get(url, headers={"Authorization": "Bearer wrong"}).status_code
                == 401
            )
            response = client.get(
                url, headers={"Authorization": "Bearer " + settings.metrics_token}
            )
            assert response.status_code == 200
            assert "diddi_worker_heartbeat_timestamp_seconds" in response.text
    finally:
        server.shutdown()
        server.server_close()


def test_otlp_sdk_posts_exact_signal_path():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from payfund_app.core.observability.tracing import RateLimitedOTLPExporter

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            paths.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings = SimpleNamespace(
        tracing_otlp_traces_endpoint="",
        tracing_otlp_endpoint=f"http://127.0.0.1:{server.server_port}",
        tracing_allow_insecure_local=True,
        observability_environment="test",
    )
    exporter = RateLimitedOTLPExporter(endpoint=otlp_endpoint(settings), timeout=1)
    try:
        assert exporter.export(()) == SpanExportResult.SUCCESS
        assert paths == ["/v1/traces"]
    finally:
        exporter.shutdown()
        server.shutdown()
        server.server_close()
