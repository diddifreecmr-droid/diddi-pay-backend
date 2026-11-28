"""Generic provider callback route (ticket 110, step 3)."""

import json

import pytest

from payfund_app.main import app
from payfund_app.modules.payments.infra.cinetpay_processor import CinetpayPaymentProcessor
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor
from payfund_app.modules.payments.presentation.deps import get_webhook_processor_resolver

API = "/payfund/v1"


def _use(adapter_factory):
    seen = []

    def resolver(name, kind):
        seen.append((name, str(kind)))
        return adapter_factory()

    app.dependency_overrides[get_webhook_processor_resolver] = lambda: resolver
    return seen


@pytest.mark.parametrize(
    "path",
    [
        f"{API}/webhooks/unknown/payments",
        f"{API}/webhooks/pawapay/bogus",
        f"{API}/webhooks/jeko/payments",  # Jeko declares no callbacks
        f"{API}/webhooks/cinetpay/refunds",  # CinetPay declares payment callbacks only
        f"{API}/webhooks/paystack/payouts",
        "/webhooks/pawapay/bogus",
    ],
)
def test_undeclared_processor_or_kind_is_404_before_any_adapter_is_built(client, path):
    seen = _use(lambda: pytest.fail("no adapter may be built for an unknown route"))

    response = client.post(path, content=b"{}", headers={"Content-Type": "application/json"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WEBHOOK_ROUTE_NOT_FOUND"
    assert seen == []


@pytest.mark.parametrize(
    "path",
    [f"{API}/webhooks/paystack/payments", f"{API}/payments/webhooks/paystack"],
)
def test_paystack_generic_path_and_alias_reach_the_same_validation(client, path):
    seen = _use(lambda: PaystackPaymentProcessor(secret_key="sk_test", webhook_secret="whsec"))

    response = client.post(
        path,
        content=b'{"event":"charge.success","data":{}}',
        headers={"Content-Type": "application/json", "X-Paystack-Signature": "bad"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_REJECTED"
    assert seen == [("paystack", "payment")]


@pytest.mark.parametrize(
    "path,kind",
    [
        (f"{API}/webhooks/pawapay/deposits", "payment"),
        (f"{API}/webhooks/pawapay/payments", "payment"),
        (f"{API}/webhooks/pawapay/refunds", "refund"),
        (f"{API}/webhooks/pawapay/payouts", "payout"),
        ("/webhooks/pawapay/deposits", "payment"),  # legacy root path in the PawaPay dashboard
        ("/webhooks/pawapay/payouts", "payout"),
    ],
)
def test_pawapay_paths_map_to_the_right_callback_kind(client, path, kind):
    seen = _use(
        lambda: PawapayPaymentProcessor(api_token="test-token", base_url="https://api.pawapay.test")
    )

    response = client.post(path, content=b"not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 401
    assert seen == [("pawapay", kind)]


def test_cinetpay_notification_for_unknown_attempt_is_rejected(client):
    _use(
        lambda: CinetpayPaymentProcessor(
            api_key="sk_test_abc", api_password="pw", base_url="https://api.cinetpay.test"
        )
    )
    body = json.dumps(
        {
            "notify_token": "tok",
            "merchant_transaction_id": "00000000-0000-0000-0000-00000000dead",
            "transaction_id": "ER-T-1",
            "status": "SUCCESS",
        }
    ).encode()

    response = client.post(
        f"{API}/payments/webhooks/cinetpay",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_REJECTED"
