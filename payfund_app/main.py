"""Point d'entrée de l'APP BASE `payfund` (DiddiPay + DiddiFund).

Base URL : `/payfund/v1` (Contrat API, en-tête du document).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from payfund_app.core.config import get_settings
from payfund_app.core.database import SessionLocal
from payfund_app.core.errors import register_exception_handlers
from payfund_app.core.observability.health import readiness
from payfund_app.core.observability.metrics import router as metrics_router
from payfund_app.core.observability.middleware import ObservabilityMiddleware
from payfund_app.core.observability.tracing import configure_tracing, shutdown_tracing
from payfund_app.core.production_readiness import production_configuration_errors
from payfund_app.modules.fund.presentation.routers import router as fund_router
from payfund_app.modules.payments.presentation.backoffice_router import (
    router as payment_backoffice_router,
)
from payfund_app.modules.payments.presentation.payout_router import (
    router as payout_router,
)
from payfund_app.modules.payments.presentation.routers import router as payment_router
from payfund_app.modules.payments.presentation.summary_router import (
    router as payment_summary_router,
)
from payfund_app.modules.payments.presentation.support_router import (
    router as payment_support_router,
)
from payfund_app.modules.payments.presentation.webhook_router import (
    pawapay_router,
)
from payfund_app.modules.payments.presentation.webhook_router import (
    router as payment_webhook_router,
)
from payfund_app.modules.wallet.infra import subscribers as wallet_subscribers
from payfund_app.modules.wallet.presentation.routers import router as wallet_router
from payfund_app.ops.maintenance import relay_outbox_events
from payfund_app.shared_kernel.events.bus import RedisEventBus, get_bus
from payfund_app.shared_kernel.logging import configure_logging, emit

configure_logging()

API_PREFIX = "/payfund/v1"

OPENAPI_TAGS = [
    {
        "name": "wallet-legacy",
        "description": (
            "API historique de conservation et mouvement d'une valeur interne DiddiFree. "
            "Elle reste active pour compatibilite et certains flux DiddiFund, mais ne doit "
            "pas etre choisie pour une nouvelle integration. Les nouveaux paiements de modules "
            "utilisent PaymentIntent."
        ),
    },
    {
        "name": "payments",
        "description": (
            "Orchestration provider-neutral des paiements externes pour DiddiGo, DiddiSend, "
            "DiddiFood, DiddiFund et les futurs modules."
        ),
    },
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    configuration_errors = production_configuration_errors(get_settings())
    if configuration_errors:
        raise RuntimeError(
            "DiddiPay production configuration is unsafe: "
            + "; ".join(configuration_errors)
        )
    bus = get_bus()
    wallet_subscribers.register(bus)
    if isinstance(bus, RedisEventBus):
        bus.start()
    with SessionLocal() as session:
        relay_outbox_events(session, bus)
    emit("info", "application.started")
    try:
        yield
    finally:
        if isinstance(bus, RedisEventBus):
            bus.stop()
        if tracer_provider is not None:
            shutdown_tracing(tracer_provider)


app = FastAPI(
    title="DiddiPay / DiddiFund",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=f"{API_PREFIX}/docs",
    openapi_url=f"{API_PREFIX}/openapi.json",
    openapi_tags=OPENAPI_TAGS,
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
app.include_router(pawapay_router, prefix=API_PREFIX)
# PawaPay staging was configured with this root callback before the API prefix was
# standardized. Keep it during migration; production should use the canonical path above.
app.include_router(pawapay_router)
app.include_router(payment_summary_router, prefix=API_PREFIX)
app.include_router(payout_router, prefix=API_PREFIX)
app.include_router(payment_backoffice_router, prefix=API_PREFIX)
app.include_router(payment_support_router, prefix=API_PREFIX)


@app.get(f"{API_PREFIX}/health", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(f"{API_PREFIX}/ready", tags=["ops"], response_model=dict[str, str])
async def ready():
    """Readiness du runtime: la base est joignable et l'outbox existe."""
    status = await readiness()
    return JSONResponse(
        {"status": status}, status_code=503 if status == "unavailable" else 200
    )
