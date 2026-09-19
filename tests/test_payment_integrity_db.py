from uuid import uuid4

from payfund_app.modules.payments.domain import (
    Money,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.infra.integrity import SqlPaymentIntegrityRepository
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentIntentRepository,
    PaymentOutboxRepository,
)


def test_integrity_audit_finds_and_clears_missing_effects(session):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference="ride:integrity",
        money=Money(5000),
        idempotency_key=str(uuid4()),
        request_fingerprint="a" * 64,
        status=PaymentIntentStatus.SUCCEEDED,
    )
    PaymentIntentRepository(session).add(intent)
    audit = SqlPaymentIntegrityRepository(session)
    assert [(gap.missing_capture, gap.missing_callback) for gap in audit.find_gaps(10)] == [(True, True)]

    FinancialLedgerRepository(session).post(
        payment_intent_id=intent.id,
        event_type="capture",
        event_reference="integrity-test",
        amount=5000,
        currency="XOF",
        debit_account="processor_receivable:paystack",
        credit_account="module_payable:diddigo",
    )
    assert [(gap.missing_capture, gap.missing_callback) for gap in audit.find_gaps(10)] == [(False, True)]

    PaymentOutboxRepository(session).enqueue(
        client_id="diddigo", event_type="payment.succeeded", aggregate_id=intent.id,
        payload={"payment_intent_id": str(intent.id)},
    )
    assert audit.find_gaps(10) == []
