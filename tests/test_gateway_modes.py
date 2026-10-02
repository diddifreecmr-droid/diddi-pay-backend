from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.errors import (
    DepositMethodNotSupported,
    WithdrawalNotSupported,
)
from payfund_app.modules.wallet.infra.gateways import (
    GatewayStatus,
    OrangeMoneySandboxGateway,
    PaystackGateway,
    StubGateway,
    WaveSandboxGateway,
    gateway_for_provider,
    get_gateway,
)
from payfund_app.modules.wallet.infra.pawapay_gateway import PawapayGateway


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


def test_stub_and_sandbox_gateways_implement_verifier_retrait(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "stub")
    get_settings.cache_clear()
    stub = get_gateway()
    operation = stub.verifier_retrait("ref-stub")
    assert operation.status in {GatewayStatus.PENDING, GatewayStatus.COMPLETED}

    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "sandbox_orange_money")
    get_settings.cache_clear()
    orange = get_gateway()
    assert orange.verifier_retrait("ref-orange").status == GatewayStatus.PENDING

    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "sandbox_wave")
    get_settings.cache_clear()
    wave = get_gateway()
    assert wave.verifier_retrait("ref-wave").status == GatewayStatus.PENDING


def test_paystack_verifier_retrait_not_implemented(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_not_a_real_secret")
    get_settings.cache_clear()
    gateway = PaystackGateway()

    with pytest.raises(NotImplementedError):
        gateway.verifier_retrait("ref-1")


def test_pawapay_gateway_is_selectable(monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "pawapay")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()

    gateway = get_gateway()

    assert isinstance(gateway, PawapayGateway)
    assert gateway.supports_withdrawal("mtn_momo")
    assert not gateway.supports_withdrawal("wave")


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


def test_gateway_for_provider_routes_paystack_regardless_of_fallback(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "")
    get_settings.cache_clear()
    fallback = SimpleNamespace(mode="pawapay")

    assert isinstance(gateway_for_provider("mtn_momo", fallback=fallback), SimpleNamespace)
    with pytest.raises(RuntimeError):
        # PaystackGateway() raises if PAYSTACK_SECRET_KEY is unset -- proves the dedicated
        # mode was actually resolved (not just the fallback returned unchanged).
        gateway_for_provider("paystack", fallback=fallback)


def test_depot_wave_sous_pawapay_renvoie_une_erreur_claire_pas_un_502(monkeypatch):
    """Régression : un provider que la passerelle active ne gère pas (ex. wave sous le mode
    pawapay) doit lever une erreur métier 422 lisible, pas un 502 générique qui masque la
    vraie cause (NotImplementedError englouti par le `except Exception` de deposer())."""
    monkeypatch.setenv("PAYMENT_GATEWAY_MODE", "pawapay")
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()

    use_cases = WalletUseCases(Mock())
    monkeypatch.setattr(
        use_cases, "compte_de", lambda _: SimpleNamespace(id=uuid.uuid4(), currency="XOF")
    )
    use_cases.transactions.get_by_idempotency_key = Mock(return_value=None)
    use_cases.transactions.create = Mock(return_value=SimpleNamespace(id=uuid.uuid4()))
    use_cases._compte_suspense = Mock(return_value=uuid.uuid4())
    use_cases.session.flush = Mock()

    with pytest.raises(DepositMethodNotSupported) as error:
        use_cases.deposer(
            user_id=uuid.uuid4(),
            provider="wave",
            amount=5_000,
            phone="+2250700000000",
            email=None,
            idempotency_key="deposit-wave-under-pawapay",
        )

    assert error.value.code == "DEPOSIT_METHOD_NOT_SUPPORTED"
    assert error.value.status_code == 422
