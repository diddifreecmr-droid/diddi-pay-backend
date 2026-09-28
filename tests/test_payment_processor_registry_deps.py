"""SCRUM-511 regression: registry must not be single-mode-exclusive.

Reproduces the reported symptom directly: an attempt stamped with one processor name must
still resolve via ProcessorRegistry.get() even when PAYMENT_PROCESSOR_MODE now names a
different processor — this is exactly what reconciliation does with attempt.processor.
"""

from __future__ import annotations

import pytest

from payfund_app.core.config import get_settings
from payfund_app.modules.payments.presentation import deps


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
