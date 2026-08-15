import uuid

from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
)


BASE = "/payfund/v1/payment-intents"
SERVICE_HEADERS = {
    "X-Client-ID": "diddigo",
    "X-Service-Key": "test-service-key",
}


def succeeded_payment(session, amount=10_000):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference=f"ride:{uuid.uuid4()}",
        money=Money(amount, "XOF"),
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


def refund(client, intent_id, amount, key):
    return client.post(
        f"{BASE}/{intent_id}/refunds",
        headers={**SERVICE_HEADERS, "Idempotency-Key": key},
        json={"amount": amount, "reason": "Service cancelled"},
    )


def test_sandbox_refund_updates_payment_intent(client, session):
    intent = succeeded_payment(session)

    response = refund(client, intent.id, 4_000, "refund-ride-1")

    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"
    session.expire_all()
    updated = PaymentIntentRepository(session).get(intent.id)
    assert updated.refunded_amount == 4_000
    assert updated.status == PaymentIntentStatus.PARTIALLY_REFUNDED


def test_refund_is_idempotent(client, session):
    intent = succeeded_payment(session)

    first = refund(client, intent.id, 4_000, "refund-ride-2")
    second = refund(client, intent.id, 4_000, "refund-ride-2")

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


def test_cumulative_refunds_cannot_exceed_capture(client, session):
    intent = succeeded_payment(session, amount=10_000)
    assert refund(client, intent.id, 7_000, "refund-part-1").status_code == 201

    response = refund(client, intent.id, 3_001, "refund-part-2")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_OPERATION_CONFLICT"


def test_refund_requires_succeeded_payment(client, session):
    intent = succeeded_payment(session)
    row = PaymentIntentRepository(session).get(intent.id)
    row.status = PaymentIntentStatus.PROCESSING
    PaymentIntentRepository(session).save(row)
    session.commit()

    response = refund(client, intent.id, 1_000, "refund-pending")

    assert response.status_code == 409


def test_openapi_documents_refund_request(client):
    schema = client.get("/payfund/v1/openapi.json").json()
    path = schema["paths"][f"{BASE}/{{intent_id}}/refunds"]["post"]
    headers = {item["name"] for item in path["parameters"]}
    assert {"X-Client-ID", "X-Service-Key", "Idempotency-Key"} <= headers
    assert "requestBody" in path
