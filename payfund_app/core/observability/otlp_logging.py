"""Export the existing sanitized JSON envelope once, through OTLP HTTP/protobuf."""

import json
import logging
from datetime import datetime
from urllib.parse import urlparse

from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceResponse,
)
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    LogRecordExportResult,
)
from opentelemetry.sdk.resources import Resource

from payfund_app.core.observability.identity import resource_attributes
from payfund_app.core.observability.metrics import TELEMETRY_DROPPED
from payfund_app.core.observability.tracing import shutdown_tracing


def logs_endpoint(settings):
    endpoint = settings.otel_exporter_otlp_logs_endpoint
    if not endpoint:
        raise ValueError("OTEL_LOGS_ENABLED requires OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    parsed = urlparse(endpoint)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and settings.tracing_allow_insecure_local
                and settings.observability_environment != "production"
            )
        )
    ):
        raise ValueError(
            "Logs require a complete HTTPS endpoint without credentials/query"
        )
    return endpoint


class LogExporter(OTLPLogExporter):
    def _export(self, serialized_data, timeout_sec=None):
        response = super()._export(serialized_data, timeout_sec)
        if response.ok and response.content:
            result = ExportLogsServiceResponse()
            try:
                result.ParseFromString(response.content)
                TELEMETRY_DROPPED.labels("logs", "collector_rejected").inc(
                    result.partial_success.rejected_log_records
                )
            except Exception:  # noqa: BLE001 - malformed collector reply
                TELEMETRY_DROPPED.labels("logs", "invalid_response").inc()
        return response


class CountingLogExporter:
    def __init__(self, delegate):
        self.delegate = delegate

    def export(self, batch):
        try:
            result = self.delegate.export(batch)
        except Exception:  # noqa: BLE001 - export must not break business calls
            result = LogRecordExportResult.FAILURE
        if result != LogRecordExportResult.SUCCESS:
            TELEMETRY_DROPPED.labels("logs", "export_failed").inc(len(batch))
        return result

    def shutdown(self):
        self.delegate.shutdown()


class BoundedLogProcessor(BatchLogRecordProcessor):
    def on_emit(self, record):
        if len(self._batch_processor._queue) >= 256:
            TELEMETRY_DROPPED.labels("logs", "queue_full").inc()
        super().on_emit(record)


class OTLPJsonHandler(logging.Handler):
    def __init__(self, endpoint, *, exporter=None):
        super().__init__()
        self.endpoint = endpoint
        self.stopped = False
        self.provider = LoggerProvider(
            resource=Resource.create(resource_attributes()), shutdown_on_exit=False
        )
        self.provider.add_log_record_processor(
            BoundedLogProcessor(
                CountingLogExporter(
                    exporter or LogExporter(endpoint=endpoint, timeout=5)
                ),
                max_queue_size=256,
                max_export_batch_size=64,
                schedule_delay_millis=1000,
                export_timeout_millis=5000,
            )
        )
        self.log = self.provider.get_logger("diddipay.structured")

    def emit(self, record):
        # Exporter diagnostics must never be fed back into the exporter.
        if self.stopped or record.name.startswith(
            ("opentelemetry", "urllib3", "requests")
        ):
            return
        try:
            line = self.format(record)  # Existing formatter handles JSON AND text.
            payload = json.loads(line)
            self.log.emit(
                LogRecord(
                    timestamp=int(
                        datetime.fromisoformat(payload["timestamp"]).timestamp() * 1e9
                    ),
                    body=line,
                    severity_text=payload["level"],
                    severity_number={
                        "debug": SeverityNumber.DEBUG,
                        "info": SeverityNumber.INFO,
                        "warn": SeverityNumber.WARN,
                        "error": SeverityNumber.ERROR,
                        "fatal": SeverityNumber.FATAL,
                    }.get(payload["level"], SeverityNumber.INFO),
                    trace_id=int(payload.get("trace_id") or "0", 16),
                    span_id=int(payload.get("span_id") or "0", 16),
                    attributes={
                        k: payload[k] for k in ("event", "request_id") if k in payload
                    },
                )
            )
        except Exception:  # noqa: BLE001 - logging cannot fail business operations
            TELEMETRY_DROPPED.labels("logs", "serialization").inc()

    def close(self):
        if not self.stopped:
            self.stopped = True
            shutdown_tracing(
                self.provider
            )  # Shared bounded flush/shutdown (5 seconds).
        super().close()
