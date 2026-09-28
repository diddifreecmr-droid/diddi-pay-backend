from __future__ import annotations

import json

import httpx
import pytest

from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.infra.gateways import GatewayStatus
from payfund_app.modules.wallet.infra.pawapay_gateway import PawapayGateway


@pytest.fixture(autouse=True)
def _pawapay_token(monkeypatch):
    monkeypatch.setenv("PAWAPAY_API_TOKEN", "sandbox-token-not-real")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


_RealClient = httpx.Client


def _mock_client_factory(monkeypatch, handler):
    def _make_client(*, timeout=20.0):
        return _RealClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(
        "payfund_app.modules.wallet.infra.pawapay_gateway.httpx.Client", _make_client
    )


def test_missing_token_raises():
    import payfund_app.core.config as config_module

    config_module.get_settings.cache_clear()
    import os

    previous = os.environ.pop("PAWAPAY_API_TOKEN", None)
    try:
        config_module.get_settings.cache_clear()
        with pytest.raises(RuntimeError):
            PawapayGateway()
    finally:
        if previous is not None:
            os.environ["PAWAPAY_API_TOKEN"] = previous
        config_module.get_settings.cache_clear()


def test_supports_withdrawal_only_for_mapped_providers():
    gateway = PawapayGateway()
    assert gateway.supports_withdrawal("mtn_momo")
    assert gateway.supports_withdrawal("orange_money")
    assert gateway.supports_withdrawal("moov")
    assert not gateway.supports_withdrawal("wave")
    assert not gateway.supports_withdrawal("card_gateway")
    assert not gateway.supports_withdrawal("paystack")


def test_unknown_country_prefix_raises_value_error():
    gateway = PawapayGateway()
    with pytest.raises(ValueError, match="Indicatif pays"):
        gateway.initier_depot(
            provider="mtn_momo", phone="+15550001234", montant=1000, reference="ref-1"
        )


def test_unsupported_operator_combo_raises_not_implemented():
    gateway = PawapayGateway()
    # ORANGE_BEN n'est pas dans _SUPPORTED_OPERATORS meme si "orange_money" et le Benin
    # sont individuellement connus.
    with pytest.raises(NotImplementedError, match="ORANGE_BEN"):
        gateway.initier_depot(
            provider="orange_money", phone="+2290100000000", montant=1000, reference="ref-2"
        )


def test_unmapped_provider_raises_not_implemented():
    gateway = PawapayGateway()
    with pytest.raises(NotImplementedError, match="wave"):
        gateway.initier_depot(
            provider="wave", phone="+2250700000000", montant=1000, reference="ref-3"
        )


def test_initier_depot_accepted_maps_to_pending(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "depositId": captured["body"]["depositId"],
                "status": "ACCEPTED",
                "created": "2026-01-01T00:00:00Z",
            },
        )

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    operation = gateway.initier_depot(
        provider="mtn_momo", phone="+2250503456789", montant=250, reference="wallet-ref-1"
    )

    assert operation.status == GatewayStatus.PENDING
    assert operation.provider_reference == captured["body"]["depositId"]
    assert captured["url"].endswith("/v2/deposits")
    assert captured["body"]["payer"]["accountDetails"]["provider"] == "MTN_MOMO_CIV"
    assert captured["body"]["payer"]["accountDetails"]["phoneNumber"] == "2250503456789"
    assert captured["body"]["amount"] == "250"
    assert captured["body"]["currency"] == "XOF"
    assert captured["body"]["clientReferenceId"] == "wallet-ref-1"


def test_initier_depot_rejected_maps_to_failed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "depositId": json.loads(request.content)["depositId"],
                "status": "REJECTED",
                "failureReason": {
                    "failureCode": "AMOUNT_OUT_OF_BOUNDS",
                    "failureMessage": "Amount out of bounds",
                },
            },
        )

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    operation = gateway.initier_depot(
        provider="mtn_momo", phone="+2250503456789", montant=0, reference="wallet-ref-2"
    )

    assert operation.status == GatewayStatus.FAILED


def test_initier_retrait_uses_payouts_endpoint(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"payoutId": captured["body"]["payoutId"], "status": "ACCEPTED"},
        )

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    operation = gateway.initier_retrait(
        provider="orange_money", phone="+2250734567890", montant=75, reference="wallet-ref-3"
    )

    assert operation.status == GatewayStatus.PENDING
    assert captured["url"].endswith("/v2/payouts")
    assert captured["body"]["recipient"]["accountDetails"]["provider"] == "ORANGE_CIV"


def test_verifier_depot_completed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "status": "FOUND",
                "data": {
                    "depositId": "dep-123",
                    "status": "COMPLETED",
                    "amount": "100.00",
                    "currency": "XOF",
                },
            },
        )

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    operation = gateway.verifier_depot("dep-123")

    assert operation.status == GatewayStatus.COMPLETED
    assert operation.amount == 100
    assert operation.currency == "XOF"


def test_verifier_depot_pending_states(monkeypatch):
    for pawapay_status in ("SUBMITTED", "PROCESSING", "IN_RECONCILIATION"):

        def handler(request: httpx.Request, status=pawapay_status) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "FOUND",
                    "data": {"depositId": "dep-123", "status": status},
                },
            )

        _mock_client_factory(monkeypatch, handler)
        gateway = PawapayGateway()

        operation = gateway.verifier_depot("dep-123")
        assert operation.status == GatewayStatus.PENDING


def test_verifier_depot_not_found_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "NOT_FOUND"})

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    with pytest.raises(RuntimeError, match="introuvable"):
        gateway.verifier_depot("missing-id")


def test_verifier_retrait_completed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/v2/payouts/pay-123")
        return httpx.Response(
            200,
            json={
                "status": "FOUND",
                "data": {
                    "payoutId": "pay-123",
                    "status": "COMPLETED",
                    "amount": "75.00",
                    "currency": "XOF",
                },
            },
        )

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    operation = gateway.verifier_retrait("pay-123")

    assert operation.status == GatewayStatus.COMPLETED
    assert operation.amount == 75
    assert operation.currency == "XOF"


def test_verifier_retrait_failed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "FOUND",
                "data": {
                    "payoutId": "pay-456",
                    "status": "FAILED",
                    "failureReason": {
                        "failureCode": "RECIPIENT_NOT_FOUND",
                        "failureMessage": "not found",
                    },
                },
            },
        )

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    operation = gateway.verifier_retrait("pay-456")

    assert operation.status == GatewayStatus.FAILED


def test_verifier_retrait_not_found_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "NOT_FOUND"})

    _mock_client_factory(monkeypatch, handler)
    gateway = PawapayGateway()

    with pytest.raises(RuntimeError, match="introuvable"):
        gateway.verifier_retrait("missing-payout")
