from types import SimpleNamespace

import pytest

from payfund_app.core.errors import UnprocessableEntity
from payfund_app.modules.payments.presentation.checkout_return import (
    resolve_checkout_return_url,
)


def settings(**overrides):
    values = {
        "payment_checkout_return_targets": {
            "diddigo-staging:app": "https://go-staging.diddifree.com/payments/return"
        },
        "payment_callback_url_fallback_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_return_target_is_resolved_from_server_configuration():
    result = resolve_checkout_return_url(
        settings=settings(),
        client_id="diddigo-staging",
        return_target="app",
        legacy_callback_url=None,
    )
    assert result == "https://go-staging.diddifree.com/payments/return"


def test_unknown_or_cross_client_target_is_rejected():
    with pytest.raises(UnprocessableEntity) as error:
        resolve_checkout_return_url(
            settings=settings(),
            client_id="diddisend-staging",
            return_target="app",
            legacy_callback_url=None,
        )
    assert error.value.code == "PAYMENT_RETURN_TARGET_FORBIDDEN"


def test_arbitrary_legacy_callback_is_rejected_when_fallback_is_disabled():
    with pytest.raises(UnprocessableEntity) as error:
        resolve_checkout_return_url(
            settings=settings(),
            client_id="diddigo-staging",
            return_target=None,
            legacy_callback_url="https://attacker.example/steal",
        )
    assert error.value.code == "PAYMENT_CALLBACK_URL_DISABLED"


def test_target_and_legacy_callback_cannot_be_combined():
    with pytest.raises(UnprocessableEntity) as error:
        resolve_checkout_return_url(
            settings=settings(payment_callback_url_fallback_enabled=True),
            client_id="diddigo-staging",
            return_target="app",
            legacy_callback_url="https://go-staging.diddifree.com/payments/return",
        )
    assert error.value.code == "PAYMENT_RETURN_TARGET_CONFLICT"
