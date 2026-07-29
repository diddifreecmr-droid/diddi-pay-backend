"""Point d'entrée de l'APP BASE `payfund` (DiddiPay + DiddiFund).

Base URL : `/payfund/v1` (Contrat API, en-tête du document).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from payfund_app.core.errors import register_exception_handlers
from payfund_app.modules.fund.presentation.routers import router as fund_router
from payfund_app.modules.wallet.infra import subscribers as wallet_subscribers
from payfund_app.modules.wallet.presentation.routers import router as wallet_router
from payfund_app.shared_kernel.events.bus import RedisEventBus, get_bus

logging.basicConfig(level=logging.INFO)

API_PREFIX = "/payfund/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    bus = get_bus()
    wallet_subscribers.register(bus)
    if isinstance(bus, RedisEventBus):
        bus.start()
    yield
    if isinstance(bus, RedisEventBus):
        bus.stop()


app = FastAPI(
    title="DiddiPay / DiddiFund",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=f"{API_PREFIX}/docs",
    openapi_url=f"{API_PREFIX}/openapi.json",
)

register_exception_handlers(app)
app.include_router(wallet_router, prefix=API_PREFIX)
app.include_router(fund_router, prefix=API_PREFIX)


@app.get(f"{API_PREFIX}/health", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}
