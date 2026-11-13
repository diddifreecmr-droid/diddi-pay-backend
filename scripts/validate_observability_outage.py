"""Five minute synthetic local OTLP outage. No real providers or central endpoints.

Run: python -m scripts.validate_observability_outage
"""

import json
import os
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Explicit settings override .env; no remote connections.
os.environ.update(
    TRACING_ENABLED="false",
    OBSERVABILITY_ENVIRONMENT="test",
    OBSERVABILITY_ENABLED="false",
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import REGISTRY

from payfund_app.core.observability.tracing import (
    CountingExporter,
    RateLimitedOTLPExporter,
    SafeBatchProcessor,
    shutdown_tracing,
)


def main():
    recovered = threading.Event()
    accepted = []
    rejected = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if recovered.is_set():
                accepted.append(len(body))
                self.send_response(200)
            else:
                rejected.append(1)
                self.send_response(503)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    provider = TracerProvider(shutdown_on_exit=False)
    processor = SafeBatchProcessor(
        CountingExporter(
            RateLimitedOTLPExporter(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces", timeout=5
            )
        ),
        max_queue_size=2048,
        max_export_batch_size=256,
        schedule_delay_millis=5000,
    )
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("outage.validation")
    app = FastAPI()
    operations = []

    @app.post("/synthetic-operation")
    def operation():
        with tracer.start_as_current_span("synthetic.operation"):
            operations.append(1)
        return {"ok": True}

    client = TestClient(app)
    tracemalloc.start()
    started = time.monotonic()
    maximum_latency = 0
    maximum_queue = 0
    # Saturate first, then continue useful local operations throughout the five minutes.
    for _ in range(5000):
        with tracer.start_as_current_span("saturation"):
            pass
    while time.monotonic() - started < 300:
        before = time.monotonic()
        assert client.post("/synthetic-operation").status_code == 200
        maximum_latency = max(maximum_latency, time.monotonic() - before)
        maximum_queue = max(maximum_queue, len(processor._batch_processor._queue))
        time.sleep(0.25)
    outage_seconds = time.monotonic() - started
    recovered.set()
    for _ in range(10):
        assert client.post("/synthetic-operation").status_code == 200
    provider.force_flush(10000)
    _, peak = tracemalloc.get_traced_memory()
    before = time.monotonic()
    shutdown_tracing(provider)
    shutdown_seconds = time.monotonic() - before
    server.shutdown()
    result = {
        "timestamp": datetime.now(UTC).isoformat(),
        "environment": "local-synthetic",
        "outage_seconds": outage_seconds,
        "successful_operations": len(operations),
        "max_operation_seconds": maximum_latency,
        "max_queue": maximum_queue,
        "python_tracemalloc_peak_bytes": peak,
        "rejected_http_exports": len(rejected),
        "accepted_http_exports_after_recovery": len(accepted),
        "shutdown_seconds": shutdown_seconds,
        "lost_spans_queue_full": REGISTRY.get_sample_value(
            "diddi_telemetry_dropped_total",
            {"signal": "traces", "reason": "queue_full"},
        ),
        "lost_spans_export_failed": REGISTRY.get_sample_value(
            "diddi_telemetry_dropped_total",
            {"signal": "traces", "reason": "export_failed"},
        ),
        "limitations": "Synthetic local operations; no central ingestion, no real provider transaction; peak includes test harness",
    }
    assert outage_seconds >= 300 and maximum_queue <= 2048 and accepted and rejected
    assert shutdown_seconds < 5.1
    target = Path("docs/observability/evidence/outage.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
