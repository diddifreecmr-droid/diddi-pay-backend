"""Signed provider webhook endpoints for the payment orchestrator."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from payfund_app.core.errors import Unauthenticated
from payfund_app.core.observability.business_metrics import observe_webhook
from payfund_app.modules.payments.application.accounting import (
    PaymentAccountingService,
    PayoutAccountingService,
)
from payfund_app.modules.payments.application.errors import (
    ProcessorCallUncertain,
    ProcessorWebhookRejected,
)
from payfund_app.modules.payments.application.payout_webhooks import (
    PayoutWebhookUseCases,
)
from payfund_app.modules.payments.application.payouts import PayoutUseCases
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.application.webhooks import PaymentWebhookUseCases
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentAttemptRepository,
    PaymentIntentRepository,
    PaymentOutboxRepository,
    PayoutRepository,
    ProviderEventRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import (
    PawapayWebhookProcessorDep,
    PaystackWebhookProcessorDep,
    SessionDep,
)
from payfund_app.modules.payments.presentation.schemas import (
    PaymentWebhookResponse,
    PayoutWebhookResponse,
)
from payfund_app.shared_kernel.logging import emit

router = APIRouter(prefix="/payments/webhooks", tags=["payment-webhooks"])
pawapay_router = APIRouter(prefix="/webhooks/pawapay", tags=["payment-webhooks"])


@router.post(
    "/paystack",
    response_model=PaymentWebhookResponse,
    summary="Receive a signed Paystack event",
    description=(
        "Provider-only endpoint. DiddiPay verifies X-Paystack-Signature against the exact "
        "raw request bytes before parsing or applying the event."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True},
                    "example": {
                        "event": "charge.success",
                        "data": {
                            "reference": "dpi_example",
                            "amount": 500000,
                            "currency": "XOF",
                            "status": "success",
                        },
                    },
                }
            },
        }
    },
)
async def paystack_webhook(
    request: Request,
    session: SessionDep,
    processor: PaystackWebhookProcessorDep,
    x_paystack_signature: Annotated[
        str | None,
        Header(
            alias="X-Paystack-Signature",
            description="HMAC SHA-512 signature calculated by Paystack over the raw body.",
        ),
    ] = None,
) -> PaymentWebhookResponse:
    # The processor reads the header mapping so signature verification always uses raw bytes.
    del x_paystack_signature
    raw_body = await request.body()
    use_cases = PaymentWebhookUseCases(
        PaymentIntentRepository(session),
        PaymentAttemptRepository(session),
        ProviderEventRepository(session),
        SqlAlchemyUnitOfWork(session),
        PaymentOutboxRepository(session),
        PaymentAccountingService(FinancialLedgerRepository(session)),
    )
    try:
        result = use_cases.process(processor, raw_body, request.headers)
    except ProcessorWebhookRejected as exc:
        observe_webhook(provider="paystack", outcome="rejected")
        raise Unauthenticated("Signature webhook Paystack invalide.") from exc
    observe_webhook(provider="paystack", outcome=result.status)
    emit(
        "info" if result.status in {"processed", "duplicate", "ignored"} else "warning",
        "payment.webhook.processed",
        processor="paystack",
        event_key=result.event_key,
        payment_intent_id=result.payment_intent_id,
        status=result.status,
    )
    return PaymentWebhookResponse(
        status=result.status,
        event_key=result.event_key,
        payment_intent_id=result.payment_intent_id,
    )


@pawapay_router.post(
    "/payouts",
    response_model=PayoutWebhookResponse,
    summary="Receive a PawaPay payout callback",
    description=(
        "Provider-only endpoint. PawaPay callback signatures are verified when enabled; "
        "the payout status is then confirmed with PawaPay before DiddiPay posts accounting. "
        "Both the canonical API path and the legacy root path are exposed for migration."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True},
                    "example": {
                        "payoutId": "00000000-0000-0000-0000-000000000001",
                        "status": "COMPLETED",
                        "amount": "5000",
                        "currency": "XOF",
                        "country": "CIV",
                    },
                }
            },
        }
    },
)
async def pawapay_payout_webhook(
    request: Request,
    session: SessionDep,
    processor: PawapayWebhookProcessorDep,
) -> PayoutWebhookResponse:
    raw_body = await request.body()
    uow = SqlAlchemyUnitOfWork(session)
    payout_use_cases = PayoutUseCases(
        PayoutRepository(session),
        PaymentOutboxRepository(session),
        ProcessorRegistry(),
        uow,
        PayoutAccountingService(FinancialLedgerRepository(session)),
    )
    use_cases = PayoutWebhookUseCases(
        PayoutRepository(session),
        ProviderEventRepository(session),
        uow,
        payout_use_cases,
    )
    try:
        result = use_cases.process(
            processor,
            raw_body,
            request.headers,
            callback_url=processor.callback_url,
        )
    except ProcessorWebhookRejected as exc:
        observe_webhook(provider="pawapay", outcome="rejected")
        raise HTTPException(status_code=401, detail="Signature webhook PawaPay invalide.") from exc
    except ProcessorCallUncertain as exc:
        observe_webhook(provider="pawapay", outcome="transient_error")
        raise HTTPException(
            status_code=503,
            detail="PawaPay status verification is temporarily unavailable.",
        ) from exc
    observe_webhook(provider="pawapay", outcome=result.status)
    emit(
        "info" if result.status in {"processed", "duplicate", "ignored"} else "warning",
        "payout.webhook.processed",
        processor="pawapay",
        event_key=result.event_key,
        payout_id=result.payout_id,
        status=result.status,
    )
    return PayoutWebhookResponse(
        status=result.status,
        event_key=result.event_key,
        payout_id=result.payout_id,
    )
