from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.infra.models import (
    FinancialJournalRecord,
    PaymentOutboxRecord,
    PayoutRecord,
    ProviderEventRecord,
)
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.presentation.deps import (
    get_processor_registry,
    get_webhook_processor_resolver,
)

BASE = "/payfund/v1/webhooks/pawapay/payouts"


def _payout_row(payout_id: uuid.UUID, *, amount: int = 5_000) -> PayoutRecord:
    now = datetime.now(UTC)
    return PayoutRecord(
        id=payout_id,
        client_id="diddifood",
        business_reference="diddifood:order:42:restaurant-payout:v1",
        beneficiary_reference="restaurant:rest-7",
        amount=amount,
        currency="XOF",
        status="processing",
        idempotency_key=f"payout-webhook:{payout_id}",
        request_fingerprint="a" * 64,
        processor="pawapay",
        provider_reference=str(payout_id),
        provider_status="accepted",
        metadata_json={"order_reference": "order-42"},
        destination={"type": "mobile_money", "network": "mtn"},
        created_at=now - timedelta(minutes=1),
        updated_at=now,
    )


def _processor(payout_id: uuid.UUID) -> PawapayPaymentProcessor:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/v2/payouts/{payout_id}"
        return httpx.Response(
            200,
            json={
                "status": "FOUND",
                "data": {
                    "payoutId": str(payout_id),
                    "status": "COMPLETED",
                    "providerTransactionId": "provider-tx-1",
                },
            },
        )

    return PawapayPaymentProcessor(
        api_token="test-token",
        base_url="https://api.pawapay.test",
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.pawapay.test",
        ),
    )


def _callback(payout_id: uuid.UUID, *, amount: str = "5000") -> dict:
    return {
        "payoutId": str(payout_id),
        "status": "COMPLETED",
        "amount": amount,
        "currency": "XOF",
        "country": "CIV",
        "providerTransactionId": "provider-tx-1",
    }


def test_pawapay_payout_webhook_updates_ledger_and_is_idempotent(client, session):
    payout_id = uuid.uuid4()
    session.add(_payout_row(payout_id))
    session.commit()
    app = client.app
    app.dependency_overrides[get_webhook_processor_resolver] = (
        lambda: lambda name, kind: _processor(payout_id)
    )
    app.dependency_overrides[get_processor_registry] = ProcessorRegistry

    first = client.post(BASE, json=_callback(payout_id))
    replay = client.post(BASE, json=_callback(payout_id))

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"
    assert session.get(PayoutRecord, payout_id).status == "succeeded"
    assert session.scalar(select(FinancialJournalRecord).where(FinancialJournalRecord.payout_id == payout_id))
    assert session.scalar(select(PaymentOutboxRecord).where(PaymentOutboxRecord.aggregate_id == payout_id))
    event = session.scalar(select(ProviderEventRecord))
    assert event.status == "processed"
    assert event.payout_id == payout_id


def test_pawapay_payout_webhook_rejects_amount_mismatch_without_posting(client, session):
    payout_id = uuid.uuid4()
    session.add(_payout_row(payout_id))
    session.commit()
    app = client.app
    app.dependency_overrides[get_webhook_processor_resolver] = (
        lambda: lambda name, kind: _processor(payout_id)
    )
    app.dependency_overrides[get_processor_registry] = ProcessorRegistry

    response = client.post(BASE, json=_callback(payout_id, amount="5001"))

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert session.get(PayoutRecord, payout_id).status == "processing"
    assert session.scalar(select(FinancialJournalRecord).where(FinancialJournalRecord.payout_id == payout_id)) is None
    event = session.scalar(select(ProviderEventRecord))
    assert event.status == "failed"
    assert event.error_message == "provider amount mismatch"
