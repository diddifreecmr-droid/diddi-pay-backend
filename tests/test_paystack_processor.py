import json
import hashlib
import hmac
import uuid

import httpx
import pytest

from payfund_app.modules.payments.application.errors import (
    ProcessorCallUncertain,
    ProcessorRequestRejected,
)
from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    RefundRequest,
)
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    NextActionType,
    RefundStatus,
)
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor


def request(**overrides):
    values = {
        "payment_intent_id": uuid.uuid4(),
        "attempt_id": uuid.uuid4(),
        "business_reference": "ride:42",
        "money": Money(5_000),
        "idempotency_key": "ride-42",
        "channel": "mobile_money",
        "network": "orange",
        "customer_email": "payer@example.com",
        "callback_url": "https://go.diddifree.com/payment-return",
        "metadata": {"ride_id": "42"},
    }
    values.update(overrides)
    return InitializePaymentRequest(**values)


def processor(handler):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.paystack.test"
    )
    return PaystackPaymentProcessor(
        secret_key="sk_test_secret",
        base_url="https://api.paystack.test",
        client=client,
    )


def test_initialize_maps_paystack_checkout_to_generic_redirect():
    captured = {}

    def handler(http_request):
        captured["request"] = http_request
        return httpx.Response(
            200,
            json={
                "status": True,
                "message": "Authorization URL created",
                "data": {
                    "authorization_url": "https://checkout.paystack.test/abc",
                    "access_code": "abc",
                    "reference": "dpi_reference",
                },
            },
        )

    result = processor(handler).initialize_payment(request())
    sent = json.loads(captured["request"].content)

    assert captured["request"].headers["Authorization"] == "Bearer sk_test_secret"
    assert sent["amount"] == 5_000
    assert sent["currency"] == "XOF"
    assert sent["channels"] == ["mobile_money"]
    assert sent["metadata"]["requested_network"] == "orange"
    assert result.status == AttemptStatus.REQUIRES_ACTION
    assert result.next_action.type == NextActionType.REDIRECT
    assert result.next_action.url == "https://checkout.paystack.test/abc"


def test_initialize_requires_email_before_calling_paystack():
    adapter = processor(lambda _: pytest.fail("HTTP must not be called"))
    with pytest.raises(ProcessorRequestRejected, match="customer_email"):
        adapter.initialize_payment(request(customer_email=None))


def test_refund_maps_paystack_processing_response():
    captured = {}

    def handler(http_request):
        captured["request"] = http_request
        return httpx.Response(
            200,
            json={
                "status": True,
                "message": "Refund has been queued for processing",
                "data": {"id": 12345, "status": "processing"},
            },
        )

    result = processor(handler).refund_payment(
        RefundRequest(
            refund_id=uuid.uuid4(),
            provider_reference="dpi_reference",
            money=Money(2_000),
            reason="Ride cancelled",
        )
    )
    sent = json.loads(captured["request"].content)

    assert captured["request"].url.path == "/refund"
    assert sent["transaction"] == "dpi_reference"
    assert sent["amount"] == 2_000
    assert sent["currency"] == "XOF"
    assert result.status == RefundStatus.PROCESSING
    assert result.provider_reference == "12345"


def test_initialize_timeout_is_ambiguous_not_failed():
    def handler(http_request):
        raise httpx.ReadTimeout("timeout", request=http_request)

    with pytest.raises(ProcessorCallUncertain) as raised:
        processor(handler).initialize_payment(request())
    assert raised.value.provider_reference.startswith("dpi_")


@pytest.mark.parametrize(
    ("provider_status", "expected"),
    [
        ("success", AttemptStatus.SUCCEEDED),
        ("failed", AttemptStatus.FAILED),
        ("abandoned", AttemptStatus.FAILED),
        ("pending", AttemptStatus.PROCESSING),
        ("mystery", AttemptStatus.UNKNOWN),
    ],
)
def test_verify_normalizes_paystack_status(provider_status, expected):
    def handler(_):
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "reference": "dpi_reference",
                    "status": provider_status,
                    "amount": 5_000,
                    "currency": "XOF",
                },
            },
        )

    result = processor(handler).verify_payment("dpi_reference")
    assert result.status == expected
    assert result.amount == 5_000
    assert result.currency == "XOF"


def test_http_rejection_is_a_definitive_failed_attempt():
    def handler(_):
        return httpx.Response(400, json={"status": False, "message": "Invalid email"})

    result = processor(handler).initialize_payment(request())
    assert result.status == AttemptStatus.FAILED
    assert result.failure_code == "PAYSTACK_INITIALIZATION_FAILED"


def test_webhook_signature_and_payload_are_normalized_and_sanitized():
    raw = json.dumps(
        {
            "event": "charge.success",
            "data": {
                "reference": "dpi_reference",
                "status": "success",
                "amount": 5_000,
                "currency": "XOF",
                "channel": "mobile_money",
                "customer": {"email": "private@example.com"},
                "authorization": {"last4": "4081"},
            },
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(b"sk_test_secret", raw, hashlib.sha512).hexdigest()

    event = processor(lambda _: pytest.fail("HTTP must not be called")).parse_webhook(
        raw, {"X-Paystack-Signature": signature}
    )

    assert event.status == AttemptStatus.SUCCEEDED
    assert event.amount == 5_000
    assert "customer" not in event.sanitized_payload
    assert "authorization" not in event.sanitized_payload


def test_webhook_rejects_invalid_signature():
    adapter = processor(lambda _: pytest.fail("HTTP must not be called"))
    with pytest.raises(Exception, match="invalid Paystack webhook signature"):
        adapter.parse_webhook(b"{}", {"x-paystack-signature": "invalid"})
