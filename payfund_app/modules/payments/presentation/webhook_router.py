"""Provider callback endpoints for the payment orchestrator (ticket 110, step 3).

One generic route serves every processor and callback kind:

    POST /payfund/v1/webhooks/{processor}/{kind}      kind = payments | deposits | refunds | payouts

The processor's plugin and declared capabilities decide whether the route exists (404 if
not). Signature/notification validation stays inside the adapter, on the raw bytes.

Aliases kept so provider dashboards keep working unchanged:
    POST /payfund/v1/payments/webhooks/paystack   -> paystack / payments
    POST /payfund/v1/payments/webhooks/cinetpay   -> cinetpay / payments
    POST /webhooks/pawapay/{kind}                  (root path, legacy PawaPay dashboard)
"""

from typing import Annotated

from fastapi import APIRouter, Header, Request

from payfund_app.core.errors import NotFound, ServiceUnavailable, Unauthenticated
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
from payfund_app.modules.payments.application.ports import WebhookKind
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.application.refund_webhooks import (
    RefundWebhookUseCases,
)
from payfund_app.modules.payments.application.webhooks import PaymentWebhookUseCases
from payfund_app.modules.payments.infra.processor_plugins import plugin_named
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
    SessionDep,
    WebhookProcessorResolverDep,
)
from payfund_app.modules.payments.presentation.schemas import (
    PaymentWebhookResponse,
    PayoutWebhookResponse,
    RefundWebhookResponse,
)
from payfund_app.shared_kernel.logging import emit

# URL segment -> callback kind. "deposits" is PawaPay's word for a collection callback.
KIND_SEGMENTS: dict[str, WebhookKind] = {
    "payments": WebhookKind.PAYMENT,
    "deposits": WebhookKind.PAYMENT,
    "refunds": WebhookKind.REFUND,
    "payouts": WebhookKind.PAYOUT,
}
# Historical operation labels in logs.
_OPERATION = {WebhookKind.PAYMENT: "deposit", WebhookKind.REFUND: "refund", WebhookKind.PAYOUT: "payout"}
_PROCESSED_EVENT = {
    WebhookKind.PAYMENT: "payment.webhook.processed",
    WebhookKind.REFUND: "refund.webhook.processed",
    WebhookKind.PAYOUT: "payout.webhook.processed",
}

WebhookResponse = PaymentWebhookResponse | RefundWebhookResponse | PayoutWebhookResponse

_ROUTE_DOC = {
    "summary": "Receive a provider callback",
    "description": (
        "Provider-only endpoint. `processor` is a configured DiddiPay processor and `kind` one "
        "of payments (alias deposits), refunds, payouts that it declares. The adapter validates "
        "the callback on the raw bytes (signature or stored notify token) and, where the "
        "provider requires it, confirms the status with the provider. The event is stored in "
        "the provider inbox and applied exactly once. Unknown processor/kind: 404."
    ),
    "openapi_extra": {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True},
                    "example": {
                        "depositId": "00000000-0000-0000-0000-000000000001",
                        "status": "COMPLETED",
                        "depositedAmount": "5000",
                        "currency": "XOF",
                    },
                }
            },
        }
    },
}
# The legacy root PawaPay route documents the same body.
_PAWAPAY_LEGACY_EXTRA = _ROUTE_DOC["openapi_extra"]

webhooks_router = APIRouter(prefix="/webhooks", tags=["payment-webhooks"])
router = APIRouter(prefix="/payments/webhooks", tags=["payment-webhooks"])
pawapay_router = APIRouter(prefix="/webhooks/pawapay", tags=["payment-webhooks"])


def _resolve_kind(processor_name: str, kind_segment: str) -> WebhookKind:
    kind = KIND_SEGMENTS.get(kind_segment)
    plugin = plugin_named(processor_name)
    if kind is None or plugin is None or not plugin.capabilities.receives(kind):
        raise NotFound(
            "Route de callback inconnue.",
            code="WEBHOOK_ROUTE_NOT_FOUND",
            details={"processor": processor_name, "kind": kind_segment},
        )
    return kind


def _apply(kind: WebhookKind, processor, raw_body: bytes, headers, session):
    if kind is WebhookKind.PAYMENT:
        use_cases = PaymentWebhookUseCases(
            PaymentIntentRepository(session),
            PaymentAttemptRepository(session),
            ProviderEventRepository(session),
            SqlAlchemyUnitOfWork(session),
            PaymentOutboxRepository(session),
            PaymentAccountingService(FinancialLedgerRepository(session)),
        )
        result = use_cases.process(processor, raw_body, headers)
        return result, PaymentWebhookResponse(
            status=result.status,
            event_key=result.event_key,
            payment_intent_id=result.payment_intent_id,
        ), {"payment_intent_id": result.payment_intent_id}

    callback_url = getattr(processor, "callback_url", None)
    if kind is WebhookKind.REFUND:
        use_cases = RefundWebhookUseCases(
            RefundRepository(session),
            PaymentIntentRepository(session),
            PaymentAttemptRepository(session),
            ProviderEventRepository(session),
            SqlAlchemyUnitOfWork(session),
            PaymentAccountingService(FinancialLedgerRepository(session)),
            PaymentOutboxRepository(session),
        )
        result = use_cases.process(processor, raw_body, headers, callback_url=callback_url)
        return result, RefundWebhookResponse(
            status=result.status, event_key=result.event_key, refund_id=result.refund_id
        ), {"refund_id": result.refund_id}

    uow = SqlAlchemyUnitOfWork(session)
    payout_use_cases = PayoutUseCases(
        PayoutRepository(session),
        PaymentOutboxRepository(session),
        ProcessorRegistry(),
        uow,
        PayoutAccountingService(FinancialLedgerRepository(session)),
    )
    use_cases = PayoutWebhookUseCases(
        PayoutRepository(session), ProviderEventRepository(session), uow, payout_use_cases
    )
    result = use_cases.process(processor, raw_body, headers, callback_url=callback_url)
    return result, PayoutWebhookResponse(
        status=result.status, event_key=result.event_key, payout_id=result.payout_id
    ), {"payout_id": result.payout_id}


async def _handle(
    processor_name: str,
    kind_segment: str,
    request: Request,
    session,
    resolve_processor,
) -> WebhookResponse:
    processor_name = processor_name.strip().lower()
    kind = _resolve_kind(processor_name, kind_segment)
    raw_body = await request.body()
    processor = resolve_processor(processor_name, kind)
    try:
        result, response, ids = _apply(kind, processor, raw_body, request.headers, session)
    except ProcessorWebhookRejected as exc:
        observe_webhook(provider=processor_name, outcome="rejected")
        emit(
            "warning",
            "payment.webhook.rejected",
            processor=processor_name,
            operation=_OPERATION[kind],
            reason=str(exc)[:160],
        )
        raise Unauthenticated(
            f"Callback {processor_name} invalide.", code="WEBHOOK_REJECTED"
        ) from exc
    except ProcessorCallUncertain as exc:
        observe_webhook(provider=processor_name, outcome="verification_unavailable")
        raise ServiceUnavailable(
            f"La verification {processor_name} est temporairement indisponible.",
            code="PROCESSOR_UNAVAILABLE",
            details={"provider": processor_name, "reference": exc.provider_reference},
        ) from exc
    observe_webhook(provider=processor_name, outcome=result.status)
    emit(
        "info" if result.status in {"processed", "duplicate", "ignored"} else "warning",
        _PROCESSED_EVENT[kind],
        processor=processor_name,
        event_key=result.event_key,
        status=result.status,
        **ids,
    )
    return response


@webhooks_router.post("/{processor_name}/{kind_segment}", response_model=WebhookResponse, **_ROUTE_DOC)
async def provider_webhook(
    processor_name: str,
    kind_segment: str,
    request: Request,
    session: SessionDep,
    resolve_processor: WebhookProcessorResolverDep,
) -> WebhookResponse:
    return await _handle(processor_name, kind_segment, request, session, resolve_processor)


def _json_body(example: dict) -> dict:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True},
                    "example": example,
                }
            },
        }
    }


@router.post(
    "/paystack",
    response_model=PaymentWebhookResponse,
    summary="Receive a signed Paystack event (alias of /webhooks/paystack/payments)",
    description=(
        "Provider-only endpoint, kept for the Paystack dashboard. DiddiPay verifies "
        "X-Paystack-Signature against the exact raw request bytes before parsing or applying "
        "the event."
    ),
    openapi_extra=_json_body(
        {
            "event": "charge.success",
            "data": {"reference": "dpi_example", "amount": 500000, "currency": "XOF", "status": "success"},
        }
    ),
)
async def paystack_webhook(
    request: Request,
    session: SessionDep,
    resolve_processor: WebhookProcessorResolverDep,
    x_paystack_signature: Annotated[
        str | None,
        Header(
            alias="X-Paystack-Signature",
            description="HMAC SHA-512 signature calculated by Paystack over the raw body.",
        ),
    ] = None,
) -> WebhookResponse:
    # Documented here; the adapter reads the header mapping so verification uses raw bytes.
    del x_paystack_signature
    return await _handle("paystack", "payments", request, session, resolve_processor)


@router.post(
    "/cinetpay",
    response_model=PaymentWebhookResponse,
    summary="Receive a CinetPay notification (alias of /webhooks/cinetpay/payments)",
    description=(
        "Kept for the CinetPay notify_url. CinetPay notifications are not trusted as payment "
        "proof: the stored per-payment notify_token is checked and the payment is verified "
        "with CinetPay before the idempotent inbox and accounting flow applies it."
    ),
    openapi_extra=_json_body(
        {
            "notify_token": "returned-at-payment-initialization",
            "merchant_transaction_id": "00000000-0000-0000-0000-000000000001",
            "transaction_id": "ER-T-EXAMPLE",
            "status": "SUCCESS",
        }
    ),
)
async def cinetpay_webhook(
    request: Request, session: SessionDep, resolve_processor: WebhookProcessorResolverDep
) -> WebhookResponse:
    return await _handle("cinetpay", "payments", request, session, resolve_processor)


@pawapay_router.post(
    "/{kind_segment}",
    response_model=WebhookResponse,
    summary="Receive a PawaPay callback (legacy root path)",
    description="Root path still configured in the PawaPay dashboard; same handler as /payfund/v1/webhooks/pawapay/{kind}.",
    openapi_extra=_PAWAPAY_LEGACY_EXTRA,
)
async def pawapay_legacy_webhook(
    kind_segment: str,
    request: Request,
    session: SessionDep,
    resolve_processor: WebhookProcessorResolverDep,
) -> WebhookResponse:
    return await _handle("pawapay", kind_segment, request, session, resolve_processor)
