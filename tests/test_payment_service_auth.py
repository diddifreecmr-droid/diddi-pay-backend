from types import SimpleNamespace

import pytest

from payfund_app.core.errors import Unauthenticated
from payfund_app.modules.payments.presentation import deps


def _settings(**overrides):
    values = {
        "payment_service_audience": "diddipay",
        "payment_service_client_id_set": {"diddigo-staging"},
        "payment_service_key_map": {"diddigo-staging": "legacy-secret"},
        "payment_service_key_fallback_enabled": True,
        "payment_intent_read_scope": "diddipay:payment-intents:read",
        "payment_intent_write_scope": "diddipay:payment-intents:write",
        "payment_refund_scope": "diddipay:payment-intents:refund",
        "payment_payout_read_scope": "diddipay:payouts:read",
        "payment_payout_write_scope": "diddipay:payouts:write",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_payment_service_token_enforces_client_audience_and_route_scope(monkeypatch):
    monkeypatch.setattr(deps, "get_settings", lambda: _settings())
    captured = {}

    def verify(token, **kwargs):
        captured.update(token=token, **kwargs)
        return SimpleNamespace(
            client_id="diddigo-staging",
            scopes=frozenset({"diddipay:payment-intents:write"}),
        )

    monkeypatch.setattr(deps, "decode_service_token", verify)
    client = deps.get_payment_intent_writer(
        "Bearer signed-token", "diddigo-staging", None
    )

    assert client.authentication_method == "service_token"
    assert captured == {
        "token": "signed-token",
        "audience": "diddipay",
        "client_id_header": "diddigo-staging",
        "required_scopes": {"diddipay:payment-intents:write"},
        "allowed_client_ids": {"diddigo-staging"},
    }


def test_invalid_bearer_never_falls_back_to_legacy_key(monkeypatch):
    monkeypatch.setattr(deps, "get_settings", lambda: _settings())

    with pytest.raises(Unauthenticated):
        deps.get_payment_intent_writer(
            "Basic invalid", "diddigo-staging", "legacy-secret"
        )


def test_legacy_key_requires_explicit_fallback_switch(monkeypatch):
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: _settings(payment_service_key_fallback_enabled=False),
    )

    with pytest.raises(Unauthenticated):
        deps.get_payment_intent_writer(None, "diddigo-staging", "legacy-secret")


def test_legacy_key_remains_available_during_staging_migration(monkeypatch):
    monkeypatch.setattr(deps, "get_settings", lambda: _settings())

    client = deps.get_payment_intent_writer(
        None, "diddigo-staging", "legacy-secret"
    )

    assert client.client_id == "diddigo-staging"
    assert client.authentication_method == "legacy_key"
