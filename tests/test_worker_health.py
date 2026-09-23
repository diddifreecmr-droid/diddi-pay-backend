import json
import time

from payfund_app.ops.worker_health import record_worker_heartbeat, worker_is_healthy


def test_worker_heartbeat_reports_recent_success(tmp_path):
    path = tmp_path / "worker.json"
    record_worker_heartbeat(str(path), healthy=True)
    assert worker_is_healthy(str(path), max_age_seconds=90)


def test_worker_heartbeat_rejects_last_failed_cycle(tmp_path):
    path = tmp_path / "worker.json"
    record_worker_heartbeat(str(path), healthy=False, error="database unavailable")
    assert not worker_is_healthy(str(path), max_age_seconds=90)
    assert json.loads(path.read_text())["error"] == "database unavailable"


def test_worker_heartbeat_rejects_stale_or_missing_file(tmp_path):
    path = tmp_path / "worker.json"
    path.write_text(json.dumps({"timestamp": time.time() - 120, "healthy": True}))
    assert not worker_is_healthy(str(path), max_age_seconds=90)
    assert not worker_is_healthy(str(tmp_path / "missing.json"), max_age_seconds=90)
