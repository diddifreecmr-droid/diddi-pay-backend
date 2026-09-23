"""S2S API for module-owned payouts."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query

from payfund_app.core.errors import Conflict, NotFound, UnprocessableEntity
from payfund_app.modules.payments.application.accounting import PayoutAccountingService
from payfund_app.modules.payments.application.errors import (
    IdempotencyConflict,
    PaymentNotFound,
)
from payfund_app.modules.payments.application.payouts import (
    CreatePayoutCommand,
    PayoutUseCases,
    PayoutView,
)
from payfund_app.modules.payments.application.processor_router import (
    ProcessorRoutingError,
)
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentOutboxRepository,
    PayoutRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import (
    PaymentPayoutReaderDep,
    PaymentPayoutWriterDep,
    ProcessorRegistryDep,
    SessionDep,
)
from payfund_app.modules.payments.presentation.schemas import (
    CreatePayoutRequest,
    PayoutFinancialSummaryResponse,
    PayoutResponse,
)
from payfund_app.shared_kernel.logging import emit

router = APIRouter(prefix="/payouts", tags=["payouts"])


def _use_cases(session, processors) -> PayoutUseCases:
    return PayoutUseCases(
        PayoutRepository(session),
        PaymentOutboxRepository(session),
        processors,
        SqlAlchemyUnitOfWork(session),
        PayoutAccountingService(FinancialLedgerRepository(session)),
    )


def _response(view: PayoutView) -> PayoutResponse:
    payout = view.payout
    return PayoutResponse(
        id=payout.id,
        client_id=payout.client_id,
        business_reference=payout.business_reference,
        beneficiary_reference=payout.beneficiary_reference,
        amount=payout.money.amount,
        currency=payout.money.currency,
        status=str(payout.status),
        processor=payout.processor,
        provider_status=payout.provider_status,
        failure_code=payout.failure_code,
        metadata=payout.metadata,
        created_at=payout.created_at,
        updated_at=payout.updated_at,
    )


def _translate(exc: Exception) -> None:
    if isinstance(exc, PaymentNotFound):
        raise NotFound("Payout introuvable.", code="PAYOUT_NOT_FOUND") from exc
    if isinstance(exc, IdempotencyConflict):
        raise Conflict(
            "Cette cle d'idempotence a deja ete utilisee avec une autre requete.",
            code="IDEMPOTENCY_CONFLICT",
        ) from exc
    if isinstance(exc, ProcessorRoutingError):
        raise UnprocessableEntity(str(exc), code="PAYOUT_METHOD_UNAVAILABLE") from exc
    raise exc


@router.post("", response_model=PayoutResponse, status_code=201)
def create_payout(
    payload: CreatePayoutRequest,
    client: PaymentPayoutWriterDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PayoutResponse:
    if not idempotency_key or not idempotency_key.strip():
        raise UnprocessableEntity(
            "L'en-tete Idempotency-Key est obligatoire.", code="IDEMPOTENCY_KEY_REQUIRED"
        )
    try:
        view = _use_cases(session, processors).create(
            CreatePayoutCommand(
                client_id=client.client_id,
                idempotency_key=idempotency_key.strip(),
                **payload.model_dump(),
            )
        )
    except Exception as exc:
        _translate(exc)
        raise
    emit(
        "info",
        "payout.resolved",
        payout_id=str(view.payout.id),
        client_id=client.client_id,
        business_reference=view.payout.business_reference,
        status=str(view.payout.status),
    )
    return _response(view)


@router.get("/{payout_id}", response_model=PayoutResponse)
def get_payout(
    payout_id: uuid.UUID,
    client: PaymentPayoutReaderDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
) -> PayoutResponse:
    try:
        return _response(_use_cases(session, processors).get(client.client_id, payout_id))
    except Exception as exc:
        _translate(exc)
        raise


@router.get("/{payout_id}/financial-summary", response_model=PayoutFinancialSummaryResponse)
def get_payout_financial_summary(
    payout_id: uuid.UUID,
    client: PaymentPayoutReaderDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
) -> PayoutFinancialSummaryResponse:
    try:
        view = _use_cases(session, processors).get(client.client_id, payout_id)
    except Exception as exc:
        _translate(exc)
        raise
    totals = FinancialLedgerRepository(session).payout_summary(payout_id)
    return PayoutFinancialSummaryResponse(
        payout_id=payout_id,
        currency=view.payout.money.currency,
        paid_out=totals.get("payout", 0),
    )


@router.get("", response_model=PayoutResponse)
def get_payout_by_business_reference(
    client: PaymentPayoutReaderDep,
    session: SessionDep,
    processors: ProcessorRegistryDep,
    business_reference: str = Query(min_length=1, max_length=128),
) -> PayoutResponse:
    try:
        return _response(
            _use_cases(session, processors).get_by_business_reference(
                client.client_id, business_reference
            )
        )
    except Exception as exc:
        _translate(exc)
        raise
