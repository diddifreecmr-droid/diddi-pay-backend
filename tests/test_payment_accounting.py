import uuid

from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.domain import Money, PaymentAttempt, PaymentIntent
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentIntentRepository,
)


def test_capture_journal_is_idempotent_and_balanced(session):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference="ride:accounting",
        money=Money(10_000),
        idempotency_key="accounting-capture",
        request_fingerprint="a" * 64,
    )
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor="paystack",
        money=intent.money,
        attempt_number=1,
    )
    ledger = FinancialLedgerRepository(session)
    service = PaymentAccountingService(ledger)
    PaymentIntentRepository(session).add(intent)
    session.commit()

    service.record_capture(intent, attempt, event_reference="charge.success:one", fee=250)
    service.record_capture(intent, attempt, event_reference="charge.success:one", fee=250)
    session.commit()

    assert ledger.summary(intent.id) == {
        "gross_captured": 10_000,
        "refunded": 0,
        "processor_fees": 250,
        "net_expected": 9_750,
        "settled": 0,
        "outstanding": 9_750,
    }

    service.record_settlement(
        intent,
        processor="paystack",
        amount=9_750,
        settlement_reference="settlement:2026-08-15:one",
    )
    session.commit()
    assert ledger.summary(intent.id)["outstanding"] == 0


def test_settlement_cannot_exceed_expected_net(session):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference="ride:settlement-limit",
        money=Money(5_000),
        idempotency_key="settlement-limit",
        request_fingerprint="c" * 64,
    )
    PaymentIntentRepository(session).add(intent)
    session.commit()
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor="paystack",
        money=intent.money,
        attempt_number=1,
    )
    ledger = FinancialLedgerRepository(session)
    service = PaymentAccountingService(ledger)
    service.record_capture(intent, attempt, event_reference="charge.success:limit", fee=100)

    try:
        service.record_settlement(
            intent,
            processor="paystack",
            amount=4_901,
            settlement_reference="settlement:too-high",
        )
    except ValueError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("an over-settlement must be rejected")


def test_financial_summary_is_scoped_to_owning_module(client, session):
    from payfund_app.modules.payments.domain import PaymentIntentStatus
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference="ride:summary",
        money=Money(8_000),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="b" * 64,
        status=PaymentIntentStatus.SUCCEEDED,
    )
    PaymentIntentRepository(session).add(intent)
    session.commit()
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor="paystack",
        money=intent.money,
        attempt_number=1,
    )
    PaymentAccountingService(FinancialLedgerRepository(session)).record_capture(
        intent, attempt, event_reference="charge.success:summary", fee=200
    )
    session.commit()

    response = client.get(
        f"/payfund/v1/payment-intents/{intent.id}/financial-summary",
        headers={"X-Client-ID": "diddigo", "X-Service-Key": "test-service-key"},
    )
    foreign = client.get(
        f"/payfund/v1/payment-intents/{intent.id}/financial-summary",
        headers={"X-Client-ID": "diddifund", "X-Service-Key": "fund-service-key"},
    )

    assert response.status_code == 200
    assert response.json()["net_expected"] == 7_800
    assert foreign.status_code == 404
