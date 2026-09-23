"""Pilotage-only aggregate of confirmed payment journal events."""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel

from payfund_app.core.config import get_settings
from payfund_app.core.errors import Unauthenticated
from payfund_app.core.security import ServicePrincipal, decode_service_token
from payfund_app.modules.payments.application.daily_summary import (
    PaymentDailySummary,
    PaymentDailySummaryUseCases,
)
from payfund_app.modules.payments.infra.daily_summary import (
    SqlPaymentDailySummaryRepository,
)
from payfund_app.modules.payments.presentation.deps import SessionDep

router = APIRouter(prefix="/internal/v1", tags=["internal-pilotage"])


class PaymentSummaryResponse(BaseModel):
    date: date
    timezone: str
    currency: str
    confirmed_payments_count: int
    confirmed_payments_amount_xof: int
    confirmed_refunds_count: int
    confirmed_refunds_amount_xof: int
    calculated_at: datetime
    source: str


def require_pilotage_service(
    authorization: Annotated[str | None, Header()] = None,
    client_id: Annotated[str | None, Header(alias="X-Client-ID")] = None,
) -> ServicePrincipal:
    settings = get_settings()
    if not settings.payment_summary_client_id:
        raise Unauthenticated("Client Pilotage non configure.")
    if not authorization or not authorization.startswith("Bearer ") or not client_id:
        raise Unauthenticated()
    accepted_scopes = {settings.payment_summary_scope}
    if settings.payment_summary_legacy_scope:
        accepted_scopes.add(settings.payment_summary_legacy_scope)
    return decode_service_token(
        authorization.removeprefix("Bearer ").strip(),
        audience=settings.payment_summary_audience,
        client_id_header=client_id,
        required_scopes=accepted_scopes,
        allowed_client_ids={settings.payment_summary_client_id},
    )


@router.get("/payment-summary", response_model=PaymentSummaryResponse)
def payment_summary(
    session: SessionDep,
    date_: Annotated[date, Query(alias="date", description="Business day in Africa/Abidjan")],
    _: Annotated[ServicePrincipal, Depends(require_pilotage_service)],
) -> PaymentSummaryResponse:
    result: PaymentDailySummary = PaymentDailySummaryUseCases(
        SqlPaymentDailySummaryRepository(session)
    ).get(date_)
    return PaymentSummaryResponse.model_validate(result, from_attributes=True)
