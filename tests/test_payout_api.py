from sqlalchemy import select

from payfund_app.modules.payments.infra.models import PaymentOutboxRecord

BASE = "/payfund/v1/payouts"
HEADERS = {
    "X-Client-ID": "diddifood",
    "X-Service-Key": "food-service-key",
    "Idempotency-Key": "diddisend:delivery:delivery-42:picked_up:v1",
}


def payload(**overrides):
    value = {
        "business_reference": "diddifood:order:order-42:restaurant-payout:v1",
        "beneficiary_reference": "restaurant:rest-7",
        "amount": 4_500,
        "currency": "XOF",
        "metadata": {
            "order_reference": "order-42",
            "delivery_id": "delivery-42",
            "breakdown_version": "v1",
            "breakdown_hash": "sha256:abc123",
        },
    }
    value.update(overrides)
    return value


def test_create_restaurant_payout_is_idempotent_and_emits_callback(client, session):
    first = client.post(BASE, headers=HEADERS, json=payload())
    replay = client.post(BASE, headers=HEADERS, json=payload())

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["status"] == "succeeded"
    assert first.json()["client_id"] == "diddifood"
    assert first.json()["beneficiary_reference"] == "restaurant:rest-7"
    events = list(session.scalars(select(PaymentOutboxRecord)))
    assert len(events) == 1
    assert events[0].event_type == "payout.succeeded"
    assert events[0].client_id == "diddifood"
    assert events[0].payload["metadata"]["delivery_id"] == "delivery-42"


def test_payout_idempotency_rejects_changed_financial_data(client):
    assert client.post(BASE, headers=HEADERS, json=payload()).status_code == 201
    changed = client.post(BASE, headers=HEADERS, json=payload(amount=4_501))
    changed_beneficiary = client.post(
        BASE, headers=HEADERS, json=payload(beneficiary_reference="restaurant:rest-8")
    )

    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert changed_beneficiary.status_code == 409


def test_payout_is_visible_only_to_owning_module(client):
    created = client.post(BASE, headers=HEADERS, json=payload()).json()
    other_module = client.get(
        f"{BASE}/{created['id']}",
        headers={"X-Client-ID": "diddigo", "X-Service-Key": "test-service-key"},
    )
    lookup = client.get(
        BASE,
        params={"business_reference": payload()["business_reference"]},
        headers={"X-Client-ID": "diddifood", "X-Service-Key": "food-service-key"},
    )

    assert other_module.status_code == 404
    assert other_module.json()["error"]["code"] == "PAYOUT_NOT_FOUND"
    assert lookup.status_code == 200
    assert lookup.json()["id"] == created["id"]


def test_payout_requires_idempotency_and_documents_contract(client):
    missing_key = client.post(
        BASE,
        headers={"X-Client-ID": "diddifood", "X-Service-Key": "food-service-key"},
        json=payload(),
    )
    schema = client.get("/payfund/v1/openapi.json").json()

    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert BASE in schema["paths"]
    assert f"{BASE}/{{payout_id}}" in schema["paths"]
    parameter_names = {
        parameter["name"] for parameter in schema["paths"][BASE]["post"]["parameters"]
    }
    assert {"X-Client-ID", "X-Service-Key", "Idempotency-Key"} <= parameter_names
