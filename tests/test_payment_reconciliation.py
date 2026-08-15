import uuid
from datetime import UTC, datetime, timedelta

from payfund_app.modules.payments.application.ports import ProviderResult
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.application.reconciliation import PaymentReconciliationUseCases
from payfund_app.modules.payments.domain import AttemptStatus, Money, PaymentAttempt, PaymentIntent, PaymentIntentStatus
from payfund_app.modules.payments.infra.repositories import PaymentAttemptRepository, PaymentIntentRepository, ProviderEventRepository
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork


class SuccessfulProcessor(SandboxPaymentProcessor):
    name = "paystack"

    def verify_payment(self, provider_reference):
        return ProviderResult(provider_reference, AttemptStatus.SUCCEEDED, "success", amount=5000, currency="XOF")


def test_reconciliation_recovers_missed_success_webhook(session):
    intents, attempts = PaymentIntentRepository(session), PaymentAttemptRepository(session)
    intent = PaymentIntent(client_id="diddigo", business_reference="ride:reconcile", money=Money(5000), idempotency_key=str(uuid.uuid4()), request_fingerprint="a"*64, status=PaymentIntentStatus.PROCESSING)
    attempt = PaymentAttempt(payment_intent_id=intent.id, processor="paystack", money=intent.money, attempt_number=1, status=AttemptStatus.UNKNOWN, provider_reference="dpi_reconcile", updated_at=datetime.now(UTC)-timedelta(minutes=10))
    intents.add(intent); attempts.add(attempt); session.commit()
    registry = ProcessorRegistry(); registry.register(SuccessfulProcessor())
    result = PaymentReconciliationUseCases(intents, attempts, ProviderEventRepository(session), registry, SqlAlchemyUnitOfWork(session)).run(minimum_age_seconds=0)
    assert result.succeeded == 1
    assert intents.get(intent.id).status == PaymentIntentStatus.SUCCEEDED
    assert attempts.get(attempt.id).status == AttemptStatus.SUCCEEDED
