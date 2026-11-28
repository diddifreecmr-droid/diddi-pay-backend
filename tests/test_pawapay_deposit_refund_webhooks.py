import uuid

from sqlalchemy import select

from payfund_app.main import app
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
    Refund,
    RefundStatus,
)
from payfund_app.modules.payments.infra.models import (
    FinancialJournalRecord,
    PaymentOutboxRecord,
    ProviderEventRecord,
)
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
    RefundRepository,
)
from payfund_app.modules.payments.presentation.deps import (
    get_pawapay_deposit_webhook_processor,
    get_pawapay_refund_webhook_processor,
)

DEPOSIT_URL = "/payfund/v1/webhooks/pawapay/deposits"
REFUND_URL = "/payfund/v1/webhooks/pawapay/refunds"


def _processor() -> PawapayPaymentProcessor:
    return PawapayPaymentProcessor(
        api_token="test-token",
        base_url="https://api.pawapay.test",
        callback_signature_required=False,
    )


def _payment(session, *, status: PaymentIntentStatus, attempt_status: AttemptStatus):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference=f"ride:{uuid.uuid4()}",
        money=Money(5_000),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="a" * 64,
        status=status,
    )
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor="pawapay",
        money=intent.money,
        attempt_number=1,
        status=attempt_status,
        provider_reference=str(uuid.uuid4()),
    )
    PaymentIntentRepository(session).add(intent)
    PaymentAttemptRepository(session).add(attempt)
    session.commit()
    return intent, attempt


def test_pawapay_deposit_callback_succeeds_payment_intent_once(client, session):
    intent, attempt = _payment(
        session,
        status=PaymentIntentStatus.REQUIRES_ACTION,
        attempt_status=AttemptStatus.REQUIRES_ACTION,
    )
    app.dependency_overrides[get_pawapay_deposit_webhook_processor] = _processor
    body = {
        "depositId": attempt.provider_reference,
        "status": "COMPLETED",
        "requestedAmount": "5000",
        "depositedAmount": "5000",
        "currency": "XOF",
        "country": "CIV",
        "payer": {"msisdn": "+225000000000"},
    }

    first = client.post(DEPOSIT_URL, json=body)
    replay = client.post(DEPOSIT_URL, json=body)

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"
    assert PaymentIntentRepository(session).get(intent.id).status == PaymentIntentStatus.SUCCEEDED
    assert session.scalar(select(PaymentOutboxRecord)).event_type == "payment.succeeded"
    assert session.scalar(select(ProviderEventRecord)).status == "processed"


def test_pawapay_refund_callback_updates_refund_and_ledger_once(client, session):
    intent, attempt = _payment(
        session,
        status=PaymentIntentStatus.SUCCEEDED,
        attempt_status=AttemptStatus.SUCCEEDED,
    )
    refund_id = uuid.uuid4()
    refund = Refund(
        id=refund_id,
        payment_intent_id=intent.id,
        payment_attempt_id=attempt.id,
        money=Money(2_000),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="b" * 64,
        status=RefundStatus.PROCESSING,
        provider_reference=str(uuid.uuid4()),
    )
    RefundRepository(session).add(refund, client_id="diddigo", processor="pawapay")
    session.commit()
    app.dependency_overrides[get_pawapay_refund_webhook_processor] = _processor
    body = {
        "refundId": refund.provider_reference,
        "status": "COMPLETED",
        "amount": "2000",
        "currency": "XOF",
        "country": "CIV",
    }

    first = client.post(REFUND_URL, json=body)
    replay = client.post(REFUND_URL, json=body)

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"
    session.expire_all()
    assert PaymentIntentRepository(session).get(intent.id).refunded_amount == 2_000
    assert RefundRepository(session).get_by_provider_reference(
        "pawapay", refund.provider_reference
    ).status == RefundStatus.SUCCEEDED
    assert session.scalar(
        select(FinancialJournalRecord).where(FinancialJournalRecord.event_type == "refund")
    )
    assert session.scalar(select(ProviderEventRecord)).refund_id == refund_id
    events = list(
        session.scalars(
            select(PaymentOutboxRecord).where(
                PaymentOutboxRecord.event_type == "diddipay.refund.succeeded.v1"
            )
        )
    )
    assert len(events) == 1
    assert events[0].client_id == "diddigo"
    assert events[0].payload["business_reference"] == intent.business_reference
    assert events[0].payload["amount"] == 2_000
    assert events[0].payload["processor"] == "pawapay"
