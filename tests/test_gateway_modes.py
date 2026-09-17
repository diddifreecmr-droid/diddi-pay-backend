from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.errors import WithdrawalNotSupported
from payfund_app.modules.wallet.infra.gateways import (
    GatewayStatus,
    OrangeMoneySandboxGateway,
    PaystackGateway,
    StubGateway,
    WaveSandboxGateway,
    get_gateway,
)


def test_stub_gateway_is_default(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "stub")
    get_settings.cache_clear()

    gateway = get_gateway()

    assert isinstance(gateway, StubGateway)
    assert gateway.supports_withdrawal("orange_money")


def test_sandbox_orange_money_gateway_is_selectable(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "sandbox_orange_money")
    get_settings.cache_clear()

    gateway = get_gateway()

    assert isinstance(gateway, OrangeMoneySandboxGateway)
    assert gateway.supports_withdrawal("orange_money")
    assert not gateway.supports_withdrawal("wave")
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
    assert gateway.supports_withdrawal("wave")
    assert not gateway.supports_withdrawal("orange_money")
    operation = gateway.initier_depot(
        provider="wave",
        phone="+2250700000000",
        montant=5000,
        reference="txn-wave-1",
    )
    assert operation.status == GatewayStatus.PENDING
    assert operation.provider_reference.startswith("wave-sandbox-deposit-")

    retrait = gateway.initier_retrait(
        provider="wave",
        phone="+2250700000000",
        montant=2000,
        reference="txn-wave-2",
    )
    assert retrait.status == GatewayStatus.PENDING
    assert retrait.provider_reference.startswith("wave-sandbox-withdraw-")


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


def test_paystack_withdrawal_is_rejected_before_ledger_write(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    get_settings.cache_clear()
    gateway = PaystackGateway()
    assert not gateway.supports_withdrawal("paystack")

    use_cases = WalletUseCases(Mock(), gateway=gateway)
    monkeypatch.setattr(
        use_cases, "compte_de", lambda _: SimpleNamespace(id=uuid.uuid4(), currency="XOF")
    )
    monkeypatch.setattr(use_cases, "_verify_pin", lambda *_: None)
    use_cases.transactions.get_by_idempotency_key = Mock(return_value=None)
    use_cases.ledger.transfer = Mock()

    with pytest.raises(WithdrawalNotSupported) as error:
        use_cases.retirer(
            user_id=uuid.uuid4(),
            provider="paystack",
            amount=5_000,
            phone="+2250700000000",
            pin="1234",
            idempotency_key="withdraw-unsupported",
        )

    assert error.value.code == "WITHDRAWAL_NOT_SUPPORTED"
    use_cases.ledger.transfer.assert_not_called()
