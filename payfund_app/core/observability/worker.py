"""Per-process private metrics endpoint and cycle instrumentation."""

import hmac
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

from opentelemetry import trace
from opentelemetry.context import Context
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from payfund_app.core.config import get_settings
from payfund_app.core.observability.metrics import (
    HEARTBEAT,
    JOB_DURATION,
    JOBS,
    QUEUE_AGE,
    QUEUE_DEAD,
    QUEUE_PENDING,
    permitted_peer,
)


def start_metrics_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            settings = get_settings()
            if not permitted_peer(self.client_address[0]):
                self.send_error(401)
                return
            if self.path != "/internal/metrics":
                self.send_error(404)
                return
            if not settings.metrics_enabled or not settings.metrics_token:
                self.send_error(503)
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(
                supplied.encode(), ("Bearer " + settings.metrics_token).encode()
            ):
                self.send_error(401)
                return
            body = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

    server = HTTPServer(("0.0.0.0", get_settings().worker_metrics_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@contextmanager
def worker_cycle(job_type="payment_maintenance"):
    started = time.perf_counter()
    outcome = "success"
    try:
        with trace.get_tracer("diddipay.worker").start_as_current_span(
            "worker.cycle", context=Context(), record_exception=False
        ):
            yield
    except Exception:
        outcome = "error"
        raise
    finally:
        JOBS.labels(job_type, outcome).inc()
        JOB_DURATION.labels(job_type).observe(time.perf_counter() - started)
        if outcome == "success":
            HEARTBEAT.labels("payment").set(time.time())


def observe_queues(session):
    # Fixed schema/catalogue: never interpolate user-controlled names into SQL or labels.
    for schema, pending, dead in (
        ("payments", "('pending','delivering')", "dead_letter"),
        ("wallet", "('pending')", "__none__"),
    ):
        row = session.execute(
            text(f"""
            SELECT count(*) FILTER (WHERE status IN {pending}),
                   count(*) FILTER (WHERE status = :dead),
                   COALESCE(EXTRACT(EPOCH FROM (now() - min(created_at) FILTER (WHERE status IN {pending}))), 0)
            FROM {schema}.outbox_events
        """),
            {"dead": dead},
        ).one()
        QUEUE_PENDING.labels(schema + "_outbox").set(row[0])
        QUEUE_DEAD.labels(schema + "_outbox").set(row[1])
        QUEUE_AGE.labels(schema + "_outbox").set(max(0, float(row[2])))
