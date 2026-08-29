from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.infra.gateways import GatewayStatus, PaystackGateway
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.modules.wallet.infra.repositories import (
    AccountRepository,
    ReconciliationLogRepository,
    TransactionRepository,
    WebhookInboxRepository,
)

BASE = "/payfund/v1/wallet"


def test_paystack_gateway_initialization_uses_backend_client(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_123")
    get_settings.cache_clear()

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.com/abc",
                    "access_code": "abc",
                    "reference": "ref-123",
                },
            }

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("payfund_app.modules.wallet.infra.gateways.httpx.Client", FakeClient)

    gateway = PaystackGateway()
    operation = gateway.initier_depot(
        provider="paystack",
        phone="+2250700000000",
        email="[email protected]",
        montant=5000,
        reference="wallet-deposit-1",
    )

    assert operation.status == GatewayStatus.PENDING
    assert operation.authorization_url == "https://checkout.paystack.com/abc"
    assert operation.access_code == "abc"
    assert captured["url"].endswith("/transaction/initialize")
    assert captured["headers"]["Authorization"] == "Bearer sk_test_123"
    assert captured["json"]["reference"] == "wallet-deposit-1"


def test_paystack_webhook_confirms_deposit(client, auth, session, make_user, monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_123")
    get_settings.cache_clear()

    user_id, account_id = make_user()
    auth.as_user(user_id)
    transaction = TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=None,
        provider_reference="paystack-ref-1",
    )
    transaction.amount = 5000
    transaction.currency = "XOF"
    session.commit()

    payload = {
        "event": "charge.success",
        "data": {"reference": "paystack-ref-1", "status": "success"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"sk_test_123", body, hashlib.sha512).hexdigest()

    response = client.post(
        f"{BASE}/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": signature},
    )

    assert response.status_code == 200
    transaction = session.get(Transaction, transaction.id)
    assert transaction.status == "completed"
    assert AccountRepository(session).balance(account_id).amount == 5000
    inbox = WebhookInboxRepository(session).seen("paystack:charge.success:paystack-ref-1")
    assert inbox is not None
    assert inbox.status == "processed"
    assert ReconciliationLogRepository(session).latest_for_transaction(transaction.id)[0].reason == "charge_success"


def test_paystack_webhook_ignores_duplicate_finalized_transaction(
    client, auth, session, make_user, monkeypatch
):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_123")
    get_settings.cache_clear()

    user_id, account_id = make_user()
    auth.as_user(user_id)
    transaction = TransactionRepository(session).create(
        type_="deposit",
        status="completed",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=None,
        provider_reference="paystack-ref-final",
    )
    transaction.amount = 5000
    transaction.currency = "XOF"
    session.commit()

    payload = {
        "event": "charge.success",
        "data": {"reference": "paystack-ref-final", "status": "success"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"sk_test_123", body, hashlib.sha512).hexdigest()

    response = client.post(
        f"{BASE}/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": signature},
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "already_finalized"
    transaction = session.get(Transaction, transaction.id)
    assert transaction.status == "completed"
    inbox = WebhookInboxRepository(session).seen("paystack:charge.success:paystack-ref-final")
    assert inbox is not None
    assert inbox.status == "processed"


def test_paystack_webhook_ignores_duplicate_event_key(client, auth, session, make_user, monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_123")
    get_settings.cache_clear()

    user_id, account_id = make_user()
    auth.as_user(user_id)
    transaction = TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=None,
        provider_reference="paystack-ref-repeat",
    )
    transaction.amount = 5000
    transaction.currency = "XOF"
    session.commit()

    payload = {
        "event": "charge.success",
        "data": {"reference": "paystack-ref-repeat", "status": "success"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"sk_test_123", body, hashlib.sha512).hexdigest()

    first = client.post(
        f"{BASE}/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": signature},
    )
    second = client.post(
        f"{BASE}/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": signature},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["reason"] == "duplicate_event"
    assert WebhookInboxRepository(session).seen("paystack:charge.success:paystack-ref-repeat") is not None


def test_paystack_webhook_reports_invalid_signature(client, monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_123")
    get_settings.cache_clear()

    payload = {
        "event": "charge.success",
        "data": {"reference": "paystack-ref-1", "status": "success"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(
        f"{BASE}/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": "bad-signature"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "invalid_signature"
    assert response.json()["reason"] == "signature_mismatch"


def test_paystack_webhook_reports_unknown_reference(client, monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_123")
    get_settings.cache_clear()

    payload = {
        "event": "charge.success",
        "data": {"reference": "missing-ref", "status": "success"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"sk_test_123", body, hashlib.sha512).hexdigest()

    response = client.post(
        f"{BASE}/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": signature},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unknown_reference"
    assert response.json()["reason"] == "no_local_transaction"


def test_depot_paystack_persiste_le_lien_de_checkout(client, auth, make_user, monkeypatch):
    """Le lien de checkout ne doit plus disparaître au rejeu ni redevenir introuvable."""
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_123")
    get_settings.cache_clear()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.com/xyz",
                    "access_code": "xyz",
                    "reference": "ref-xyz",
                },
            }

    class FakeClient:
        def __init__(self, timeout):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr("payfund_app.modules.wallet.infra.gateways.httpx.Client", FakeClient)

    user_id, _ = make_user()
    auth.as_user(user_id)
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    payload = {
        "provider": "paystack",
        "amount": 5000,
        "phone": "+2250700000000",
        "email": "buyer@example.com",
    }

    premiere = client.post(f"{BASE}/deposit", json=payload, headers=headers)
    assert premiere.status_code == 202
    body = premiere.json()
    assert body["authorization_url"] == "https://checkout.paystack.com/xyz"
    assert body["access_code"] == "xyz"

    # Rejeu de la même Idempotency-Key (retry réseau côté client, par exemple) : le lien ne doit
    # plus revenir à `null`.
    seconde = client.post(f"{BASE}/deposit", json=payload, headers=headers)
    assert seconde.json()["transaction_id"] == body["transaction_id"]
    assert seconde.json()["authorization_url"] == "https://checkout.paystack.com/xyz"

    # Récupérable aussi via GET, même sans avoir gardé la réponse `202` initiale.
    detail = client.get(f"{BASE}/transactions/{body['transaction_id']}").json()
    assert detail["authorization_url"] == "https://checkout.paystack.com/xyz"
    assert detail["access_code"] == "xyz"
