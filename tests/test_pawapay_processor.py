from __future__ import annotations

import json
import uuid

import httpx
import pytest

from payfund_app.modules.payments.application.errors import (
    ProcessorCallUncertain,
    ProcessorRequestRejected,
)
from payfund_app.modules.payments.application.ports import InitializePaymentRequest
from payfund_app.modules.payments.domain import AttemptStatus, Money, NextActionType
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor


def processor(handler):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.pawapay.test"
    )
    return PawapayPaymentProcessor(
        api_token="sandbox-token-not-real",
        base_url="https://api.pawapay.test",
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
        "network": "mtn",
        "customer_phone": "+2250503456789",
        "metadata": {"ride_id": "42"},
    }
    values.update(overrides)
    return InitializePaymentRequest(**values)


def test_missing_token_raises():
    with pytest.raises(ValueError, match="PAWAPAY_API_TOKEN"):
        PawapayPaymentProcessor(api_token="")


def test_unsupported_network_is_rejected_before_any_http_call():
    def unreached(request):
        raise AssertionError("must not call pawapay for an unsupported network")

    with pytest.raises(ProcessorRequestRejected, match="wave"):
        processor(unreached).initialize_payment(request(network="wave"))


def test_missing_phone_is_rejected_before_any_http_call():
    def unreached(request):
        raise AssertionError("must not call pawapay without a phone number")

    with pytest.raises(ProcessorRequestRejected, match="customer_phone"):
        processor(unreached).initialize_payment(request(customer_phone=None))


def test_unrecognised_dial_code_is_rejected():
    def unreached(request):
        raise AssertionError("must not call pawapay for an unrecognised dial code")

    with pytest.raises(ProcessorRequestRejected, match="dial code"):
        processor(unreached).initialize_payment(request(customer_phone="+15550001234"))


def test_inactive_operator_combo_is_rejected():
    def unreached(request):
        raise AssertionError("must not call pawapay for an inactive operator")

    # orange_money + Benin is individually known but not an active combo (see
    # _SUPPORTED_OPERATORS / GET /v2/active-conf).
    with pytest.raises(ProcessorRequestRejected, match="ORANGE_BEN"):
        processor(unreached).initialize_payment(
            request(network="orange", customer_phone="+2290100000000")
        )


def test_initialize_accepted_maps_to_requires_action_mobile_money_prompt():
    captured = {}

    def handler(http_request):
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={"depositId": captured["body"]["depositId"], "status": "ACCEPTED"},
        )

    attempt_id = uuid.uuid4()
    result = processor(handler).initialize_payment(request(attempt_id=attempt_id))

    assert captured["body"]["payer"]["accountDetails"]["provider"] == "MTN_MOMO_CIV"
    assert captured["body"]["payer"]["accountDetails"]["phoneNumber"] == "2250503456789"
    assert captured["body"]["amount"] == "5000"
    assert captured["body"]["currency"] == "XOF"
    assert captured["body"]["depositId"] == str(attempt_id)
    assert result.status == AttemptStatus.REQUIRES_ACTION
    assert result.provider_reference == str(attempt_id)
    assert result.next_action.type == NextActionType.MOBILE_MONEY_PROMPT
    assert result.next_action.url is None
    assert result.next_action.instructions


def test_initialize_is_idempotent_on_retry_same_attempt_id():
    """Unlike the wallet's fresh-uuid4-per-call, depositId is derived from attempt_id so a
    retried HTTP call resubmits the same depositId -- pawapay documents this as safe."""
    seen_deposit_ids = set()

    def handler(http_request):
        seen_deposit_ids.add(json.loads(http_request.content)["depositId"])
        return httpx.Response(200, json={"depositId": "x", "status": "ACCEPTED"})

    attempt_id = uuid.uuid4()
    req = request(attempt_id=attempt_id)
    processor(handler).initialize_payment(req)
    processor(handler).initialize_payment(req)

    assert seen_deposit_ids == {str(attempt_id)}


def test_initialize_rejected_maps_to_failed():
    def handler(http_request):
        return httpx.Response(
            200,
            json={
                "depositId": json.loads(http_request.content)["depositId"],
                "status": "REJECTED",
                "failureReason": {
                    "failureCode": "AMOUNT_OUT_OF_BOUNDS",
                    "failureMessage": "Amount out of bounds",
                },
            },
        )

    result = processor(handler).initialize_payment(request())

    assert result.status == AttemptStatus.FAILED
    assert result.failure_code == "PAWAPAY_DEPOSIT_REJECTED"


def test_initialize_transport_error_raises_processor_call_uncertain():
    def handler(http_request):
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ProcessorCallUncertain):
        processor(handler).initialize_payment(request())


def test_verify_payment_completed():
    def handler(http_request):
        return httpx.Response(
            200,
            json={
                "status": "FOUND",
                "data": {
                    "depositId": "dep-1",
                    "status": "COMPLETED",
                    "amount": "5000.00",
                    "currency": "XOF",
                },
            },
        )

    result = processor(handler).verify_payment("dep-1")

    assert result.status == AttemptStatus.SUCCEEDED
    assert result.amount == 5_000
    assert result.currency == "XOF"


def test_verify_payment_still_processing():
    def handler(http_request):
        return httpx.Response(
            200,
            json={"status": "FOUND", "data": {"depositId": "dep-2", "status": "SUBMITTED"}},
        )

    result = processor(handler).verify_payment("dep-2")

    assert result.status == AttemptStatus.PROCESSING


def test_verify_payment_not_found():
    def handler(http_request):
        return httpx.Response(200, json={"status": "NOT_FOUND"})

    result = processor(handler).verify_payment("dep-missing")

    assert result.status == AttemptStatus.UNKNOWN
    assert result.failure_code == "PAWAPAY_VERIFICATION_FAILED"


def test_parse_webhook_not_implemented():
    with pytest.raises(NotImplementedError, match="BCEAO"):
        processor(lambda r: httpx.Response(200)).parse_webhook(b"{}", {})


def test_refund_payment_not_implemented():
    from payfund_app.modules.payments.application.ports import RefundRequest
    from payfund_app.modules.payments.domain import Money as RefundMoney

    with pytest.raises(ProcessorRequestRejected, match="payout"):
        processor(lambda r: httpx.Response(200)).refund_payment(
            RefundRequest(
                refund_id=uuid.uuid4(),
                provider_reference="dep-1",
                money=RefundMoney(1_000),
            )
        )
