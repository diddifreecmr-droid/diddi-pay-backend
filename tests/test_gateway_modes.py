from __future__ import annotations

from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.infra.gateways import (
    GatewayStatus,
    OrangeMoneySandboxGateway,
    StubGateway,
    WaveSandboxGateway,
    get_gateway,
)


def test_stub_gateway_is_default(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "stub")
    get_settings.cache_clear()

    gateway = get_gateway()

    assert isinstance(gateway, StubGateway)


def test_sandbox_orange_money_gateway_is_selectable(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "sandbox_orange_money")
    get_settings.cache_clear()

    gateway = get_gateway()

    assert isinstance(gateway, OrangeMoneySandboxGateway)
    operation = gateway.initier_depot(
        provider="orange_money",
        phone="+2250700000000",
        montant=5000,
        reference="txn-1",
    )
    assert operation.status == GatewayStatus.PENDING
    assert operation.provider_reference.startswith("orange-money-sandbox-deposit-")


def test_sandbox_wave_gateway_is_selectable(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "sandbox_wave")
    get_settings.cache_clear()

    gateway = get_gateway()

    assert isinstance(gateway, WaveSandboxGateway)
    operation = gateway.initier_depot(
        provider="wave",
        phone="+2250700000000",
        montant=5000,
        reference="txn-wave-1",
    )
    assert operation.status == GatewayStatus.PENDING
    assert operation.provider_reference.startswith("wave-sandbox-deposit-")


def test_sandbox_orange_money_rejects_other_providers(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "sandbox_orange_money")
    get_settings.cache_clear()

    gateway = get_gateway()

    try:
        gateway.initier_depot(
            provider="mtn_momo",
            phone="+2250700000000",
            montant=5000,
            reference="txn-2",
        )
    except NotImplementedError as exc:
        assert "mtn_momo" in str(exc)
    else:
        raise AssertionError("Expected NotImplementedError")
