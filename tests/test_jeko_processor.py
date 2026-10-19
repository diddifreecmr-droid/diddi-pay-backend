from __future__ import annotations

import json
import uuid

import httpx
import pytest

from payfund_app.modules.payments.application.errors import (
    ProcessorCallUncertain,
    ProcessorRequestRejected,
)
from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    PayoutRequest,
    RefundRequest,
)
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    NextActionType,
    PayoutStatus,
)
from payfund_app.modules.payments.infra.jeko_processor import JekoPaymentProcessor

STORE_ID = "0ae7d8e1-a033-491a-9048-7e90c4364889"


def processor(handler):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.jeko.test"
    )
    return JekoPaymentProcessor(
        api_key="jeko_not-real",
        api_key_id="id-not-real",
        store_id=STORE_ID,
        base_url="https://api.jeko.test",
        client=client,
    )


def request(**overrides):
    values = {
        "payment_intent_id": uuid.uuid4(),
        "attempt_id": uuid.uuid4(),
        "business_reference": "ride:42",
        "money": Money(5_000),
        "idempotency_key": "ride-42",
        "channel": "mobile_money",
        "network": "wave",
        "customer_phone": "+2250503456789",
        "callback_url": "https://go.diddifree.com/payments/return",
        "metadata": {"ride_id": "42"},
    }
    values.update(overrides)
    return InitializePaymentRequest(**values)


# --- construction guards -------------------------------------------------------------------


def test_missing_api_key_raises():
    with pytest.raises(ValueError, match="JEKO_API_KEY"):
        JekoPaymentProcessor(api_key="", api_key_id="x", store_id=STORE_ID)


def test_missing_api_key_id_raises():
    with pytest.raises(ValueError, match="JEKO_API_KEY_ID"):
        JekoPaymentProcessor(api_key="jeko_x", api_key_id="", store_id=STORE_ID)


def test_missing_store_id_raises():
    with pytest.raises(ValueError, match="JEKO_STORE_ID"):
        JekoPaymentProcessor(api_key="jeko_x", api_key_id="x", store_id="")


# --- initialize_payment --------------------------------------------------------------------


def test_unsupported_network_is_rejected_before_any_http_call():
    def unreached(http_request):
        raise AssertionError("must not call jeko for an unsupported network")

    with pytest.raises(ProcessorRequestRejected, match="jeko_bitcoin|bitcoin"):
        processor(unreached).initialize_payment(request(network="bitcoin"))


def test_missing_callback_url_is_rejected_before_any_http_call():
    def unreached(http_request):
        raise AssertionError("must not call jeko without a callback_url")

    with pytest.raises(ProcessorRequestRejected, match="callback_url"):
        processor(unreached).initialize_payment(request(callback_url=None))


def test_initialize_accepted_maps_to_requires_action_redirect():
    captured = {}

    def handler(http_request):
        captured["url"] = str(http_request.url)
        captured["headers"] = http_request.headers
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "id": "pr-123",
                "storeId": STORE_ID,
                "reference": captured["body"]["reference"],
                "type": "redirect",
                "paymentMethod": "wave",
                "status": "pending",
                "redirectUrl": "https://pay.jeko.africa/pr/pr-123",
                "errorReason": None,
            },
        )

    attempt_id = uuid.uuid4()
    result = processor(handler).initialize_payment(request(attempt_id=attempt_id))

    assert captured["url"].endswith("/partner_api/payment_requests")
    assert captured["headers"]["X-API-KEY"] == "jeko_not-real"
    assert captured["headers"]["X-API-KEY-ID"] == "id-not-real"
    assert captured["body"]["storeId"] == STORE_ID
    # XOF x 100, multiple of 100 -- the Paystack minor-units trick.
    assert captured["body"]["amountCents"] == 500_000
    assert captured["body"]["currency"] == "XOF"
    assert captured["body"]["reference"] == f"dpi_{attempt_id.hex}"
    assert captured["body"]["paymentDetails"]["type"] == "redirect"
    assert captured["body"]["paymentDetails"]["data"]["paymentMethod"] == "wave"
    assert (
        captured["body"]["paymentDetails"]["data"]["successUrl"]
        == "https://go.diddifree.com/payments/return"
    )
    assert result.status == AttemptStatus.REQUIRES_ACTION
    assert result.provider_reference == "pr-123"
    assert result.next_action.type == NextActionType.REDIRECT
    assert result.next_action.url == "https://pay.jeko.africa/pr/pr-123"


def test_initialize_maps_each_supported_network_to_its_payment_method():
    for network in ("orange", "wave", "mtn", "moov", "djamo"):
        captured = {}

        def handler(http_request, _captured=captured):
            _captured["body"] = json.loads(http_request.content)
            return httpx.Response(200, json={"id": "pr-x", "redirectUrl": "https://pay.jeko.africa/pr/pr-x"})

        processor(handler).initialize_payment(request(network=network))
        assert captured["body"]["paymentDetails"]["data"]["paymentMethod"] == network


def test_initialize_validation_error_maps_to_failed_and_reads_errors_shape():
    def handler(http_request):
        return httpx.Response(
            422,
            json={
                "errors": [
                    {
                        "message": "The amountCents field must be a multiple of 100",
                        "rule": "not_multiple_of_100",
                        "field": "amountCents",
                    }
                ]
            },
        )

    result = processor(handler).initialize_payment(request())

    assert result.status == AttemptStatus.FAILED
    assert result.failure_code == "JEKO_PAYMENT_REQUEST_REJECTED"
    assert result.failure_message == "The amountCents field must be a multiple of 100"


def test_initialize_missing_redirect_url_maps_to_failed():
    def handler(http_request):
        return httpx.Response(200, json={"id": "pr-9", "status": "pending"})

    result = processor(handler).initialize_payment(request())

    assert result.status == AttemptStatus.FAILED
    assert result.failure_code == "JEKO_PAYMENT_REQUEST_REJECTED"
    assert result.provider_reference == "pr-9"


def test_initialize_transport_error_raises_processor_call_uncertain():
    def handler(http_request):
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ProcessorCallUncertain):
        processor(handler).initialize_payment(request())


# --- verify_payment ------------------------------------------------------------------------


def test_verify_payment_success_converts_amount_and_fee_from_cents():
    def handler(http_request):
        return httpx.Response(
            200,
            json={
                "id": "pr-123",
                "status": "success",
                "transaction": {
                    "id": "txn-1",
                    "amount": {"amount": 500_000, "currency": "XOF"},
                    "fees": {"amount": 7_500, "currency": "XOF"},
                    "status": "success",
                },
            },
        )

    result = processor(handler).verify_payment("pr-123")

    assert result.status == AttemptStatus.SUCCEEDED
    assert result.amount == 5_000
    assert result.fee == 75
    assert result.currency == "XOF"


def test_verify_payment_pending_is_processing():
    def handler(http_request):
        return httpx.Response(200, json={"id": "pr-123", "status": "pending", "escrowId": None})

    result = processor(handler).verify_payment("pr-123")

    assert result.status == AttemptStatus.PROCESSING
    assert result.amount is None


def test_verify_payment_error_is_failed():
    def handler(http_request):
        return httpx.Response(200, json={"id": "pr-123", "status": "error"})

    result = processor(handler).verify_payment("pr-123")

    assert result.status == AttemptStatus.FAILED


def test_verify_payment_http_error_is_unknown():
    def handler(http_request):
        return httpx.Response(500, json={})

    result = processor(handler).verify_payment("pr-123")

    assert result.status == AttemptStatus.UNKNOWN
    assert result.failure_code == "JEKO_VERIFICATION_FAILED"


def test_verify_payment_fractional_xof_is_invalid_response():
    def handler(http_request):
        return httpx.Response(
            200,
            json={
                "id": "pr-123",
                "status": "success",
                "transaction": {"amount": {"amount": 101, "currency": "XOF"}},
            },
        )

    result = processor(handler).verify_payment("pr-123")

    assert result.status == AttemptStatus.UNKNOWN
    assert result.failure_code == "JEKO_RESPONSE_INVALID"


# --- create_payout -------------------------------------------------------------------------


def payout_request(**overrides):
    values = {
        "payout_id": uuid.uuid4(),
        "business_reference": "diddifood:order:1:restaurant-payout:v1",
        "beneficiary_reference": "restaurant:rest-7",
        "money": Money(5_000),
        "idempotency_key": "payout-1",
        "destination": {"type": "mobile_money", "phone": "+2250503456789", "network": "wave"},
    }
    values.update(overrides)
    return PayoutRequest(**values)


def test_create_payout_accepted_maps_to_processing():
    captured = {}

    def handler(http_request):
        captured["url"] = str(http_request.url)
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(200, json={"id": "tr-1", "status": "pending"})

    payout_id = uuid.uuid4()
    result = processor(handler).create_payout(payout_request(payout_id=payout_id))

    assert captured["url"].endswith("/partner_api/transfers")
    assert captured["body"]["storeId"] == STORE_ID
    assert captured["body"]["amountCents"] == 500_000
    assert captured["body"]["paymentMethod"] == "wave"
    assert captured["body"]["identifier"]["reference"] == "+2250503456789"
    assert captured["body"]["name"] == "restaurant:rest-7"
    assert captured["body"]["reference"] == f"dpo_{payout_id.hex}"
    assert result.status == PayoutStatus.PROCESSING
    assert result.provider_reference == "tr-1"


def test_create_payout_unsupported_network_rejected_before_http():
    def unreached(http_request):
        raise AssertionError("must not call jeko for an unsupported payout network")

    with pytest.raises(ProcessorRequestRejected):
        processor(unreached).create_payout(
            payout_request(destination={"type": "mobile_money", "phone": "+2250503456789", "network": "xyz"})
        )


def test_create_payout_missing_phone_rejected_before_http():
    def unreached(http_request):
        raise AssertionError("must not call jeko without a phone")

    with pytest.raises(ProcessorRequestRejected, match="phone"):
        processor(unreached).create_payout(
            payout_request(destination={"type": "mobile_money", "network": "wave"})
        )


def test_create_payout_non_mobile_money_destination_rejected():
    def unreached(http_request):
        raise AssertionError("must not call jeko for a non mobile_money destination")

    with pytest.raises(ProcessorRequestRejected, match="mobile_money"):
        processor(unreached).create_payout(
            payout_request(destination={"type": "bank", "phone": "x", "network": "wave"})
        )


def test_create_payout_insufficient_balance_maps_to_failed():
    def handler(http_request):
        return httpx.Response(
            400,
            json={
                "id": "insufficient_balance",
                "message": "Insufficient balance",
                "extras": "Internal server error",
            },
        )

    result = processor(handler).create_payout(payout_request())

    assert result.status == PayoutStatus.FAILED
    assert result.failure_code == "JEKO_PAYOUT_REJECTED"
    assert result.failure_message == "Insufficient balance"
    assert result.provider_status == "insufficient_balance"


def test_create_payout_transport_error_raises_processor_call_uncertain():
    def handler(http_request):
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ProcessorCallUncertain):
        processor(handler).create_payout(payout_request())


# --- verify_payout -------------------------------------------------------------------------


def test_verify_payout_success_is_succeeded():
    def handler(http_request):
        return httpx.Response(200, json={"id": "tr-1", "status": "success"})

    result = processor(handler).verify_payout("tr-1")

    assert result.status == PayoutStatus.SUCCEEDED


def test_verify_payout_pending_is_processing():
    def handler(http_request):
        return httpx.Response(200, json={"id": "tr-1", "status": "pending"})

    assert processor(handler).verify_payout("tr-1").status == PayoutStatus.PROCESSING


def test_verify_payout_error_is_failed():
    def handler(http_request):
        return httpx.Response(200, json={"id": "tr-1", "status": "error"})

    assert processor(handler).verify_payout("tr-1").status == PayoutStatus.FAILED


def test_verify_payout_http_error_stays_processing():
    def handler(http_request):
        return httpx.Response(500, json={})

    result = processor(handler).verify_payout("tr-1")

    assert result.status == PayoutStatus.PROCESSING
    assert result.failure_code == "JEKO_VERIFICATION_FAILED"


# --- deliberately-unwired surfaces ---------------------------------------------------------


def test_parse_webhook_not_implemented():
    with pytest.raises(NotImplementedError, match="BCEAO"):
        processor(lambda r: httpx.Response(200)).parse_webhook(b"{}", {})


def test_refund_payment_not_implemented():
    req = RefundRequest(refund_id=uuid.uuid4(), provider_reference="pr-1", money=Money(1_000))
    with pytest.raises(NotImplementedError, match="escrow"):
        processor(lambda r: httpx.Response(200)).refund_payment(req)
