import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from payfund_app.core.config import get_settings
from payfund_app.modules.fund.domain.entities import CampaignStatus
from payfund_app.modules.fund.infra.models import Campaign, Investment


BASE = "/payfund/v1/fund"
CALLBACK_SECRET = "fund-callback-secret-for-tests"


def create_active_campaign(client, session, auth, owner_id):
    auth.as_user(owner_id)
    response = client.post(
        f"{BASE}/campaigns",
        json={"title": "Campagne Paystack", "goal_amount": 100_000},
    )
    campaign_id = uuid.UUID(response.json()["campaign_id"])
    session.get(Campaign, campaign_id).status = str(CampaignStatus.ACTIVE)
    session.commit()
    return campaign_id


def start_payment(client, auth, investor_id, campaign_id, key="investment-payment-1"):
    auth.as_user(investor_id)
    return client.post(
        f"{BASE}/campaigns/{campaign_id}/invest/payment",
        headers={"Idempotency-Key": key},
        json={
            "amount": 10_000,
            "channel": "mobile_money",
            "network": "orange",
            "customer_email": "investor@example.com",
        },
    )


def signed_event(payment, event_id, **data_overrides):
    data = {
        "event_id": "charge.success:test:success",
        "payment_intent_id": payment["payment_intent_id"],
        "business_reference": payment["business_reference"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "status": "succeeded",
    }
    data.update(data_overrides)
    payload = {
        "id": str(event_id),
        "type": "payment.succeeded",
        "occurred_at": datetime.now(UTC).isoformat(),
        "data": data,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(
        CALLBACK_SECRET.encode(), raw, hashlib.sha256
    ).hexdigest()
    return raw, signature


def test_external_investment_creates_provider_neutral_payment_order(
    client, auth, session, make_user
):
    owner, _ = make_user()
    investor, _ = make_user()
    campaign_id = create_active_campaign(client, session, auth, owner)

    response = start_payment(client, auth, investor, campaign_id)

    assert response.status_code == 201
    body = response.json()
    assert body["operation_type"] == "investment"
    assert body["status"] == "requires_action"
    assert body["next_action"]["type"] == "redirect"
    assert body["payment_intent_id"]


def test_external_investment_creation_is_idempotent(client, auth, session, make_user):
    owner, _ = make_user()
    investor, _ = make_user()
    campaign_id = create_active_campaign(client, session, auth, owner)

    first = start_payment(client, auth, investor, campaign_id)
    second = start_payment(client, auth, investor, campaign_id)

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["payment_intent_id"] == first.json()["payment_intent_id"]
    assert second.json()["next_action"] == first.json()["next_action"]


def test_signed_success_event_finalizes_investment_once(
    client, auth, session, make_user, monkeypatch
):
    monkeypatch.setenv("DIDDIFUND_DIDDIPAY_CALLBACK_SECRET", CALLBACK_SECRET)
    get_settings.cache_clear()
    owner, _ = make_user()
    investor, _ = make_user()
    campaign_id = create_active_campaign(client, session, auth, owner)
    payment = start_payment(client, auth, investor, campaign_id).json()
    event_id = uuid.uuid4()
    raw, signature = signed_event(payment, event_id)
    headers = {
        "Content-Type": "application/json",
        "X-DiddiPay-Event-ID": str(event_id),
        "X-DiddiPay-Signature": signature,
    }

    first = client.post(
        f"{BASE}/payments/webhooks/diddipay", content=raw, headers=headers
    )
    second = client.post(
        f"{BASE}/payments/webhooks/diddipay", content=raw, headers=headers
    )

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert session.scalar(select(func.count()).select_from(Investment)) == 1
    session.expire_all()
    assert int(session.get(Campaign, campaign_id).raised_amount) == 10_000


def test_diddipay_event_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setenv("DIDDIFUND_DIDDIPAY_CALLBACK_SECRET", CALLBACK_SECRET)
    get_settings.cache_clear()
    event_id = uuid.uuid4()
    payload = {
        "id": str(event_id),
        "type": "payment.succeeded",
        "occurred_at": datetime.now(UTC).isoformat(),
        "data": {},
    }

    response = client.post(
        f"{BASE}/payments/webhooks/diddipay",
        json=payload,
        headers={
            "X-DiddiPay-Event-ID": str(event_id),
            "X-DiddiPay-Signature": "invalid",
        },
    )

    assert response.status_code == 401


def test_diddipay_event_rejects_amount_mismatch(
    client, auth, session, make_user, monkeypatch
):
    monkeypatch.setenv("DIDDIFUND_DIDDIPAY_CALLBACK_SECRET", CALLBACK_SECRET)
    get_settings.cache_clear()
    owner, _ = make_user()
    investor, _ = make_user()
    campaign_id = create_active_campaign(client, session, auth, owner)
    payment = start_payment(client, auth, investor, campaign_id).json()
    event_id = uuid.uuid4()
    raw, signature = signed_event(payment, event_id, amount=payment["amount"] + 1)

    response = client.post(
        f"{BASE}/payments/webhooks/diddipay",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-DiddiPay-Event-ID": str(event_id),
            "X-DiddiPay-Signature": signature,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_AMOUNT_MISMATCH"
    assert session.scalar(select(func.count()).select_from(Investment)) == 0


def test_openapi_documents_fund_payment_requests(client):
    paths = client.get("/payfund/v1/openapi.json").json()["paths"]
    create_path = f"{BASE}/campaigns/{{campaign_id}}/invest/payment"
    callback_path = f"{BASE}/payments/webhooks/diddipay"

    assert create_path in paths
    assert f"{BASE}/payment-orders/{{order_id}}" in paths
    assert callback_path in paths
    create_headers = {item["name"] for item in paths[create_path]["post"]["parameters"]}
    callback_headers = {
        item["name"] for item in paths[callback_path]["post"]["parameters"]
    }
    assert "Idempotency-Key" in create_headers
    assert {"X-DiddiPay-Signature", "X-DiddiPay-Event-ID"} <= callback_headers
    assert "requestBody" in paths[callback_path]["post"]
