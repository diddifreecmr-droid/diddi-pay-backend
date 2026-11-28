"""Ticket 051: a Moov Money payment intent is accepted and routed to PawaPay."""

import json
import uuid

import httpx
import pytest

from payfund_app.core.config import get_settings
from payfund_app.modules.payments.presentation import deps

BASE = "/payfund/v1/payment-intents"
HEADERS = {"X-Client-ID": "diddigo", "X-Service-Key": "test-service-key"}


@pytest.fixture(autouse=True)
def _pawapay_configured(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "pawapay")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()
    deps.reset_processor_registry()
    yield
    deps.reset_processor_registry()
    get_settings.cache_clear()


def _payload(network: str, key: str) -> dict:
    return {
        "business_reference": f"ride:{key}",
        "amount": 1_000,
        "currency": "XOF",
        "channel": "mobile_money",
        "network": network,
        "customer_phone": "+2250103456789",
        "callback_url": "https://go.diddifree.com/payments/return",
    }


def test_moov_intent_is_created_on_pawapay_with_moov_operator(monkeypatch, client):
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"redirectUrl": "https://paywith.pawapay.io/v2?x"})

    real_client = httpx.Client
    monkeypatch.setattr(
        "payfund_app.modules.payments.infra.pawapay_processor.httpx.Client",
        lambda base_url, timeout: real_client(
            transport=httpx.MockTransport(handler), base_url=base_url
        ),
    )
    key = f"moov-{uuid.uuid4()}"

    response = client.post(BASE, headers={**HEADERS, "Idempotency-Key": key}, json=_payload("moov", key))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["processor"] == "pawapay"
    assert body["status"] == "requires_action"
    # The Payment Page takes only the country; the Moov operator check ran before the call.
    assert sent["body"]["country"] == "CIV"


def test_unknown_network_is_still_a_validation_error(client):
    key = f"bad-network-{uuid.uuid4()}"

    response = client.post(BASE, headers={**HEADERS, "Idempotency-Key": key}, json=_payload("bitcoin", key))

    assert response.status_code == 422
