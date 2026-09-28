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
        "payment_callback_allowlist": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


_ALLOWLIST = {"diddifood": ["https://diddifood-backend-staging.diddifree.com/food/v1/payments/return"]}


def test_legacy_callback_within_client_allowlist_is_accepted():
    url = "https://diddifood-backend-staging.diddifree.com/food/v1/payments/return/abc?x=1"
    result = resolve_checkout_return_url(
        settings=settings(
            payment_callback_url_fallback_enabled=True,
            payment_callback_allowlist=_ALLOWLIST,
        ),
        client_id="diddifood",
        return_target=None,
        legacy_callback_url=url,
    )
    assert result == url  # opaque path/query preserved


def test_legacy_callback_outside_client_allowlist_is_rejected():
    with pytest.raises(UnprocessableEntity) as error:
        resolve_checkout_return_url(
            settings=settings(
                payment_callback_url_fallback_enabled=True,
                payment_callback_allowlist=_ALLOWLIST,
            ),
            client_id="diddifood",
            return_target=None,
            legacy_callback_url="https://attacker.example/food/v1/payments/return",
        )
    assert error.value.code == "PAYMENT_CALLBACK_URL_FORBIDDEN"


def test_host_suffix_spoof_is_rejected():
    with pytest.raises(UnprocessableEntity) as error:
        resolve_checkout_return_url(
            settings=settings(
                payment_callback_url_fallback_enabled=True,
                payment_callback_allowlist=_ALLOWLIST,
            ),
            client_id="diddifood",
            return_target=None,
            legacy_callback_url="https://diddifood-backend-staging.diddifree.com.attacker.com/food/v1/payments/return",
        )
    assert error.value.code == "PAYMENT_CALLBACK_URL_FORBIDDEN"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://diddifood-backend-staging.diddifree.com/food/v1/payments/return",  # not https
        "https://user:pass@diddifood-backend-staging.diddifree.com/food/v1/payments/return",  # userinfo
        "https://diddifood-backend-staging.diddifree.com/food/v1/payments/return/../../evil",  # traversal
    ],
)
def test_clear_attacks_are_always_rejected(bad_url):
    with pytest.raises(UnprocessableEntity) as error:
        resolve_checkout_return_url(
            settings=settings(
                payment_callback_url_fallback_enabled=True,
                payment_callback_allowlist=_ALLOWLIST,
            ),
            client_id="diddifood",
            return_target=None,
            legacy_callback_url=bad_url,
        )
    assert error.value.code == "PAYMENT_CALLBACK_URL_FORBIDDEN"


def test_legacy_callback_allowed_transitionally_when_no_allowlist_for_client():
    url = "https://diddisend-api-staging.diddifree.com/v1/payments/return"
    result = resolve_checkout_return_url(
        settings=settings(payment_callback_url_fallback_enabled=True),
        client_id="diddisend",
        return_target=None,
        legacy_callback_url=url,
    )
    assert result == url


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
