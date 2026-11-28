"""Ticket 116: modules receive payment.failed as soon as DiddiPay learns a payment failed."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from payfund_app.main import app
from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.application.ports import ProviderResult
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.application.reconciliation import PaymentReconciliationUseCases
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.infra.models import PaymentOutboxRecord
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentAttemptRepository,
    PaymentIntentRepository,
    PaymentOutboxRepository,
    ProviderEventRepository,
)
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import get_webhook_processor_resolver

DEPOSIT_URL = "/payfund/v1/webhooks/pawapay/deposits"


def _pending_payment(session, *, processor="pawapay", age_minutes=0):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference=f"ride:{uuid.uuid4()}",
        money=Money(5_000),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="a" * 64,
        status=PaymentIntentStatus.REQUIRES_ACTION,
    )
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor=processor,
        money=intent.money,
        attempt_number=1,
        status=AttemptStatus.REQUIRES_ACTION,
        provider_reference=str(uuid.uuid4()),
        updated_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
    )
    PaymentIntentRepository(session).add(intent)
    PaymentAttemptRepository(session).add(attempt)
    session.commit()
    return intent, attempt


def _events(session):
    return list(session.scalars(select(PaymentOutboxRecord).order_by(PaymentOutboxRecord.created_at)))


def test_failed_deposit_callback_publishes_payment_failed_once(client, session):
    intent, attempt = _pending_payment(session)
    app.dependency_overrides[get_webhook_processor_resolver] = lambda: lambda name, kind: (
        PawapayPaymentProcessor(api_token="t", base_url="https://api.pawapay.test")
    )
    body = {
        "depositId": attempt.provider_reference,
        "status": "FAILED",
        "requestedAmount": "5000",
        "currency": "XOF",
        "country": "CIV",
        "failureReason": {"failureCode": "PAYER_NOT_FOUND", "failureMessage": "unknown wallet"},
    }

    first = client.post(DEPOSIT_URL, json=body)
    replay = client.post(DEPOSIT_URL, json=body)

    assert first.status_code == 200 and replay.json()["status"] == "duplicate"
    assert PaymentIntentRepository(session).get(intent.id).status == PaymentIntentStatus.FAILED
    events = _events(session)
    assert [e.event_type for e in events] == ["payment.failed"]
    payload = events[0].payload
    assert events[0].client_id == "diddigo"
    assert payload["payment_intent_id"] == str(intent.id)
    assert payload["business_reference"] == intent.business_reference
    assert payload["status"] == "failed"
    assert payload["amount"] == 5_000


class _FailingVerify(SandboxPaymentProcessor):
    name = "pawapay"

    def verify_payment(self, provider_reference):
        return ProviderResult(provider_reference, AttemptStatus.FAILED, "failed")


def test_reconciliation_publishes_payment_failed(session):
    intent, _ = _pending_payment(session, age_minutes=10)
    registry = ProcessorRegistry()
    registry.register(_FailingVerify())
    intents, attempts = PaymentIntentRepository(session), PaymentAttemptRepository(session)
    use_cases = PaymentReconciliationUseCases(
        intents, attempts, ProviderEventRepository(session), registry,
        SqlAlchemyUnitOfWork(session), PaymentOutboxRepository(session),
        PaymentAccountingService(FinancialLedgerRepository(session)),
    )

    use_cases.run(minimum_age_seconds=0)
    use_cases.run(minimum_age_seconds=0)  # already final: no second event

    assert intents.get(intent.id).status == PaymentIntentStatus.FAILED
    assert [e.event_type for e in _events(session)] == ["payment.failed"]


def test_still_pending_after_callback_publishes_nothing(client, session):
    _, attempt = _pending_payment(session)
    app.dependency_overrides[get_webhook_processor_resolver] = lambda: lambda name, kind: (
        PawapayPaymentProcessor(api_token="t", base_url="https://api.pawapay.test")
    )

    response = client.post(DEPOSIT_URL, json={
        "depositId": attempt.provider_reference, "status": "PROCESSING",
        "requestedAmount": "5000", "currency": "XOF", "country": "CIV",
    })

    assert response.status_code == 200
    assert _events(session) == []
