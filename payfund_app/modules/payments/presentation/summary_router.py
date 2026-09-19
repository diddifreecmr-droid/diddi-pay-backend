"""Pilotage-only aggregate of confirmed payment journal events."""

from datetime import date, datetime
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel

from payfund_app.core.config import get_settings
from payfund_app.core.errors import Forbidden, Unauthenticated
from payfund_app.core.security import _client
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
) -> None:
    settings = get_settings()
    if not settings.payment_summary_client_id:
        raise Unauthenticated("Client Pilotage non configure.")
    if not authorization or not authorization.startswith("Bearer ") or not client_id:
        raise Unauthenticated()
    token = authorization.removeprefix("Bearer ").strip()
    try:
        key = _client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.diddifreeid_issuer,
            audience=settings.payment_summary_audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp", "client_id"]},
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise Unauthenticated() from exc
    if (
        claims.get("token_type") != "service"
        or claims.get("role") != "service"
        or claims.get("status") != "active"
        or claims.get("sub") != "service:pilotage"
        or claims.get("client_id") != client_id
    ):
        raise Unauthenticated()
    if client_id != settings.payment_summary_client_id:
        raise Forbidden("Service non autorise pour le resume DiddiPay.")
    scopes = claims.get("scope")
    if not isinstance(scopes, str) or settings.payment_summary_scope not in scopes.split():
        raise Forbidden("Scope insuffisant pour le resume DiddiPay.")


@router.get("/payment-summary", response_model=PaymentSummaryResponse)
def payment_summary(
    session: SessionDep,
    date_: Annotated[date, Query(alias="date", description="Business day in Africa/Abidjan")],
    _: Annotated[None, Depends(require_pilotage_service)],
) -> PaymentSummaryResponse:
    result: PaymentDailySummary = PaymentDailySummaryUseCases(
        SqlPaymentDailySummaryRepository(session)
    ).get(date_)
    return PaymentSummaryResponse.model_validate(result, from_attributes=True)
