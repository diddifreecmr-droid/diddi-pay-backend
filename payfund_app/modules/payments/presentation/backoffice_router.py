"""S2S-only operational payment views for DiddiAdmin Backoffice."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from payfund_app.core.config import get_settings
from payfund_app.core.errors import BadRequest, NotFound, Unauthenticated
from payfund_app.core.security import ServicePrincipal, decode_service_token
from payfund_app.modules.payments.application.backoffice import BackofficePaymentQueries
from payfund_app.modules.payments.application.backoffice_commands import (
    BackofficeCommandService,
)
from payfund_app.modules.payments.infra.backoffice import SqlBackofficePaymentRepository
from payfund_app.modules.payments.infra.backoffice_commands import (
    SqlBackofficeCommandRepository,
    SqlBackofficePaymentOperations,
)
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


class BackofficeCommandBody(BaseModel):
    contract_version: Literal["backoffice.v1"] = "backoffice.v1"
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)


class RetryCallbackBody(BackofficeCommandBody):
    pass


class RecordSettlementBody(BackofficeCommandBody):
    amount: int = Field(gt=0)
    settlement_reference: str = Field(min_length=3, max_length=180)


class BackofficeCommandResponse(BaseModel):
    contract_version: Literal["backoffice.v1"] = "backoffice.v1"
    status: str
    command_id: str
    service_audit_id: uuid.UUID
    result: dict


class BackofficeCapabilitiesResponse(BaseModel):
    contract_version: Literal["backoffice.v1"] = "backoffice.v1"
    module: Literal["diddipay"] = "diddipay"
    authentication: Literal["diddifreeid_service_token"] = "diddifreeid_service_token"
    read_scope: str
    command_scope: str
    resources: list[str]
    commands: list[str]
    command_headers: list[str]


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


def require_backoffice_commander(
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
        required_scopes={settings.backoffice_command_scope},
        allowed_client_ids=allowed_clients,
    )


def _validate_command_headers(
    body: BackofficeCommandBody,
    actor: str | None,
    command_id: str | None,
    idempotency_key: str | None,
) -> tuple[str, str, str]:
    if not actor or not command_id or not idempotency_key:
        raise BadRequest(
            "X-Backoffice-Actor, X-Backoffice-Command-Id et Idempotency-Key sont obligatoires.",
            code="BACKOFFICE_HEADERS_REQUIRED",
        )
    if body.idempotency_key != idempotency_key:
        raise BadRequest(
            "La clé du corps doit correspondre à l'en-tête Idempotency-Key.",
            code="IDEMPOTENCY_KEY_MISMATCH",
        )
    return actor, command_id, idempotency_key


def _command_response(command) -> BackofficeCommandResponse:
    return BackofficeCommandResponse(
        status=command.status,
        command_id=command.command_id,
        service_audit_id=command.id,
        result=command.result,
    )


def _response(payment) -> BackofficePaymentResponse:
    return BackofficePaymentResponse.model_validate(payment, from_attributes=True)


@router.get("/capabilities", response_model=BackofficeCapabilitiesResponse)
def backoffice_capabilities(
    _: Annotated[ServicePrincipal, Depends(require_backoffice_reader)],
) -> BackofficeCapabilitiesResponse:
    settings = get_settings()
    return BackofficeCapabilitiesResponse(
        read_scope=settings.backoffice_read_scope,
        command_scope=settings.backoffice_command_scope,
        resources=["payments", "payment_attempts", "financial_summary", "commands"],
        commands=["retry_callback", "record_settlement"],
        command_headers=[
            "X-Backoffice-Actor",
            "X-Backoffice-Command-Id",
            "Idempotency-Key",
        ],
    )


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


@router.post(
    "/payments/{payment_intent_id}/callbacks/{event_id}/retry",
    response_model=BackofficeCommandResponse,
)
def retry_payment_callback(
    payment_intent_id: uuid.UUID,
    event_id: uuid.UUID,
    body: RetryCallbackBody,
    session: SessionDep,
    principal: Annotated[ServicePrincipal, Depends(require_backoffice_commander)],
    actor: Annotated[str | None, Header(alias="X-Backoffice-Actor")] = None,
    command_id: Annotated[str | None, Header(alias="X-Backoffice-Command-Id")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BackofficeCommandResponse:
    actor, command_id, idempotency_key = _validate_command_headers(
        body, actor, command_id, idempotency_key
    )
    command = BackofficeCommandService(
        SqlBackofficeCommandRepository(session), SqlBackofficePaymentOperations(session)
    ).execute(
        client_id=principal.client_id,
        command_id=command_id,
        actor_user_id=actor,
        idempotency_key=idempotency_key,
        action="payment.callback.retry",
        target_type="payment_intent",
        target_id=payment_intent_id,
        reason=body.reason,
        payload={"event_id": str(event_id)},
    )
    return _command_response(command)


@router.post(
    "/payments/{payment_intent_id}/settlements",
    response_model=BackofficeCommandResponse,
)
def record_payment_settlement(
    payment_intent_id: uuid.UUID,
    body: RecordSettlementBody,
    session: SessionDep,
    principal: Annotated[ServicePrincipal, Depends(require_backoffice_commander)],
    actor: Annotated[str | None, Header(alias="X-Backoffice-Actor")] = None,
    command_id: Annotated[str | None, Header(alias="X-Backoffice-Command-Id")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BackofficeCommandResponse:
    actor, command_id, idempotency_key = _validate_command_headers(
        body, actor, command_id, idempotency_key
    )
    command = BackofficeCommandService(
        SqlBackofficeCommandRepository(session), SqlBackofficePaymentOperations(session)
    ).execute(
        client_id=principal.client_id,
        command_id=command_id,
        actor_user_id=actor,
        idempotency_key=idempotency_key,
        action="payment.settlement.record",
        target_type="payment_intent",
        target_id=payment_intent_id,
        reason=body.reason,
        payload={
            "amount": body.amount,
            "settlement_reference": body.settlement_reference,
        },
    )
    return _command_response(command)


@router.get("/commands/{command_id}", response_model=BackofficeCommandResponse)
def get_backoffice_command(
    command_id: str,
    session: SessionDep,
    principal: Annotated[ServicePrincipal, Depends(require_backoffice_reader)],
) -> BackofficeCommandResponse:
    command = SqlBackofficeCommandRepository(session).get(
        client_id=principal.client_id, command_id=command_id
    )
    if command is None:
        raise NotFound("Commande Backoffice introuvable.", code="COMMAND_NOT_FOUND")
    return _command_response(command)
