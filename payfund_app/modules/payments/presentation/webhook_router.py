"""Signed provider webhook endpoints for the payment orchestrator."""

from typing import Annotated

from fastapi import APIRouter, Header, Request

from payfund_app.core.errors import Unauthenticated
from payfund_app.modules.payments.application.errors import ProcessorWebhookRejected
from payfund_app.modules.payments.application.webhooks import PaymentWebhookUseCases
from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
    ProviderEventRepository,
    PaymentOutboxRepository,
    FinancialLedgerRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import (
    PaystackWebhookProcessorDep,
    SessionDep,
)
from payfund_app.modules.payments.presentation.schemas import PaymentWebhookResponse
from payfund_app.shared_kernel.logging import emit

router = APIRouter(prefix="/payments/webhooks", tags=["payment-webhooks"])


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
        raise Unauthenticated("Signature webhook Paystack invalide.") from exc
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
