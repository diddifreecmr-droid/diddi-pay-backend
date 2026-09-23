"""Durable-enough local heartbeat used by the container worker healthcheck."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def record_worker_heartbeat(path: str, *, healthy: bool, error: str | None = None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    payload = {
        "timestamp": time.time(),
        "healthy": healthy,
        "error": error[:255] if error else None,
    }
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, target)


def worker_is_healthy(path: str, *, max_age_seconds: int) -> bool:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        age = time.time() - float(payload["timestamp"])
        return bool(payload["healthy"]) and 0 <= age <= max_age_seconds
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--max-age", type=int, default=90)
    args = parser.parse_args(argv)
    return 0 if worker_is_healthy(args.path, max_age_seconds=args.max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
