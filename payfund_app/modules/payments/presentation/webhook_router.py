"""Signed provider webhook endpoints for the payment orchestrator."""

from fastapi import APIRouter, Request

from payfund_app.core.errors import Unauthenticated
from payfund_app.modules.payments.application.errors import ProcessorWebhookRejected
from payfund_app.modules.payments.application.webhooks import PaymentWebhookUseCases
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
    ProviderEventRepository,
    PaymentOutboxRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import (
    PaystackWebhookProcessorDep,
    SessionDep,
)
from payfund_app.modules.payments.presentation.schemas import PaymentWebhookResponse

router = APIRouter(prefix="/payments/webhooks", tags=["payment-webhooks"])


@router.post("/paystack", response_model=PaymentWebhookResponse)
async def paystack_webhook(
    request: Request,
    session: SessionDep,
    processor: PaystackWebhookProcessorDep,
) -> PaymentWebhookResponse:
    raw_body = await request.body()
    use_cases = PaymentWebhookUseCases(
        PaymentIntentRepository(session),
        PaymentAttemptRepository(session),
        ProviderEventRepository(session),
        SqlAlchemyUnitOfWork(session),
        PaymentOutboxRepository(session),
    )
    try:
        result = use_cases.process(processor, raw_body, request.headers)
    except ProcessorWebhookRejected as exc:
        raise Unauthenticated("Signature webhook Paystack invalide.") from exc
    return PaymentWebhookResponse(
        status=result.status,
        event_key=result.event_key,
        payment_intent_id=result.payment_intent_id,
    )
