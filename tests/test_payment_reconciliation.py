import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.application.ports import ProviderResult
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.application.reconciliation import (
    PaymentReconciliationUseCases,
)
from payfund_app.modules.payments.application.webhooks import PaymentWebhookUseCases
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.infra.models import (
    FinancialJournalRecord,
    PaymentOutboxRecord,
)
from payfund_app.modules.payments.infra.paystack_processor import (
    PaystackPaymentProcessor,
)
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentAttemptRepository,
    PaymentIntentRepository,
    PaymentOutboxRepository,
    ProviderEventRepository,
)
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork


class SuccessfulProcessor(SandboxPaymentProcessor):
    name = "paystack"

    def verify_payment(self, provider_reference):
        return ProviderResult(provider_reference, AttemptStatus.SUCCEEDED, "success", amount=5000, currency="XOF", fee=100)


def test_reconciliation_recovers_missed_success_webhook(session):
    intents, attempts = PaymentIntentRepository(session), PaymentAttemptRepository(session)
    intent = PaymentIntent(client_id="diddigo", business_reference="ride:reconcile", money=Money(5000), idempotency_key=str(uuid.uuid4()), request_fingerprint="a"*64, status=PaymentIntentStatus.PROCESSING)
    attempt = PaymentAttempt(payment_intent_id=intent.id, processor="paystack", money=intent.money, attempt_number=1, status=AttemptStatus.UNKNOWN, provider_reference="dpi_reconcile", updated_at=datetime.now(UTC)-timedelta(minutes=10))
    intents.add(intent); attempts.add(attempt); session.commit()
    registry = ProcessorRegistry(); registry.register(SuccessfulProcessor())
    events = ProviderEventRepository(session)
    uow = SqlAlchemyUnitOfWork(session)
    outbox = PaymentOutboxRepository(session)
    accounting = PaymentAccountingService(FinancialLedgerRepository(session))
    result = PaymentReconciliationUseCases(intents, attempts, events, registry, uow, outbox, accounting).run(minimum_age_seconds=0)
    assert result.succeeded == 1
    assert intents.get(intent.id).status == PaymentIntentStatus.SUCCEEDED
    assert attempts.get(attempt.id).status == AttemptStatus.SUCCEEDED
    assert [row.event_type for row in session.scalars(select(FinancialJournalRecord))] == ["capture", "processor_fee"]
    assert [row.event_type for row in session.scalars(select(PaymentOutboxRecord))] == ["payment.succeeded"]

    raw = json.dumps({"event": "charge.success", "data": {
        "reference": attempt.provider_reference, "status": "success", "amount": 5000,
        "currency": "XOF", "fees": 100,
    }}).encode()
    secret = "sk_test"
    signature = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    webhook_result = PaymentWebhookUseCases(intents, attempts, events, uow, outbox, accounting).process(
        PaystackPaymentProcessor(secret_key=secret), raw, {"X-Paystack-Signature": signature}
    )
    assert webhook_result.status == "processed"
    assert len(list(session.scalars(select(PaymentOutboxRecord)))) == 1
    assert len(list(session.scalars(select(FinancialJournalRecord)))) == 2


def test_reconciliation_rejects_reference_mismatch_without_effects(session):
    intents, attempts = PaymentIntentRepository(session), PaymentAttemptRepository(session)
    intent = PaymentIntent(client_id="diddigo", business_reference="ride:mismatch", money=Money(5000), idempotency_key=str(uuid.uuid4()), request_fingerprint="a"*64, status=PaymentIntentStatus.PROCESSING)
    attempt = PaymentAttempt(payment_intent_id=intent.id, processor="paystack", money=intent.money, attempt_number=1, status=AttemptStatus.UNKNOWN, provider_reference="dpi_expected", updated_at=datetime.now(UTC)-timedelta(minutes=10))
    intents.add(intent); attempts.add(attempt); session.commit()

    class WrongReference(SuccessfulProcessor):
        def verify_payment(self, provider_reference):
            return ProviderResult("dpi_wrong", AttemptStatus.SUCCEEDED, "success", amount=5000, currency="XOF")

    registry = ProcessorRegistry(); registry.register(WrongReference())
    result = PaymentReconciliationUseCases(intents, attempts, ProviderEventRepository(session), registry, SqlAlchemyUnitOfWork(session), PaymentOutboxRepository(session), PaymentAccountingService(FinancialLedgerRepository(session))).run(minimum_age_seconds=0)
    assert result.mismatched == 1
    assert intents.get(intent.id).status == PaymentIntentStatus.PROCESSING
    assert list(session.scalars(select(PaymentOutboxRecord))) == []
