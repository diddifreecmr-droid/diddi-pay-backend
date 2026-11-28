"""Processor plugin list (ticket 110, step 2): one declaration per processor."""

import pytest

from payfund_app.core.config import get_settings
from payfund_app.core.errors import ServiceUnavailable
from payfund_app.modules.payments.application.ports import WebhookKind
from payfund_app.modules.payments.infra.processor_plugins import (
    PLUGINS,
    ProcessorNotConfigured,
    build_webhook_processor,
    plugin_named,
)
from payfund_app.modules.payments.presentation import deps


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    deps.reset_processor_registry()
    yield
    deps.reset_processor_registry()
    get_settings.cache_clear()


def test_plugin_names_match_adapter_names():
    settings = get_settings()
    for plugin in PLUGINS:
        if plugin.is_configured(settings):
            assert plugin.build(settings).name == plugin.name
    assert {p.name for p in PLUGINS} == {"sandbox", "paystack", "pawapay", "cinetpay", "jeko"}


def test_only_plugins_with_payment_callbacks_build_webhook_adapters():
    for plugin in PLUGINS:
        assert (plugin.build_webhook is not None) == (plugin.name in {"paystack", "pawapay", "cinetpay"})


@pytest.mark.parametrize("mode,env", [("pawapay", "PAWAPAY_API_TOKEN"), ("jeko", "JEKO_API_KEY")])
def test_selected_processor_without_settings_is_a_clear_503(monkeypatch, mode, env):
    # Before ticket 110 only CinetPay and Paystack failed fast; PawaPay/Jeko silently fell
    # through to "processor not registered" on the first payment.
    monkeypatch.setenv("PAYMENT_PROCESSOR_MODE", mode)
    for name in ("PAWAPAY_API_TOKEN", "JEKO_API_KEY", "JEKO_API_KEY_ID", "JEKO_STORE_ID"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()

    with pytest.raises(ProcessorNotConfigured) as error:
        deps.get_processor_registry()

    assert isinstance(error.value, ServiceUnavailable)
    assert error.value.code == "PROCESSOR_NOT_CONFIGURED"
    assert error.value.details["processor"] == mode
    assert env in error.value.details["missing"]


def test_pawapay_webhook_adapter_uses_the_url_of_each_callback_kind(monkeypatch):
    base = "https://pay.example.test/payfund/v1/webhooks/pawapay"
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    monkeypatch.setenv("PAWAPAY_CALLBACK_URL", f"{base}/payouts")
    monkeypatch.setenv("PAWAPAY_DEPOSIT_CALLBACK_URL", f"{base}/deposits")
    monkeypatch.setenv("PAWAPAY_REFUND_CALLBACK_URL", "")
    monkeypatch.setenv("PAWAPAY_PAYOUT_CALLBACK_URL", "")
    get_settings.cache_clear()
    settings = get_settings()

    assert build_webhook_processor("pawapay", WebhookKind.PAYMENT, settings).callback_url == f"{base}/deposits"
    # Empty specific URL falls back to PAWAPAY_CALLBACK_URL, as before.
    assert build_webhook_processor("pawapay", WebhookKind.REFUND, settings).callback_url == f"{base}/payouts"
    assert build_webhook_processor("pawapay", WebhookKind.PAYOUT, settings).callback_url == f"{base}/payouts"


def test_webhook_adapter_for_unconfigured_or_unknown_processor_is_503(monkeypatch):
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "")
    get_settings.cache_clear()

    for name in ("pawapay", "jeko", "unknown"):
        with pytest.raises(ServiceUnavailable) as error:
            build_webhook_processor(name, WebhookKind.PAYMENT, get_settings())
        assert error.value.code == "PROCESSOR_NOT_CONFIGURED"
        assert error.value.details == {"provider": name}


def test_plugin_lookup_is_case_insensitive():
    assert plugin_named(" PawaPay ").name == "pawapay"
    assert plugin_named("nope") is None
