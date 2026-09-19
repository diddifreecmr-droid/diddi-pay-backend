"""Point d'entrée de l'APP BASE `payfund` (DiddiPay + DiddiFund).

Base URL : `/payfund/v1` (Contrat API, en-tête du document).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import ProgrammingError

from payfund_app.core.config import get_settings
from payfund_app.core.database import SessionLocal
from payfund_app.core.errors import register_exception_handlers
from payfund_app.core.observability.metrics import router as metrics_router
from payfund_app.core.observability.middleware import ObservabilityMiddleware
from payfund_app.core.observability.tracing import configure_tracing
from payfund_app.modules.fund.presentation.routers import router as fund_router
from payfund_app.modules.payments.presentation.routers import router as payment_router
from payfund_app.modules.payments.presentation.webhook_router import router as payment_webhook_router
from payfund_app.modules.payments.presentation.summary_router import router as payment_summary_router
from payfund_app.modules.payments.infra.repositories import PaymentOutboxRepository
from payfund_app.modules.wallet.infra.repositories import OutboxRepository
from payfund_app.modules.wallet.infra import subscribers as wallet_subscribers
from payfund_app.ops.maintenance import relay_outbox_events
from payfund_app.modules.wallet.presentation.routers import router as wallet_router
from payfund_app.shared_kernel.events.bus import RedisEventBus, get_bus
from payfund_app.shared_kernel.logging import configure_logging

configure_logging()

API_PREFIX = "/payfund/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    bus = get_bus()
    wallet_subscribers.register(bus)
    if isinstance(bus, RedisEventBus):
        bus.start()
    with SessionLocal() as session:
        relay_outbox_events(session, bus)
    yield
    if isinstance(bus, RedisEventBus):
        bus.stop()
    if tracer_provider is not None:
        tracer_provider.shutdown()


app = FastAPI(
    title="DiddiPay / DiddiFund",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=f"{API_PREFIX}/docs",
    openapi_url=f"{API_PREFIX}/openapi.json",
)

register_exception_handlers(app)
tracer_provider = configure_tracing(app)

# Authentification par JWT dans l'en-tête `Authorization`, pas par cookie : pas besoin de
# `allow_credentials`, donc `allow_origins=["*"]` reste valide (le navigateur l'accepterait de
# toute façon mal combiné à des credentials). Restreindre via `CORS_ORIGINS` en production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list,
    allow_origin_regex=get_settings().cors_origin_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ObservabilityMiddleware)

app.include_router(metrics_router)
app.include_router(wallet_router, prefix=API_PREFIX)
app.include_router(fund_router, prefix=API_PREFIX)
app.include_router(payment_router, prefix=API_PREFIX)
app.include_router(payment_webhook_router, prefix=API_PREFIX)
app.include_router(payment_summary_router, prefix=API_PREFIX)


@app.get(f"{API_PREFIX}/health", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(f"{API_PREFIX}/ready", tags=["ops"])
def ready() -> dict[str, str]:
    """Readiness du runtime: la base est joignable et l'outbox existe."""
    with SessionLocal() as session:
        try:
            OutboxRepository(session).pending(limit=1)
            PaymentOutboxRepository(session).status_counts()
        except ProgrammingError:
            session.rollback()
            return {"status": "degraded"}
    return {"status": "ready"}
