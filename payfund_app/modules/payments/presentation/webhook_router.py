"""Signed provider webhook endpoints for the payment orchestrator."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from payfund_app.core.errors import ServiceUnavailable, Unauthenticated
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
from payfund_app.modules.payments.application.refund_webhooks import (
    RefundWebhookUseCases,
)
from payfund_app.modules.payments.application.webhooks import PaymentWebhookUseCases
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentAttemptRepository,
    PaymentIntentRepository,
    PaymentOutboxRepository,
    PayoutRepository,
    ProviderEventRepository,
    RefundRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import (
    CinetpayWebhookProcessorDep,
    PawapayDepositWebhookProcessorDep,
    PawapayRefundWebhookProcessorDep,
    PawapayWebhookProcessorDep,
    PaystackWebhookProcessorDep,
    SessionDep,
)
from payfund_app.modules.payments.presentation.schemas import (
    PaymentWebhookResponse,
    PayoutWebhookResponse,
    RefundWebhookResponse,
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
        emit(
            "warning",
            "payment.webhook.rejected",
            processor="paystack",
            reason=str(exc)[:160],
        )
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


@router.post(
    "/cinetpay",
    response_model=PaymentWebhookResponse,
    summary="Receive and verify a CinetPay payment notification",
    description=(
        "CinetPay notifications are not trusted as payment proof. DiddiPay validates the "
        "stored per-payment notify_token, verifies the payment with CinetPay, then applies "
        "the normal idempotent inbox and accounting flow."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True},
                    "example": {
                        "notify_token": "returned-at-payment-initialization",
                        "merchant_transaction_id": "00000000-0000-0000-0000-000000000001",
                        "transaction_id": "ER-T-EXAMPLE",
                        "status": "SUCCESS",
                    },
                }
            },
        }
    },
)
async def cinetpay_webhook(
    request: Request,
    session: SessionDep,
    processor: CinetpayWebhookProcessorDep,
) -> PaymentWebhookResponse:
    raw_body = await request.body()
    attempts = PaymentAttemptRepository(session)
    try:
        # The reference is used only to find the stored hash. The callback status and amount
        # remain untrusted until CinetPay's verification endpoint is called by the adapter.
        reference = processor.extract_webhook_reference(raw_body)
        attempt = attempts.get_by_provider_reference("cinetpay", reference)
        expected_hash = attempt.provider_metadata.get("notify_token_sha256") if attempt else None
        if not expected_hash:
            raise ProcessorWebhookRejected("unknown CinetPay payment notification")

        event = processor.parse_webhook(
            raw_body,
            request.headers,
            expected_notify_token_hash=str(expected_hash),
        )
        use_cases = PaymentWebhookUseCases(
            PaymentIntentRepository(session),
            attempts,
            ProviderEventRepository(session),
            SqlAlchemyUnitOfWork(session),
            PaymentOutboxRepository(session),
            PaymentAccountingService(FinancialLedgerRepository(session)),
        )
        result = use_cases.process_event(processor, raw_body, event)
    except ProcessorWebhookRejected as exc:
        observe_webhook(provider="cinetpay", outcome="rejected")
        emit(
            "warning",
            "payment.webhook.rejected",
            processor="cinetpay",
            reason=str(exc)[:160],
        )
        raise Unauthenticated("Notification webhook CinetPay invalide.") from exc
    except ProcessorCallUncertain as exc:
        observe_webhook(provider="cinetpay", outcome="verification_unavailable")
        raise ServiceUnavailable(
            "La verification CinetPay est temporairement indisponible.",
            code="PROCESSOR_UNAVAILABLE",
            details={"provider": "cinetpay", "reference": exc.provider_reference},
        ) from exc
    observe_webhook(provider="cinetpay", outcome=result.status)
    emit(
        "info" if result.status in {"processed", "duplicate", "ignored"} else "warning",
        "payment.webhook.processed",
        processor="cinetpay",
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
    "/deposits",
    response_model=PaymentWebhookResponse,
    summary="Receive a PawaPay deposit callback",
    description=(
        "Provider-only endpoint for PaymentIntent deposits. The signed callback is stored "
        "in the provider inbox before the PaymentIntent and financial ledger are updated. "
        "Missed callbacks remain recoverable through payment reconciliation."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True},
                    "example": {
                        "depositId": "00000000-0000-0000-0000-000000000001",
                        "status": "COMPLETED",
                        "requestedAmount": "5000",
                        "depositedAmount": "5000",
                        "currency": "XOF",
                        "country": "CIV",
                    },
                }
            },
        }
    },
)
async def pawapay_deposit_webhook(
    request: Request,
    session: SessionDep,
    processor: PawapayDepositWebhookProcessorDep,
) -> PaymentWebhookResponse:
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
        observe_webhook(provider="pawapay", outcome="rejected")
        emit(
            "warning",
            "payment.webhook.rejected",
            processor="pawapay",
            operation="deposit",
            reason=str(exc)[:160],
        )
        raise HTTPException(status_code=401, detail="Signature webhook PawaPay invalide.") from exc
    observe_webhook(provider="pawapay", outcome=result.status)
    emit(
        "info" if result.status in {"processed", "duplicate", "ignored"} else "warning",
        "payment.webhook.processed",
        processor="pawapay",
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
    "/refunds",
    response_model=RefundWebhookResponse,
    summary="Receive a PawaPay refund callback",
    description=(
        "Provider-only endpoint for asynchronous PawaPay refunds. The callback is validated, "
        "deduplicated and applied to the refund state and financial ledger exactly once."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True},
                    "example": {
                        "refundId": "00000000-0000-0000-0000-000000000002",
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
async def pawapay_refund_webhook(
    request: Request,
    session: SessionDep,
    processor: PawapayRefundWebhookProcessorDep,
) -> RefundWebhookResponse:
    raw_body = await request.body()
    use_cases = RefundWebhookUseCases(
        RefundRepository(session),
        PaymentIntentRepository(session),
        PaymentAttemptRepository(session),
        ProviderEventRepository(session),
        SqlAlchemyUnitOfWork(session),
        PaymentAccountingService(FinancialLedgerRepository(session)),
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
        emit(
            "warning",
            "payment.webhook.rejected",
            processor="pawapay",
            operation="refund",
            reason=str(exc)[:160],
        )
        raise HTTPException(status_code=401, detail="Signature webhook PawaPay invalide.") from exc
    observe_webhook(provider="pawapay", outcome=result.status)
    emit(
        "info" if result.status in {"processed", "duplicate", "ignored"} else "warning",
        "refund.webhook.processed",
        processor="pawapay",
        event_key=result.event_key,
        refund_id=result.refund_id,
        status=result.status,
    )
    return RefundWebhookResponse(
        status=result.status,
        event_key=result.event_key,
        refund_id=result.refund_id,
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
        emit(
            "warning",
            "payment.webhook.rejected",
            processor="pawapay",
            operation="payout",
            reason=str(exc)[:160],
        )
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
