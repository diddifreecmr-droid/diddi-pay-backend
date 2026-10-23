"""S2S-only, privacy-preserving payment context for DiddiSupport."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from payfund_app.core.config import get_settings
from payfund_app.core.errors import BadRequest, Conflict, NotFound, Unauthenticated
from payfund_app.core.observability.context import get_request_id
from payfund_app.core.security import ServicePrincipal, decode_service_token
from payfund_app.modules.payments.application.support_context import (
    SupportPaymentContextQueries,
)
from payfund_app.modules.payments.infra.support_context import (
    SqlSupportPaymentContextRepository,
)
from payfund_app.modules.payments.infra.support_refunds import (
    SupportRefundActionRepository,
)
from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.application.errors import (
    IdempotencyConflict,
    PaymentNotFound,
    PaymentOperationConflict,
    ProcessorCallUncertain,
)
from payfund_app.modules.payments.application.fingerprints import request_fingerprint
from payfund_app.modules.payments.application.refunds import CreateRefundCommand, RefundUseCases
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentAttemptRepository,
    PaymentIntentRepository,
    PaymentOutboxRepository,
    RefundRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import ProcessorRegistryDep
from payfund_app.modules.payments.presentation.deps import SessionDep

router = APIRouter(prefix="/internal/support", tags=["internal-support"])


class SupportAttemptContextResponse(BaseModel):
    status: str
    provider_status: str | None
    failure_code: str | None
    occurred_at: datetime


class SupportPaymentContextResponse(BaseModel):
    contract_version: str = "support.payment-context.v1"
    payment_intent_id: uuid.UUID
    business_reference: str
    payment_status: str
    amount: int
    currency: str
    refunded_amount: int
    refund_status: str
    latest_attempt: SupportAttemptContextResponse | None
    updated_at: datetime


class SupportRefundRequest(BaseModel):
    payment_intent_id: uuid.UUID
    support_case_id: uuid.UUID
    amount: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    reason_code: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)


class SupportRefundResponse(BaseModel):
    contract_version: str = "support.payment-actions.v1"
    action_id: uuid.UUID
    status: str
    payment_intent_id: uuid.UUID
    refund_id: uuid.UUID | None
    result: dict


def require_support_reader(
    authorization: Annotated[str | None, Header()] = None,
    client_id: Annotated[str | None, Header(alias="X-Client-ID")] = None,
) -> ServicePrincipal:
    settings = get_settings()
    allowed_clients = settings.support_client_id_set
    if not allowed_clients:
        raise Unauthenticated("Client Support non configure.")
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthenticated("Token de service Support requis.")
    return decode_service_token(
        authorization.removeprefix("Bearer ").strip(),
        audience=settings.support_audience,
        client_id_header=client_id,
        required_scopes={settings.support_context_scope},
        allowed_client_ids=allowed_clients,
    )


def require_support_refunder(
    authorization: Annotated[str | None, Header()] = None,
    client_id: Annotated[str | None, Header(alias="X-Client-ID")] = None,
) -> ServicePrincipal:
    settings = get_settings()
    allowed_clients = settings.support_client_id_set
    if not allowed_clients:
        raise Unauthenticated("Client Support non configure.")
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthenticated("Token de service Support requis.")
    return decode_service_token(
        authorization.removeprefix("Bearer ").strip(),
        audience=settings.support_audience,
        client_id_header=client_id,
        required_scopes={settings.support_refund_scope},
        allowed_client_ids=allowed_clients,
    )


@router.get(
    "/payment/{payment_intent_id}/context",
    response_model=SupportPaymentContextResponse,
)
def get_support_payment_context(
    payment_intent_id: uuid.UUID,
    session: SessionDep,
    _: Annotated[ServicePrincipal, Depends(require_support_reader)],
) -> SupportPaymentContextResponse:
    context = SupportPaymentContextQueries(
        SqlSupportPaymentContextRepository(session)
    ).get(payment_intent_id)
    if context is None:
        raise NotFound("Contexte paiement introuvable.", code="PAYMENT_CONTEXT_NOT_FOUND")
    return SupportPaymentContextResponse.model_validate(context, from_attributes=True)


def _enqueue_refund_result(
    session, *, action, principal, status: str, result: dict, refund_id: uuid.UUID | None
) -> None:
    PaymentOutboxRepository(session).enqueue(
        client_id=principal.client_id,
        event_type=f"diddipay.refund.{status}.v1",
        aggregate_id=action.id,
        payload={
            "event_version": "1",
            "action_id": str(action.id),
            "support_case_id": str(action.support_case_id),
            "payment_intent_id": str(action.payment_intent_id),
            "refund_id": str(refund_id) if refund_id else None,
            "status": status,
            "result": result,
            "request_id": get_request_id(),
        },
    )


@router.get("/refund-requests/{action_id}", response_model=SupportRefundResponse)
def get_support_refund_request(
    action_id: uuid.UUID,
    session: SessionDep,
    principal: Annotated[ServicePrincipal, Depends(require_support_reader)],
) -> SupportRefundResponse:
    action = SupportRefundActionRepository(session).get(principal.client_id, action_id)
    if action is None:
        raise NotFound("Action de remboursement introuvable.", code="SUPPORT_ACTION_NOT_FOUND")
    return SupportRefundResponse(
        action_id=action.id,
        status=action.status,
        payment_intent_id=action.payment_intent_id,
        refund_id=action.refund_id,
        result=action.result,
    )


@router.post("/refund-requests", response_model=SupportRefundResponse, status_code=201)
def request_support_refund(
    payload: SupportRefundRequest,
    session: SessionDep,
    processors: ProcessorRegistryDep,
    principal: Annotated[ServicePrincipal, Depends(require_support_refunder)],
    operator_id: Annotated[str | None, Header(alias="X-Support-Operator-ID")] = None,
    delegation_id: Annotated[str | None, Header(alias="X-Support-Delegation-ID")] = None,
    command_id: Annotated[str | None, Header(alias="X-Support-Command-ID")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SupportRefundResponse:
    if not operator_id or not delegation_id or not command_id or not idempotency_key:
        raise BadRequest(
            "X-Support-Operator-ID, X-Support-Delegation-ID, X-Support-Command-ID et Idempotency-Key sont obligatoires.",
            code="SUPPORT_DELEGATION_REQUIRED",
        )
    if payload.idempotency_key != idempotency_key:
        raise BadRequest(
            "La clé du corps doit correspondre à l'en-tête Idempotency-Key.",
            code="IDEMPOTENCY_KEY_MISMATCH",
        )
    payment_intent = PaymentIntentRepository(session).get(payload.payment_intent_id)
    if payment_intent is None:
        raise NotFound("PaymentIntent introuvable.", code="PAYMENT_INTENT_NOT_FOUND")
    if payment_intent.money.currency != payload.currency.upper():
        raise Conflict("La devise du remboursement ne correspond pas au paiement.", code="REFUND_CURRENCY_MISMATCH")
    fingerprint = request_fingerprint(payload.model_dump(exclude={"idempotency_key"}))
    actions = SupportRefundActionRepository(session)
    existing = actions.get_by_idempotency(principal.client_id, idempotency_key)
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise Conflict("Cette clé a déjà été utilisée pour une autre demande.", code="IDEMPOTENCY_CONFLICT")
        return SupportRefundResponse(
            action_id=existing.id,
            status=existing.status,
            payment_intent_id=existing.payment_intent_id,
            refund_id=existing.refund_id,
            result=existing.result,
        )
    action = actions.create(
        client_id=principal.client_id,
        command_id=command_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        support_case_id=payload.support_case_id,
        operator_user_id=operator_id,
        delegation_id=delegation_id,
        payment_intent_id=payload.payment_intent_id,
        amount=payload.amount,
        currency=payload.currency.upper(),
        reason_code=payload.reason_code,
        reason=payload.reason,
    )
    try:
        refund = RefundUseCases(
            PaymentIntentRepository(session),
            PaymentAttemptRepository(session),
            RefundRepository(session),
            processors,
            SqlAlchemyUnitOfWork(session),
            PaymentAccountingService(FinancialLedgerRepository(session)),
        ).create(
            CreateRefundCommand(
                # RefundUseCases enforces ownership using the original payment client.
                # Support is the delegated actor, not the payment owner.
                client_id=payment_intent.client_id,
                payment_intent_id=payload.payment_intent_id,
                amount=payload.amount,
                reason=payload.reason,
                idempotency_key=f"support:{idempotency_key}",
            )
        )
    except ProcessorCallUncertain as exc:
        result = {"refund_id": None, "provider_reference": exc.provider_reference}
        _enqueue_refund_result(
            session,
            action=action,
            principal=principal,
            status="unknown",
            result=result,
            refund_id=None,
        )
        actions.complete(action, status="unknown", result=result, refund_id=None)
        return SupportRefundResponse(
            action_id=action.id,
            status="unknown",
            payment_intent_id=payload.payment_intent_id,
            refund_id=None,
            result=result,
        )
    except (PaymentNotFound, PaymentOperationConflict) as exc:
        session.rollback()
        status_code = "REFUND_REQUEST_NOT_ELIGIBLE" if isinstance(exc, PaymentOperationConflict) else "PAYMENT_NOT_FOUND"
        raise Conflict(str(exc), code=status_code) from exc
    except IdempotencyConflict as exc:
        session.rollback()
        raise Conflict("Conflit d'idempotence.", code="IDEMPOTENCY_CONFLICT") from exc

    result = {
        "refund_id": str(refund.id),
        "status": str(refund.status),
        "provider_status": refund.provider_status,
    }
    action_status = (
        "unknown"
        if str(refund.status) == "processing" and refund.provider_status == "unknown"
        else ("succeeded" if str(refund.status) == "succeeded" else "failed")
    )
    _enqueue_refund_result(
        session,
        action=action,
        principal=principal,
        status=action_status,
        result=result,
        refund_id=refund.id,
    )
    action = actions.complete(
        action,
        status=action_status,
        result=result,
        refund_id=refund.id,
    )
    return SupportRefundResponse(
        action_id=action.id,
        status=action.status,
        payment_intent_id=payload.payment_intent_id,
        refund_id=refund.id,
        result=result,
    )
