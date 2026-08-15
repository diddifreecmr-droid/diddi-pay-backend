import hashlib
import hmac
import json
import uuid

from sqlalchemy import select

from payfund_app.main import app
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.infra.models import ProviderEventRecord
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
)
from payfund_app.modules.payments.presentation.deps import get_paystack_webhook_processor


URL = "/payfund/v1/payments/webhooks/paystack"
SECRET = "webhook-test-secret"


def seed_payment(session):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference="ride:42",
        money=Money(5_000),
        idempotency_key=f"ride-{uuid.uuid4()}",
        request_fingerprint="a" * 64,
        status=PaymentIntentStatus.REQUIRES_ACTION,
    )
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor="paystack",
        money=intent.money,
        attempt_number=1,
        status=AttemptStatus.REQUIRES_ACTION,
        provider_reference=f"dpi_{uuid.uuid4().hex}",
    )
    PaymentIntentRepository(session).add(intent)
    PaymentAttemptRepository(session).add(attempt)
    session.commit()
    return intent, attempt


def signed_payload(attempt, *, amount=5_000, currency="XOF"):
    raw = json.dumps(
        {
            "event": "charge.success",
            "data": {
                "reference": attempt.provider_reference,
                "status": "success",
                "amount": amount,
                "currency": currency,
                "channel": "mobile_money",
                "customer": {"email": "must-not-be-stored@example.com"},
                "authorization": {"last4": "4081"},
            },
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return raw, signature


def configure_processor():
    app.dependency_overrides[get_paystack_webhook_processor] = lambda: PaystackPaymentProcessor(
        secret_key="sk_test", webhook_secret=SECRET
    )


def test_signed_success_webhook_completes_intent_once(client, session):
    intent, attempt = seed_payment(session)
    raw, signature = signed_payload(attempt)
    configure_processor()

    first = client.post(
        URL,
        content=raw,
        headers={"Content-Type": "application/json", "X-Paystack-Signature": signature},
    )
    second = client.post(
        URL,
        content=raw,
        headers={"Content-Type": "application/json", "X-Paystack-Signature": signature},
    )

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert PaymentIntentRepository(session).get(intent.id).status == PaymentIntentStatus.SUCCEEDED
    assert PaymentAttemptRepository(session).get(attempt.id).status == AttemptStatus.SUCCEEDED


def test_invalid_webhook_signature_is_rejected(client, session):
    _, attempt = seed_payment(session)
    raw, _ = signed_payload(attempt)
    configure_processor()

    response = client.post(
        URL,
        content=raw,
        headers={"Content-Type": "application/json", "X-Paystack-Signature": "invalid"},
    )
    assert response.status_code == 401


def test_amount_mismatch_is_audited_without_completing_payment(client, session):
    intent, attempt = seed_payment(session)
    raw, signature = signed_payload(attempt, amount=5_001)
    configure_processor()

    response = client.post(
        URL,
        content=raw,
        headers={"Content-Type": "application/json", "X-Paystack-Signature": signature},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert PaymentIntentRepository(session).get(intent.id).status == PaymentIntentStatus.REQUIRES_ACTION
    event = session.scalar(select(ProviderEventRecord))
    assert event.status == "failed"
    assert event.error_message == "provider amount or currency mismatch"
    assert "customer" not in event.payload
    assert "authorization" not in event.payload
