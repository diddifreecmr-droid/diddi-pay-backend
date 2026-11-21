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
from payfund_app.core.errors import ServiceUnavailable
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


def test_cinetpay_mode_without_credentials_fails_with_actionable_error(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "cinetpay")
    monkeypatch.setenv("CINETPAY_API_KEY", "")
    monkeypatch.setenv("CINETPAY_API_PASSWORD", "")
    get_settings.cache_clear()

    with pytest.raises(ServiceUnavailable) as error:
        deps.get_processor_registry()

    assert error.value.code == "PROCESSOR_NOT_CONFIGURED"
    assert error.value.details == {
        "processor": "cinetpay",
        "missing": ["CINETPAY_API_KEY", "CINETPAY_API_PASSWORD"],
    }


def test_cinetpay_configuration_failure_does_not_cache_empty_registry(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "cinetpay")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "")
    monkeypatch.setenv("CINETPAY_API_KEY", "")
    monkeypatch.setenv("CINETPAY_API_PASSWORD", "")
    get_settings.cache_clear()

    with pytest.raises(ServiceUnavailable):
        deps.get_processor_registry()

    monkeypatch.setenv("CINETPAY_API_KEY", "cinetpay-test-key")
    monkeypatch.setenv("CINETPAY_API_PASSWORD", "cinetpay-test-password")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    assert set(registry.names()) == {"cinetpay", "sandbox"}


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


def test_jeko_registered_only_when_all_three_credentials_present(monkeypatch):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "sandbox")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "")
    monkeypatch.setenv("JEKO_API_KEY", "jeko_not-real")
    monkeypatch.setenv("JEKO_API_KEY_ID", "id-not-real")
    monkeypatch.setenv("JEKO_STORE_ID", "store-not-real")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    assert set(registry.names()) == {"sandbox", "jeko"}


def test_jeko_not_registered_when_store_id_missing(monkeypatch):
    # A key + key_id without a store_id can't do anything (every call needs the store), so
    # the registration is gated on all three -- not just the credential pair.
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "sandbox")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "")
    monkeypatch.setenv("JEKO_API_KEY", "jeko_not-real")
    monkeypatch.setenv("JEKO_API_KEY_ID", "id-not-real")
    monkeypatch.setenv("JEKO_STORE_ID", "")
    get_settings.cache_clear()

    registry = deps.get_processor_registry()

    assert registry.names() == ("sandbox",)


def _processor_for(session, intent_id) -> str:
    return session.scalar(
        select(PaymentAttemptRecord.processor).where(
            PaymentAttemptRecord.payment_intent_id == intent_id
        )
    )


def test_payment_processor_mode_is_strict_and_explicit_processor_overrides(
    monkeypatch, client, session
):
    """The configured processor stays authoritative; a request may explicitly override it."""
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()
    deps.reset_processor_registry()

    def pawapay_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"redirectUrl": "https://paywith.pawapay.io/v2?x"})

    def paystack_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.test/mtn",
                    "access_code": "abc",
                    "reference": "dpi_reference",
                },
            },
        )

    _mock_outbound(
        monkeypatch, paystack_handler=paystack_handler, pawapay_handler=pawapay_handler
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
            "customer_email": "payer@example.test",
            "callback_url": "https://go.diddifree.com/payments/return",
        },
    )

    assert response.status_code == 201
    assert _processor_for(session, response.json()["id"]) == "paystack"

    override_key = f"dispatch-mtn-pawapay-{uuid.uuid4()}"
    override_response = client.post(
        BASE,
        headers={**HEADERS, "Idempotency-Key": override_key},
        json={
            "business_reference": f"ride:{override_key}",
            "amount": 5_000,
            "currency": "XOF",
            "channel": "mobile_money",
            "network": "mtn",
            "processor": "pawapay",
            "customer_phone": "+2250503456789",
            "callback_url": "https://go.diddifree.com/payments/return",
        },
    )

    assert override_response.status_code == 201
    assert _processor_for(session, override_response.json()["id"]) == "pawapay"


def test_explicit_processor_network_mismatch_is_not_rerouted(
    monkeypatch, client, session
):
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()
    deps.reset_processor_registry()

    def unreached(request: httpx.Request) -> httpx.Response:
        raise AssertionError("an incompatible processor must fail before an outbound call")

    _mock_outbound(monkeypatch, paystack_handler=unreached, pawapay_handler=unreached)
    key = f"dispatch-wave-pawapay-{uuid.uuid4()}"
    response = client.post(
        BASE,
        headers={**HEADERS, "Idempotency-Key": key},
        json={
            "business_reference": f"remittance:{key}",
            "amount": 835,
            "currency": "XOF",
            "channel": "mobile_money",
            "network": "wave",
            "processor": "pawapay",
            "customer_phone": "+2250503456789",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PAYMENT_METHOD_UNAVAILABLE"


def test_wave_routes_to_paystack_not_sandbox(monkeypatch, client, session):
    """A network must not override the configured PaymentIntent processor."""
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", "paystack")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()
    deps.reset_processor_registry()

    def unreached_pawapay(request: httpx.Request) -> httpx.Response:
        raise AssertionError("wave must not reach pawapay -- it doesn't support wave at all")

    def paystack_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.test/wave",
                    "access_code": "abc",
                    "reference": "dpi_reference",
                },
            },
        )

    _mock_outbound(
        monkeypatch, paystack_handler=paystack_handler, pawapay_handler=unreached_pawapay
    )

    wave_key = f"dispatch-wave-{uuid.uuid4()}"
    response = client.post(
        BASE,
        headers={**HEADERS, "Idempotency-Key": wave_key},
        json={
            "business_reference": f"remittance:{wave_key}",
            "amount": 835,
            "currency": "XOF",
            "channel": "mobile_money",
            "network": "wave",
            "customer_email": "payer@example.test",
        },
    )

    assert response.status_code == 201
    assert _processor_for(session, response.json()["id"]) == "paystack"


def test_mobile_money_payout_routes_to_pawapay(monkeypatch, client):
    """A payout can explicitly override the configured default processor."""
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()
    deps.reset_processor_registry()

    def pawapay_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"payoutId": "pay-1", "status": "ACCEPTED"})

    def unreached_paystack(request: httpx.Request) -> httpx.Response:
        raise AssertionError("mobile money payout must not reach Paystack")

    _mock_outbound(
        monkeypatch, paystack_handler=unreached_paystack, pawapay_handler=pawapay_handler
    )

    payout_key = f"payout-dispatch-{uuid.uuid4()}"
    response = client.post(
        "/payfund/v1/payouts",
        headers={
            "X-Client-ID": "diddifood",
            "X-Service-Key": "food-service-key",
            "Idempotency-Key": payout_key,
        },
        json={
            "business_reference": f"diddifood:order:{payout_key}:restaurant-payout:v1",
            "processor": "pawapay",
            "beneficiary_reference": "restaurant:rest-7",
            "beneficiary_destination": {
                "type": "mobile_money",
                "phone": "+2250503456789",
                "network": "mtn",
            },
            "amount": 4_500,
            "currency": "XOF",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["processor"] == "pawapay"
    assert body["status"] == "processing"
    assert body["beneficiary_destination"]["phone"] == "+2250503456789"
