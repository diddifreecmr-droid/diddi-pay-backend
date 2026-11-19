import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.config import dictConfig

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)
from opentelemetry.sdk._logs.export import LogRecordExportResult
from opentelemetry.sdk.trace import TracerProvider
from uvicorn.config import LOGGING_CONFIG

from payfund_app.core.config import Settings, get_settings
from payfund_app.core.observability import otlp_logging
from payfund_app.core.observability.metrics import TELEMETRY_DROPPED
from payfund_app.core.observability.tracing import otlp_endpoint
from payfund_app.shared_kernel.logging import configure_logging, emit, logger


def test_standard_settings_and_legacy_precedence(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("OBSERVABILITY_ENVIRONMENT", "production")
    monkeypatch.setenv("DEPLOYMENT_ID", "diddipay-staging")
    monkeypatch.setenv("METRICS_TOKEN", "a" * 32)
    monkeypatch.setenv("METRICS_BEARER_TOKEN", "b" * 32)
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.invalid/otlp/diddipay"
    )
    s = Settings(_env_file=None)
    assert s.observability_environment == "staging"
    assert s.observability_deployment_id == "diddipay-staging"
    assert s.metrics_token == "b" * 32
    assert otlp_endpoint(s) == "https://collector.invalid/otlp/diddipay/v1/traces"
    with pytest.raises(ValueError):
        Settings(_env_file=None, otel_exporter_otlp_protocol="grpc")


@pytest.mark.parametrize("component", ["api", "worker"])
def test_real_logger_after_uvicorn_configuration_once(monkeypatch, component):
    batches = []

    class Memory:
        def export(self, records):
            batches.extend(records)
            return LogRecordExportResult.SUCCESS

        def shutdown(self):
            pass

    monkeypatch.setattr(otlp_logging, "LogExporter", lambda **kwargs: Memory())
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "true")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://collector.invalid/v1/logs"
    )
    monkeypatch.setenv("OBSERVABILITY_COMPONENT", component)
    get_settings.cache_clear()
    dictConfig(LOGGING_CONFIG)
    configure_logging()
    configure_logging()
    h = next(h for h in logger.handlers if isinstance(h, otlp_logging.OTLPJsonHandler))
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("marker") as span:
        emit("info", "onboarding.synthetic", password="never-export-me")
        logging.getLogger("httpx").warning("text password=never-export-me")
    h.provider.force_flush()
    assert len(batches) == 2
    for row in batches:
        assert row.resource.attributes["diddi.component"] == component
        assert row.log_record.trace_id == span.get_span_context().trace_id
        assert "never-export-me" not in row.log_record.body
        assert json.loads(row.log_record.body)["schema_version"] == "1.0.0"
    # Exporter diagnostics cannot loop back into this handler.
    logging.getLogger("opentelemetry.exporter").warning("export failed")
    h.provider.force_flush()
    assert len(batches) == 2
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "false")
    get_settings.cache_clear()
    configure_logging()
    assert h.stopped
    assert not any(isinstance(h, otlp_logging.OTLPJsonHandler) for h in logger.handlers)


def test_real_protobuf_post_exact_path_and_failure_recovery(monkeypatch):
    requests = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, self.headers["Content-Type"], body))
            self.send_response(400 if len(requests) == 1 else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("OTEL_LOGS_ENABLED", "true")
        monkeypatch.setenv("TRACING_ALLOW_INSECURE_LOCAL", "true")
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
            f"http://127.0.0.1:{server.server_port}/otlp/diddipay/v1/logs",
        )
        get_settings.cache_clear()
        configure_logging()
        before = TELEMETRY_DROPPED.labels("logs", "export_failed")._value.get()
        emit("info", "wire.failed")
        h = next(
            h for h in logger.handlers if isinstance(h, otlp_logging.OTLPJsonHandler)
        )
        h.provider.force_flush()
        assert (
            TELEMETRY_DROPPED.labels("logs", "export_failed")._value.get() == before + 1
        )
        emit("info", "wire.synthetic")
        h.provider.force_flush()
        assert len(requests) == 2
        path, content_type, body = requests[1]
        assert path == "/otlp/diddipay/v1/logs"
        assert content_type == "application/x-protobuf"
        message = ExportLogsServiceRequest.FromString(body)
        assert (
            "wire.synthetic"
            in message.resource_logs[0].scope_logs[0].log_records[0].body.string_value
        )
    finally:
        monkeypatch.setenv("OTEL_LOGS_ENABLED", "false")
        get_settings.cache_clear()
        configure_logging()
        server.shutdown()
        server.server_close()


def test_log_export_failure_counted():
    class Broken:
        def export(self, batch):
            raise OSError("synthetic outage")

    metric = TELEMETRY_DROPPED.labels("logs", "export_failed")
    before = metric._value.get()
    assert (
        otlp_logging.CountingLogExporter(Broken()).export([1, 2])
        == LogRecordExportResult.FAILURE
    )
    assert metric._value.get() == before + 2


def test_missing_endpoints_do_not_export(monkeypatch):
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "TRACING_OTLP_ENDPOINT",
        "TRACING_OTLP_TRACES_ENDPOINT",
    ):
        monkeypatch.setenv(name, "")
    settings = Settings(_env_file=None)
    assert otlp_endpoint(settings) == ""
    with pytest.raises(ValueError):
        otlp_logging.logs_endpoint(settings)


def test_logs_queue_and_shutdown_bounded(monkeypatch):
    started, release = threading.Event(), threading.Event()

    class Blocked:
        def export(self, batch):
            started.set()
            release.wait(10)
            return LogRecordExportResult.SUCCESS

        def shutdown(self):
            pass

    monkeypatch.setattr(otlp_logging, "LogExporter", lambda **kwargs: Blocked())
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "true")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://collector.invalid/v1/logs"
    )
    get_settings.cache_clear()
    configure_logging()
    h = next(h for h in logger.handlers if isinstance(h, otlp_logging.OTLPJsonHandler))
    for _ in range(64):
        emit("info", "batch.synthetic")
    assert started.wait(2)
    before = TELEMETRY_DROPPED.labels("logs", "queue_full")._value.get()
    for _ in range(300):
        emit("info", "overflow.synthetic")
    assert TELEMETRY_DROPPED.labels("logs", "queue_full")._value.get() >= before + 44
    start = time.monotonic()
    h.close()
    assert time.monotonic() - start < 5.5
    release.set()
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "false")
    get_settings.cache_clear()
    configure_logging()
