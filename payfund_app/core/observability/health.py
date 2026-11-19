"""Read-only probes: one in-flight check, no stale success, three-second total budget."""

import asyncio
import threading
from concurrent.futures import Future
from functools import lru_cache

import redis
from sqlalchemy import create_engine, text

from payfund_app.core.config import get_settings
from payfund_app.core.production_readiness import production_configuration_errors

_lock = threading.Lock()
_inflight: Future | None = None


@lru_cache(maxsize=1)
def _engine():
    return create_engine(
        get_settings().database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
        connect_args={
            "connect_timeout": 1,
            "options": "-c statement_timeout=700 -c lock_timeout=500",
        },
    )


def check_dependencies() -> str:
    settings = get_settings()
    if production_configuration_errors(settings):
        return "unavailable"
    try:
        with _engine().connect() as connection:
            connection.execute(
                text("SELECT telemetry_context FROM wallet.outbox_events LIMIT 1")
            )
            connection.execute(
                text("SELECT telemetry_context FROM payments.outbox_events LIMIT 1")
            )
    except Exception:  # noqa: BLE001 - isolate telemetry/probe failure
        return "unavailable"
    try:
        with redis.Redis.from_url(
            settings.redis_url, socket_timeout=0.4, socket_connect_timeout=0.4
        ) as client:
            client.ping()
    except Exception:  # noqa: BLE001 - isolate telemetry/probe failure
        # Redis is critical for stream publication; callbacks can continue without it.
        return (
            "unavailable"
            if settings.payment_event_transport == "redis_stream"
            else "degraded"
        )
    return "ready"


async def readiness() -> str:
    global _inflight
    with _lock:
        if _inflight is None or _inflight.done():
            future = Future()
            _inflight = future

            def run():
                try:
                    future.set_result(check_dependencies())
                except Exception:  # noqa: BLE001 - isolate telemetry/probe failure
                    future.set_result("unavailable")

            threading.Thread(target=run, daemon=True).start()
        current = _inflight
    try:
        return await asyncio.wait_for(
            asyncio.shield(asyncio.wrap_future(current)), timeout=2.8
        )
    except TimeoutError:
        return "unavailable"
