import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from payfund_app.modules.payments.application.payouts import PayoutUseCases
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.infra.models import (
    FinancialEntryRecord,
    FinancialJournalRecord,
    PaymentOutboxRecord,
    PayoutRecord,
)
from payfund_app.modules.payments.infra.repositories import (
    PaymentOutboxRepository,
    PayoutRepository,
)
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork

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
    journals = list(
        session.scalars(
            select(FinancialJournalRecord).where(
                FinancialJournalRecord.payout_id == first.json()["id"]
            )
        )
    )
    assert len(journals) == 1
    entries = list(
        session.scalars(
            select(FinancialEntryRecord).where(
                FinancialEntryRecord.journal_id == journals[0].id
            )
        )
    )
    assert {(entry.account, entry.direction) for entry in entries} == {
        ("module_payable:diddifood", "debit"),
        ("processor_balance:sandbox", "credit"),
    }
    assert sum(entry.amount for entry in entries if entry.direction == "debit") == 4_500
    assert sum(entry.amount for entry in entries if entry.direction == "credit") == 4_500

    summary = client.get(
        f"{BASE}/{first.json()['id']}/financial-summary",
        headers={"X-Client-ID": "diddifood", "X-Service-Key": "food-service-key"},
    )
    assert summary.status_code == 200
    assert summary.json()["paid_out"] == 4_500


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
    assert f"{BASE}/{{payout_id}}/financial-summary" in schema["paths"]
    parameter_names = {
        parameter["name"] for parameter in schema["paths"][BASE]["post"]["parameters"]
    }
    assert {"X-Client-ID", "X-Service-Key", "Idempotency-Key"} <= parameter_names


def test_processing_payout_is_reconciled_and_emits_final_event(session):
    payout_id = uuid.uuid4()
    session.add(
        PayoutRecord(
            id=payout_id,
            client_id="diddifood",
            business_reference="diddifood:order:reconcile-42:restaurant-payout:v1",
            beneficiary_reference="restaurant:rest-7",
            amount=4_500,
            currency="XOF",
            status="processing",
            idempotency_key="diddisend:delivery:reconcile-42:picked_up:v1",
            request_fingerprint="a" * 64,
            processor="sandbox",
            provider_reference=f"sandbox-payout-{payout_id}",
            provider_status="network_error",
            metadata_json={"delivery_id": "reconcile-42"},
            created_at=datetime.now(UTC) - timedelta(minutes=10),
            updated_at=datetime.now(UTC) - timedelta(minutes=10),
        )
    )
    session.commit()
    processors = ProcessorRegistry()
    processors.register(SandboxPaymentProcessor())

    result = PayoutUseCases(
        PayoutRepository(session),
        PaymentOutboxRepository(session),
        processors,
        SqlAlchemyUnitOfWork(session),
    ).reconcile(minimum_age_seconds=300)

    assert result.scanned == 1
    assert result.succeeded == 1
    assert PayoutRepository(session).get(payout_id).status == "succeeded"
    event = session.scalar(
        select(PaymentOutboxRecord).where(PaymentOutboxRecord.aggregate_id == payout_id)
    )
    assert event.event_type == "payout.succeeded"
