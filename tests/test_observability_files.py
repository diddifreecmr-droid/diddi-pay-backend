"""Local file transport evidence; no collector or real payment involved."""

import errno
import json
import logging
import threading
import time
from logging.config import fileConfig
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider

from payfund_app.core.config import get_settings
from payfund_app.core.observability.context import bind_request_id, reset_request_id
from payfund_app.core.observability.file_logging import BoundedFileHandler
from payfund_app.core.observability.metrics import TELEMETRY_DROPPED
from payfund_app.shared_kernel.logging import JsonFormatter, configure_logging, logger


def send(handler, message="synthetic.event"):
    handler.handle(logging.LogRecord("library", logging.INFO, "", 0, message, (), None))


def drained(handler):
    deadline = time.monotonic() + 3
    while handler.lines.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    assert handler.lines.unfinished_tasks == 0


def count(reason):
    return TELEMETRY_DROPPED.labels("logs", reason)._value.get()


def test_rotation_restart_bounded_archives(tmp_path):
    path = tmp_path / "api.jsonl"
    for cycle in range(2):
        handler = BoundedFileHandler(str(path), max_bytes=2048, backups=5)
        handler.setFormatter(JsonFormatter())
        for i in range(100):
            send(handler, f"cycle-{cycle}-event-{i}")
        drained(handler)
        handler.close()
        assert len(list(tmp_path.glob("api.jsonl.[0-9]*"))) == 5
        assert sum(p.stat().st_size for p in tmp_path.glob("api.jsonl*")) <= 6 * 2048
        for file in tmp_path.glob("api.jsonl*"):
            if file.suffix != ".lock":
                for line in file.read_text(encoding="utf-8").splitlines():
                    assert json.loads(line)["timestamp"].endswith("Z")
    assert "cycle-1-event-99" in path.read_text()


@pytest.mark.parametrize("error", [errno.EACCES, errno.ENOSPC])
def test_write_failure_counted_then_recovery(tmp_path, monkeypatch, error):
    handler = BoundedFileHandler(str(tmp_path / "api.jsonl"))
    original = handler._sink.emit
    before = count("file_write_failed")

    def fail(line):
        raise OSError(error, "synthetic disk failure")

    monkeypatch.setattr(handler._sink, "emit", fail)
    send(handler)
    drained(handler)
    assert count("file_write_failed") == before + 1
    monkeypatch.setattr(handler._sink, "emit", original)
    send(handler, "recovered")
    drained(handler)
    handler.close()
    assert "recovered" in (tmp_path / "api.jsonl").read_text()


def test_slow_disk_queue_and_shutdown_are_bounded(tmp_path, monkeypatch):
    handler = BoundedFileHandler(str(tmp_path / "api.jsonl"), capacity=2)
    started, release = threading.Event(), threading.Event()

    def blocked(line):
        started.set()
        release.wait(5)

    monkeypatch.setattr(handler._sink, "emit", blocked)
    send(handler)
    assert started.wait(1)
    before = count("file_queue_full")
    start = time.monotonic()
    for _ in range(100):
        send(handler)
    assert time.monotonic() - start < 0.5
    assert handler.lines.qsize() == 2
    assert count("file_queue_full") == before + 98
    start = time.monotonic()
    handler.close()
    assert time.monotonic() - start < 1.5
    release.set()
    handler.thread.join(2)
    assert not handler.thread.is_alive()


def test_exclusive_writer_and_restart(tmp_path):
    path = str(tmp_path / "api.jsonl")
    first, second = BoundedFileHandler(path), BoundedFileHandler(path)
    send(first, "owner")
    drained(first)
    before = count("file_write_failed")
    send(second, "contender")
    drained(second)
    assert count("file_write_failed") == before + 1
    first.close()
    send(second, "new-owner")
    drained(second)
    second.close()
    assert (tmp_path / "api.jsonl").read_text().splitlines() == ["owner", "new-owner"]


def test_recover_partial_line_after_crash(tmp_path):
    path = tmp_path / "api.jsonl"
    path.write_bytes(b'{"complete":true}\n{"partial":')
    before = count("file_incomplete_line")
    handler = BoundedFileHandler(str(path))
    handler.setFormatter(JsonFormatter())
    send(handler, "after-crash")
    drained(handler)
    handler.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2 and rows[0] == {"complete": True}
    assert count("file_incomplete_line") == before + 1


def test_application_library_context_redaction_and_reconfigure(tmp_path, monkeypatch):
    path = tmp_path / "api.jsonl"
    monkeypatch.setenv("OBSERVABILITY_LOG_FILE", str(path))
    get_settings.cache_clear()
    configure_logging()
    configure_logging()
    handler = next(h for h in logger.handlers if isinstance(h, BoundedFileHandler))
    token = bind_request_id("synthetic-file-request")
    try:
        tracer = TracerProvider().get_tracer("file-test")
        with tracer.start_as_current_span("synthetic") as span:
            expected_trace = f"{span.get_span_context().trace_id:032x}"
            logger.info("app password=synthetic-secret")
            logging.getLogger("httpx").warning("library password=synthetic-secret")
    finally:
        reset_request_id(token)
    drained(handler)
    fileConfig(Path(__file__).resolve().parents[1] / "alembic.ini")
    # Actual migration logging reconfiguration closes/disables existing handlers.
    configure_logging()
    current = next(h for h in logger.handlers if isinstance(h, BoundedFileHandler))
    assert current is not handler
    logger.info("restart")
    logging.getLogger("alembic").info("migration.startup")
    drained(current)
    current.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 4
    assert all(r["request_id"] == "synthetic-file-request" for r in rows[:2])
    assert all(r["trace_id"] == expected_trace for r in rows[:2])
    assert "synthetic-secret" not in path.read_text()
    monkeypatch.setenv("OBSERVABILITY_LOG_FILE", "")
    get_settings.cache_clear()
    configure_logging()
