"""Ticket 121: a refund carries the admin's choice to take it back from the payee or not."""

import uuid

from sqlalchemy import select

from payfund_app.modules.payments.application.refunds import CreateRefundCommand
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.infra.models import PaymentOutboxRecord, RefundRecord
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
)

BASE = "/payfund/v1/payment-intents"
HEADERS = {"X-Client-ID": "diddigo", "X-Service-Key": "test-service-key"}


def _paid(session):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference=f"ride:{uuid.uuid4()}",
        money=Money(10_000, "XOF"),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="f" * 64,
        status=PaymentIntentStatus.SUCCEEDED,
    )
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor="sandbox",
        money=intent.money,
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        provider_reference=f"sandbox-{uuid.uuid4()}",
    )
    PaymentIntentRepository(session).add(intent)
    PaymentAttemptRepository(session).add(attempt)
    session.commit()
    return intent


def _refund(client, intent_id, key, **extra):
    return client.post(
        f"{BASE}/{intent_id}/refunds",
        headers={**HEADERS, "Idempotency-Key": key},
        json={"amount": 2_000, "reason": "Ride cancelled", **extra},
    )


def _event_payload(session):
    row = session.scalar(
        select(PaymentOutboxRecord).where(
            PaymentOutboxRecord.event_type == "diddipay.refund.succeeded.v1"
        )
    )
    return row.payload


def test_default_refund_is_not_taken_back_from_the_payee(client, session):
    intent = _paid(session)

    response = _refund(client, intent.id, "refund-default")

    assert response.status_code == 201
    assert response.json()["recover_from_payee"] is False
    assert _event_payload(session)["recover_from_payee"] is False
    assert session.scalar(select(RefundRecord.recover_from_payee)) is False


def test_admin_can_ask_to_take_the_refund_back_from_the_payee(client, session):
    intent = _paid(session)

    response = _refund(client, intent.id, "refund-recover", recover_from_payee=True)

    assert response.status_code == 201
    assert response.json()["recover_from_payee"] is True
    assert _event_payload(session)["recover_from_payee"] is True


def test_same_key_with_a_different_choice_is_an_idempotency_conflict(client, session):
    intent = _paid(session)
    assert _refund(client, intent.id, "refund-choice").status_code == 201

    replay = _refund(client, intent.id, "refund-choice", recover_from_payee=True)

    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_fingerprint_without_the_flag_is_unchanged():
    # Refunds created before ticket 121 must still replay with their Idempotency-Key.
    base = dict(client_id="diddigo", payment_intent_id=uuid.UUID(int=1), amount=100,
                idempotency_key="k", reason="r")
    old_style = CreateRefundCommand(**base).fingerprint()
    assert CreateRefundCommand(**base, recover_from_payee=False).fingerprint() == old_style
    assert CreateRefundCommand(**base, recover_from_payee=True).fingerprint() != old_style
