"""Provider-neutral PaymentIntent HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query

from payfund_app.core.errors import Conflict, NotFound, UnprocessableEntity
from payfund_app.modules.payments.application.errors import (
    IdempotencyConflict,
    PaymentNotFound,
    PaymentOperationConflict,
)
from payfund_app.modules.payments.application.processor_router import ProcessorRoutingError
from payfund_app.modules.payments.application.refunds import (
    CreateRefundCommand,
    RefundUseCases,
)
from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.application.use_cases import (
    CreatePaymentIntentCommand,
    PaymentUseCases,
    PaymentView,
)
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
    RefundRepository,
    FinancialLedgerRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import (
    PaymentClientDep,
    ProcessorRegistryDep,
    SessionDep,
)
from payfund_app.modules.payments.presentation.schemas import (
    CreatePaymentIntentRequest,
    CreateRefundRequest,
    NextActionResponse,
    PaymentAttemptResponse,
    PaymentIntentListResponse,
    PaymentIntentResponse,
    RefundResponse,
    PaymentFinancialSummaryResponse,
)

router = APIRouter(prefix="/payment-intents", tags=["payments"])


def _use_cases(session, processors) -> PaymentUseCases:
    return PaymentUseCases(
        PaymentIntentRepository(session),
        PaymentAttemptRepository(session),
        processors,
        SqlAlchemyUnitOfWork(session),
    )


def _response(view: PaymentView) -> PaymentIntentResponse:
    intent = view.intent
    attempts = []
    for attempt in view.attempts:
        action = attempt.next_action
        attempts.append(
            PaymentAttemptResponse(
                id=attempt.id,
                status=str(attempt.status),
                channel=attempt.channel,
                network=attempt.network,
                next_action=NextActionResponse(
                    type=str(action.type),
                    url=action.url,
                    instructions=action.instructions,
                    expires_at=action.expires_at,
                )
                if action
                else None,
                failure_code=attempt.failure_code,
                created_at=attempt.created_at,
                updated_at=attempt.updated_at,
            )
        )
    return PaymentIntentResponse(
        id=intent.id,
        client_id=intent.client_id,
        business_reference=intent.business_reference,
        amount=intent.money.amount,
        currency=intent.money.currency,
        status=str(intent.status),
        payer_user_id=intent.payer_user_id,
        payee_user_id=intent.payee_user_id,
        description=intent.description,
        metadata=intent.metadata,
        refunded_amount=intent.refunded_amount,
        attempts=attempts,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
    )


def _translate_error(exc: Exception) -> None:
    if isinstance(exc, PaymentNotFound):
        raise NotFound("PaymentIntent introuvable.", code="PAYMENT_INTENT_NOT_FOUND") from exc
    if isinstance(exc, IdempotencyConflict):
        raise Conflict(
            "Cette cle d'idempotence a deja ete utilisee avec une autre requete.",
            code="IDEMPOTENCY_CONFLICT",
        ) from exc
    if isinstance(exc, PaymentOperationConflict):
        raise Conflict(str(exc), code="PAYMENT_OPERATION_CONFLICT") from exc
    if isinstance(exc, ProcessorRoutingError):
        raise UnprocessableEntity(str(exc), code="PAYMENT_METHOD_UNAVAILABLE") from exc
    raise exc


@router.post("", response_model=PaymentIntentResponse, status_code=201)
def create_payment_intent(
    payload: CreatePaymentIntentRequest,
    client: PaymentClientDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PaymentIntentResponse:
    if not idempotency_key or not idempotency_key.strip():
        raise UnprocessableEntity(
            "L'en-tete Idempotency-Key est obligatoire.", code="IDEMPOTENCY_KEY_REQUIRED"
        )
    try:
        view = _use_cases(session, processors).create(
            CreatePaymentIntentCommand(
                client_id=client.client_id,
                idempotency_key=idempotency_key.strip(),
                **payload.model_dump(),
            )
        )
    except Exception as exc:
        _translate_error(exc)
        raise
    return _response(view)


@router.get("/{intent_id}", response_model=PaymentIntentResponse)
def get_payment_intent(
    intent_id: uuid.UUID,
    client: PaymentClientDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
) -> PaymentIntentResponse:
    try:
        return _response(_use_cases(session, processors).get(client.client_id, intent_id))
    except Exception as exc:
        _translate_error(exc)
        raise


@router.get("", response_model=PaymentIntentListResponse)
def list_payment_intents(
    client: PaymentClientDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
    limit: int = Query(default=50, ge=1, le=100),
) -> PaymentIntentListResponse:
    views = _use_cases(session, processors).list(client.client_id, limit=limit)
    return PaymentIntentListResponse(data=[_response(view) for view in views])


@router.post("/{intent_id}/cancel", response_model=PaymentIntentResponse)
def cancel_payment_intent(
    intent_id: uuid.UUID,
    client: PaymentClientDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
) -> PaymentIntentResponse:
    try:
        return _response(_use_cases(session, processors).cancel(client.client_id, intent_id))
    except Exception as exc:
        _translate_error(exc)
        raise


@router.post(
    "/{intent_id}/refunds", response_model=RefundResponse, status_code=201
)
def create_refund(
    intent_id: uuid.UUID,
    payload: CreateRefundRequest,
    client: PaymentClientDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RefundResponse:
    if not idempotency_key or not idempotency_key.strip():
        raise UnprocessableEntity(
            "L'en-tete Idempotency-Key est obligatoire.",
            code="IDEMPOTENCY_KEY_REQUIRED",
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
                client_id=client.client_id,
                payment_intent_id=intent_id,
                amount=payload.amount,
                reason=payload.reason,
                idempotency_key=idempotency_key.strip(),
            )
        )
    except Exception as exc:
        _translate_error(exc)
        raise
    return RefundResponse(
        id=refund.id,
        payment_intent_id=refund.payment_intent_id,
        amount=refund.money.amount if hasattr(refund, "money") else refund.amount,
        currency=refund.money.currency if hasattr(refund, "money") else refund.currency,
        status=str(refund.status),
        provider_status=refund.provider_status,
        created_at=refund.created_at,
        updated_at=refund.updated_at,
    )


@router.get(
    "/{intent_id}/financial-summary",
    response_model=PaymentFinancialSummaryResponse,
)
def get_financial_summary(
    intent_id: uuid.UUID,
    client: PaymentClientDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
) -> PaymentFinancialSummaryResponse:
    try:
        view = _use_cases(session, processors).get(client.client_id, intent_id)
    except Exception as exc:
        _translate_error(exc)
        raise
    totals = FinancialLedgerRepository(session).summary(intent_id)
    return PaymentFinancialSummaryResponse(
        payment_intent_id=intent_id,
        currency=view.intent.money.currency,
        **totals,
    )
