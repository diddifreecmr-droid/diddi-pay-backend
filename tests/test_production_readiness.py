import pytest

from payfund_app.core.config import Settings
from payfund_app.core.production_readiness import production_configuration_errors


@pytest.fixture(autouse=True)
def isolate_production_settings(monkeypatch):
    monkeypatch.delenv("PAYMENT_CALLBACK_TARGETS", raising=False)
    monkeypatch.delenv("PAYMENT_CHECKOUT_RETURN_TARGETS", raising=False)


def production_settings(**overrides) -> Settings:
    values = {
        "deployment_environment": "production",
        "payment_processor_mode": "paystack",
        "paystack_environment": "live",
        "paystack_secret_key": "sk_live_not_a_real_key",
        "paystack_base_url": "https://api.paystack.co",
        "payment_service_key_fallback_enabled": False,
        "payment_callback_url_fallback_enabled": False,
        "payment_service_client_ids": "diddigo-production",
        "payment_gateway_autoconfirm": False,
        "cors_origins": "https://go.diddifree.com",
        "qr_signing_secret": "a-production-secret-managed-outside-git",
        "payment_checkout_return_targets": {
            "diddigo-production:app": "https://go.diddifree.com/payments/return"
        },
        "payment_callback_targets": {
            "diddigo-production": {
                "url": "https://go.diddifree.com/internal/webhooks/diddipay",
                "secret": "callback-secret-long-enough",
            }
        },
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_configuration_passes():
    assert production_configuration_errors(production_settings()) == []


def test_non_production_configuration_is_not_blocked():
    settings = production_settings(
        deployment_environment="staging",
        payment_processor_mode="sandbox",
        paystack_environment="test",
        paystack_secret_key="sk_test_fake",
    )
    assert production_configuration_errors(settings) == []


def test_production_rejects_test_keys_sandbox_and_legacy_fallbacks():
    errors = production_configuration_errors(
        production_settings(
            payment_processor_mode="sandbox",
            paystack_environment="test",
            paystack_secret_key="sk_test_fake",
            payment_service_key_fallback_enabled=True,
            payment_callback_url_fallback_enabled=True,
        )
    )
    assert "PAYMENT_PROCESSOR_MODE must be paystack" in errors
    assert "PAYSTACK_ENVIRONMENT must be live" in errors
    assert "PAYSTACK_SECRET_KEY must be a live secret key" in errors
    assert "PAYMENT_SERVICE_KEY_FALLBACK_ENABLED must be false" in errors
    assert "PAYMENT_CALLBACK_URL_FALLBACK_ENABLED must be false" in errors


def test_production_rejects_non_https_destinations():
    settings = production_settings(
        payment_checkout_return_targets={"diddigo-production:app": "http://localhost/return"},
        payment_callback_targets={
            "diddigo-production": {
                "url": "http://app:8000/internal/webhooks/diddipay",
                "secret": "callback-secret-long-enough",
            }
        },
    )
    errors = production_configuration_errors(settings)
    assert any("checkout return target" in error for error in errors)
    assert any("payment callback target" in error for error in errors)
