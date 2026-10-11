"""SCRUM-511 regression: registry must not be single-mode-exclusive.

Reproduces the reported symptom directly: an attempt stamped with one processor name must
still resolve via ProcessorRegistry.get() even when PAYMENT_PROCESSOR_MODE now names a
different processor — this is exactly what reconciliation does with attempt.processor.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from sqlalchemy import select

from payfund_app.core.config import get_settings
from payfund_app.modules.payments.infra.models import PaymentAttemptRecord
from payfund_app.modules.payments.presentation import deps

BASE = "/payfund/v1/payment-intents"
HEADERS = {
    "X-Client-ID": "diddigo",
    "X-Service-Key": "test-service-key",
}


def _mock_outbound(monkeypatch, *, paystack_handler, pawapay_handler):
    monkeypatch.setattr(
        "payfund_app.modules.payments.infra.paystack_processor.httpx.Client",
        lambda base_url, timeout: httpx.Client(
            transport=httpx.MockTransport(paystack_handler), base_url=base_url
        ),
    )
    monkeypatch.setattr(
        "payfund_app.modules.payments.infra.pawapay_processor.httpx.Client",
        lambda base_url, timeout: httpx.Client(
            transport=httpx.MockTransport(pawapay_handler), base_url=base_url
        ),
    )


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch):
    deps.reset_processor_registry()
    yield
    deps.reset_processor_registry()
    get_settings.cache_clear()


def test_sandbox_is_always_registered_regardless_of_mode(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    # This is the exact SCRUM-511 failure mode: reconciliation looks up an attempt's own
    # processor by name. It must succeed even though the *current* mode is "paystack".
    assert registry.get("sandbox") is not None
    assert "sandbox" in registry.names()


def test_paystack_registered_only_when_key_configured(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "sandbox")
    # setenv("") not delenv: Settings reads .env directly (env_file=".env"), so merely
    # deleting from os.environ falls through to whatever real key is in that file locally.
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    assert registry.names() == ("sandbox",)


def test_both_registered_when_paystack_key_present_even_under_sandbox_mode(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "sandbox")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    assert set(registry.names()) == {"paystack", "sandbox"}


def test_paystack_mode_without_key_fails_fast(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="PAYSTACK_SECRET_KEY"):
        deps.get_processor_registry()


def test_pawapay_registered_only_when_token_configured(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "sandbox")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    assert registry.names() == ("sandbox",)


def test_all_three_processors_registered_simultaneously(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    assert set(registry.names()) == {"paystack", "pawapay", "sandbox"}


def _processor_for(session, intent_id) -> str:
    return session.scalar(
        select(PaymentAttemptRecord.processor).where(
            PaymentAttemptRecord.payment_intent_id == intent_id
        )
    )


def test_mobile_money_mtn_routes_to_pawapay_despite_paystack_default_mode(
    monkeypatch, client, session
):
    """Regression for the dispatch bug: PAYMENT_PROCESSOR_MODE=paystack must not force
    Paystack onto a network pawapay explicitly declares (mtn/orange/moov) -- the dedicated
    processor for that network wins. card still goes to Paystack, unaffected."""
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()
    deps.reset_processor_registry()

    def pawapay_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"depositId": "dep-1", "status": "ACCEPTED"}
        )

    def unreached_paystack(request: httpx.Request) -> httpx.Response:
        raise AssertionError("mtn_momo must not reach Paystack")

    _mock_outbound(
        monkeypatch, paystack_handler=unreached_paystack, pawapay_handler=pawapay_handler
    )

    mobile_money_key = f"dispatch-mtn-{uuid.uuid4()}"
    response = client.post(
        BASE,
        headers={**HEADERS, "Idempotency-Key": mobile_money_key},
        json={
            "business_reference": f"ride:{mobile_money_key}",
            "amount": 5_000,
            "currency": "XOF",
            "channel": "mobile_money",
            "network": "mtn",
            "customer_phone": "+2250503456789",
        },
    )

    assert response.status_code == 201
    assert _processor_for(session, response.json()["id"]) == "pawapay"

    deps.reset_processor_registry()

    def unreached_pawapay(request: httpx.Request) -> httpx.Response:
        raise AssertionError("card must not reach pawapay")

    def paystack_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.test/abc",
                    "access_code": "abc",
                    "reference": "dpi_reference",
                },
            },
        )

    _mock_outbound(
        monkeypatch, paystack_handler=paystack_handler, pawapay_handler=unreached_pawapay
    )

    card_key = f"dispatch-card-{uuid.uuid4()}"
    card_response = client.post(
        BASE,
        headers={**HEADERS, "Idempotency-Key": card_key},
        json={
            "business_reference": f"ride:{card_key}",
            "amount": 5_000,
            "currency": "XOF",
            "channel": "card",
            "customer_email": "payer@example.test",
        },
    )

    assert card_response.status_code == 201
    assert _processor_for(session, card_response.json()["id"]) == "paystack"
