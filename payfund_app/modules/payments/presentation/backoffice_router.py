"""S2S-only operational payment views for DiddiAdmin Backoffice."""

from datetime import datetime
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from payfund_app.core.config import get_settings
from payfund_app.core.errors import NotFound, Unauthenticated
from payfund_app.core.security import ServicePrincipal, decode_service_token
from payfund_app.modules.payments.application.backoffice import BackofficePaymentQueries
from payfund_app.modules.payments.infra.backoffice import SqlBackofficePaymentRepository
from payfund_app.modules.payments.presentation.deps import SessionDep

router = APIRouter(prefix="/internal/backoffice", tags=["internal-backoffice"])


class BackofficeAttemptResponse(BaseModel):
    id: uuid.UUID
    processor: str
    status: str
    provider_reference: str | None
    provider_status: str | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


class BackofficePaymentResponse(BaseModel):
    id: uuid.UUID
    client_id: str
    business_reference: str
    amount: int
    currency: str
    status: str
    refunded_amount: int
    created_at: datetime
    updated_at: datetime
    attempts: list[BackofficeAttemptResponse] = Field(default_factory=list)
    financial_summary: dict[str, int] | None = None


class BackofficePaymentCollection(BaseModel):
    items: list[BackofficePaymentResponse]
    page: int
    page_size: int
    total: int


def require_backoffice_reader(
    authorization: Annotated[str | None, Header()] = None,
    client_id: Annotated[str | None, Header(alias="X-Client-ID")] = None,
) -> ServicePrincipal:
    settings = get_settings()
    allowed_clients = settings.backoffice_client_id_set
    if not allowed_clients:
        raise Unauthenticated("Client Backoffice non configure.")
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthenticated("Token de service Backoffice requis.")
    return decode_service_token(
        authorization.removeprefix("Bearer ").strip(),
        audience=settings.backoffice_audience,
        client_id_header=client_id,
        required_scopes={settings.backoffice_read_scope},
        allowed_client_ids=allowed_clients,
    )


def _response(payment) -> BackofficePaymentResponse:
    return BackofficePaymentResponse.model_validate(payment, from_attributes=True)


@router.get("/payments", response_model=BackofficePaymentCollection)
def list_backoffice_payments(
    session: SessionDep,
    _: Annotated[ServicePrincipal, Depends(require_backoffice_reader)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Literal[
        "requires_action",
        "processing",
        "succeeded",
        "failed",
        "cancelled",
        "partially_refunded",
        "refunded",
    ]
    | None = None,
) -> BackofficePaymentCollection:
    result = BackofficePaymentQueries(SqlBackofficePaymentRepository(session)).list(
        page=page, page_size=page_size, status=status
    )
    return BackofficePaymentCollection(
        items=[_response(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/payments/{payment_intent_id}", response_model=BackofficePaymentResponse)
def get_backoffice_payment(
    payment_intent_id: uuid.UUID,
    session: SessionDep,
    _: Annotated[ServicePrincipal, Depends(require_backoffice_reader)],
) -> BackofficePaymentResponse:
    payment = BackofficePaymentQueries(SqlBackofficePaymentRepository(session)).get(
        payment_intent_id
    )
    if payment is None:
        raise NotFound("PaymentIntent introuvable.", code="PAYMENT_INTENT_NOT_FOUND")
    return _response(payment)
