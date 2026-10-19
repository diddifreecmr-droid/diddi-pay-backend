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
from payfund_app.modules.payments.infra.cinetpay_processor import (
    CinetpayPaymentProcessor,
)

_TOKEN_OK = {"code": 0, "status": "OK", "data": {"access_token": "jwt-test"}}


def processor(handler, *, login_response=None, api_key="sk_test_abc"):
    """Build a processor whose MockTransport auto-answers /v1/oauth/login with a token and
    routes everything else to `handler`. `login_response` overrides the login reply."""
    logins = {"count": 0}

    def dispatch(http_request):
        if http_request.url.path == "/v1/oauth/login":
            logins["count"] += 1
            return login_response or httpx.Response(200, json=_TOKEN_OK)
        return handler(http_request)

    client = httpx.Client(
        transport=httpx.MockTransport(dispatch), base_url="https://api.cinetpay.net"
    )
    proc = CinetpayPaymentProcessor(
        api_key=api_key,
        api_password="pwd-not-real",
        base_url="https://api.cinetpay.net",
        client=client,
    )
    proc._test_login_count = logins  # type: ignore[attr-defined]
    return proc


def request(**overrides):
    values = {
        "payment_intent_id": uuid.uuid4(),
        "attempt_id": uuid.uuid4(),
        "business_reference": "ride:42",
        "money": Money(5_000),
        "idempotency_key": "ride-42",
        "channel": "mobile_money",
        "network": "wave",
        "customer_email": "client@example.com",
        "customer_phone": "+2250503456789",
        "callback_url": "https://go.diddifree.com/payments/return",
        "metadata": {},
    }
    values.update(overrides)
    return InitializePaymentRequest(**values)


# -- construction & config -----------------------------------------------------------


def test_missing_api_key_raises():
    with pytest.raises(ValueError, match="CINETPAY_API_KEY"):
        CinetpayPaymentProcessor(api_key="", api_password="pwd")


def test_missing_api_password_raises():
    with pytest.raises(ValueError, match="CINETPAY_API_PASSWORD"):
        CinetpayPaymentProcessor(api_key="sk_test_abc", api_password="")


def test_base_url_auto_detected_from_key_prefix():
    sandbox = CinetpayPaymentProcessor(api_key="sk_test_abc", api_password="p")
    live = CinetpayPaymentProcessor(api_key="sk_live_abc", api_password="p")
    assert sandbox._base_url == "https://api.cinetpay.net"
    assert live._base_url == "https://api.cinetpay.co"


# -- authentication ------------------------------------------------------------------


def test_login_sends_both_api_key_and_api_password():
    captured = {}

    def dispatch(http_request):
        if http_request.url.path == "/v1/oauth/login":
            captured["body"] = json.loads(http_request.content)
            return httpx.Response(200, json=_TOKEN_OK)
        return httpx.Response(200, json={"payment_url": "https://pay.cinetpay.net/x"})

    client = httpx.Client(transport=httpx.MockTransport(dispatch), base_url="https://api.cinetpay.net")
    proc = CinetpayPaymentProcessor(
        api_key="sk_test_abc",
        api_password="pwd-not-real",
        base_url="https://api.cinetpay.net",
        client=client,
    )
    proc.initialize_payment(request())
    assert captured["body"] == {"api_key": "sk_test_abc", "api_password": "pwd-not-real"}


def test_login_without_token_is_rejected():
    proc = processor(
        lambda r: httpx.Response(200, json={"payment_url": "x"}),
        login_response=httpx.Response(
            422, json={"code": 1005, "status": "INVALID_CREDENTIALS", "description": "bad creds"}
        ),
    )
    with pytest.raises(ProcessorRequestRejected, match="cinetpay login rejected"):
        proc.initialize_payment(request())


def test_expired_token_triggers_one_refresh_and_retry():
    calls = {"n": 0}

    def handler(http_request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"code": 1003, "status": "EXPIRED_TOKEN"})
        return httpx.Response(200, json={"payment_url": "https://pay.cinetpay.net/ok"})

    proc = processor(handler)
    result = proc.initialize_payment(request())
    assert result.status == AttemptStatus.REQUIRES_ACTION
    assert calls["n"] == 2
    assert proc._test_login_count["count"] == 2  # logged in again after the expiry


# -- collection ----------------------------------------------------------------------


def test_missing_email_is_rejected_before_any_http_call():
    def unreached(http_request):
        raise AssertionError("must not call cinetpay without an email")

    with pytest.raises(ProcessorRequestRejected, match="customer_email"):
        processor(unreached).initialize_payment(request(customer_email=None))


def test_missing_callback_url_is_rejected_before_any_http_call():
    def unreached(http_request):
        raise AssertionError("must not call cinetpay without a callback url")

    with pytest.raises(ProcessorRequestRejected, match="callback_url"):
        processor(unreached).initialize_payment(request(callback_url=None))


def test_initialize_success_maps_to_requires_action_redirect():
    captured = {}

    def handler(http_request):
        assert http_request.url.path == "/v1/payment"
        assert http_request.headers["Authorization"] == "Bearer jwt-test"
        captured["body"] = json.loads(http_request.content)
        # Real shape verified live: HTTP 200, top-level status OK, payment_url to the hosted
        # checkout, real state in details (INITIATED / must_be_redirected).
        return httpx.Response(
            200,
            json={
                "code": 200,
                "status": "OK",
                "payment_token": "ptk",
                "notify_token": "ntk",
                "transaction_id": "cp-1",
                "payment_url": "https://secure.cinetpay.net/checkout/ptk",
                "details": {"code": 2001, "status": "INITIATED", "must_be_redirected": True},
            },
        )

    attempt_id = uuid.uuid4()
    result = processor(handler).initialize_payment(request(attempt_id=attempt_id))

    body = captured["body"]
    assert body["merchant_transaction_id"] == str(attempt_id)
    assert body["amount"] == 5000 and isinstance(body["amount"], int)
    assert body["currency"] == "XOF"
    assert (
        body["success_url"]
        == body["failed_url"]
        == body["notify_url"]
        == "https://go.diddifree.com/payments/return"
    )
    # channel=ALL (not MOBILE_MONEY, which returns OPERATION_ERROR on the account -- verified live)
    assert body["channel"] == "ALL"
    assert body["client_phone_number"] == "2250503456789"
    assert result.status == AttemptStatus.REQUIRES_ACTION
    assert result.provider_reference == str(attempt_id)
    assert result.next_action.type == NextActionType.REDIRECT
    assert result.next_action.url == "https://secure.cinetpay.net/checkout/ptk"


def test_initialize_is_idempotent_on_retry_same_attempt_id():
    seen = set()

    def handler(http_request):
        seen.add(json.loads(http_request.content)["merchant_transaction_id"])
        return httpx.Response(200, json={"payment_url": "https://checkout.cinetpay.net/x"})

    attempt_id = uuid.uuid4()
    req = request(attempt_id=attempt_id)
    processor(handler).initialize_payment(req)
    processor(handler).initialize_payment(req)
    assert seen == {str(attempt_id)}


def test_initialize_operation_error_maps_to_failed():
    # Real shape verified live when CinetPay rejects at the gateway (e.g. a misconfigured channel).
    def handler(http_request):
        return httpx.Response(
            422,
            json={
                "code": -1,
                "status": "OPERATION_ERROR",
                "description": "Une erreur est survenue",
                "error": "contact admin at tech@paymetrust.net",
            },
        )

    result = processor(handler).initialize_payment(request())
    assert result.status == AttemptStatus.FAILED
    assert result.failure_code == "CINETPAY_INITIALIZATION_FAILED"
    assert "Une erreur est survenue" in result.failure_message


def test_initialize_invalid_params_in_details_maps_to_failed():
    # Real shape verified live: HTTP 200, top-level OK, real error nested in details.
    def handler(http_request):
        return httpx.Response(
            200,
            json={
                "code": 200,
                "status": "OK",
                "payment_token": "ptk",
                "details": {
                    "code": 1004,
                    "status": "INVALID_PARAMS",
                    "message": "Params you provides are invalid",
                },
            },
        )

    result = processor(handler).initialize_payment(request())
    assert result.status == AttemptStatus.FAILED
    assert result.failure_code == "CINETPAY_INITIALIZATION_FAILED"
    assert result.provider_status == "INVALID_PARAMS"
    assert "invalid" in result.failure_message.lower()


def test_initialize_transport_error_raises_processor_call_uncertain():
    def handler(http_request):
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ProcessorCallUncertain):
        processor(handler).initialize_payment(request())


# -- verify payment ------------------------------------------------------------------


def test_verify_payment_success():
    def handler(http_request):
        assert http_request.url.path.startswith("/v1/payment/")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "status": "SUCCESS",
                    "transaction_id": "cp-1",
                    "amount": "5000",
                    "currency": "XOF",
                },
            },
        )

    result = processor(handler).verify_payment("merch-1")
    assert result.status == AttemptStatus.SUCCEEDED
    assert result.amount == 5000
    assert result.currency == "XOF"
    assert result.provider_reference == "cp-1"


def test_verify_payment_pending_is_processing():
    def handler(http_request):
        return httpx.Response(200, json={"data": {"status": "PENDING"}})

    result = processor(handler).verify_payment("merch-2")
    assert result.status == AttemptStatus.PROCESSING


def test_verify_payment_initiated_unpaid_is_processing():
    # Real shape verified live: an unpaid hosted payment is flat, top-level status INITIATED.
    def handler(http_request):
        return httpx.Response(
            200,
            json={
                "code": 2001,
                "status": "INITIATED",
                "merchant_transaction_id": "merch-x",
                "transaction_id": "cp-x",
                "user": None,
            },
        )

    result = processor(handler).verify_payment("merch-x")
    assert result.status == AttemptStatus.PROCESSING
    assert result.provider_reference == "cp-x"
    assert result.provider_status == "initiated"


def test_verify_payment_failed():
    def handler(http_request):
        return httpx.Response(200, json={"data": {"status": "FAILED"}})

    result = processor(handler).verify_payment("merch-3")
    assert result.status == AttemptStatus.FAILED
    assert result.failure_code == "CINETPAY_PAYMENT_FAILED"


def test_verify_payment_http_error_is_unknown():
    def handler(http_request):
        return httpx.Response(500, json={})

    result = processor(handler).verify_payment("merch-4")
    assert result.status == AttemptStatus.UNKNOWN
    assert result.failure_code == "CINETPAY_VERIFICATION_FAILED"


# -- payout --------------------------------------------------------------------------


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


def test_create_payout_accepted_maps_to_processing_and_resolves_wave_ci():
    captured = {}

    def handler(http_request):
        assert http_request.url.path == "/v1/transfer"
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(200, json={"status": "PENDING", "transaction_id": "tr-9"})

    payout_id = uuid.uuid4()
    result = processor(handler).create_payout(payout_request(payout_id=payout_id))

    assert captured["body"]["payment_method"] == "WAVE_CI"
    assert captured["body"]["phone_number"] == "+2250503456789"
    assert captured["body"]["amount"] == 5000
    assert captured["body"]["merchant_transaction_id"] == str(payout_id)
    assert result.status == PayoutStatus.PROCESSING
    assert result.provider_reference == "tr-9"


def test_create_payout_rejects_non_mobile_money_destination():
    def unreached(http_request):
        raise AssertionError("must not call cinetpay for an unsupported destination type")

    with pytest.raises(ProcessorRequestRejected, match="mobile_money"):
        processor(unreached).create_payout(
            payout_request(destination={"type": "bank_transfer", "account_number": "123"})
        )


def test_create_payout_rejects_missing_phone():
    def unreached(http_request):
        raise AssertionError("must not call cinetpay without a phone number")

    with pytest.raises(ProcessorRequestRejected, match="phone"):
        processor(unreached).create_payout(
            payout_request(destination={"type": "mobile_money", "network": "wave"})
        )


def test_create_payout_rejects_unknown_network():
    def unreached(http_request):
        raise AssertionError("must not call cinetpay for an unknown network")

    with pytest.raises(ProcessorRequestRejected, match="network"):
        processor(unreached).create_payout(
            payout_request(
                destination={"type": "mobile_money", "phone": "+2250503456789", "network": "bitcoin"}
            )
        )


def test_create_payout_insufficient_balance_maps_to_failed():
    # Real shape verified live: HTTP 200, top-level status FAILED, reason nested in details.
    def handler(http_request):
        return httpx.Response(
            200,
            json={
                "code": 2010,
                "status": "FAILED",
                "merchant_transaction_id": "m-1",
                "transaction_id": "ER-T-1",
                "amount": 500,
                "fee_amount": 10,
                "currency": "XOF",
                "details": {
                    "code": 2005,
                    "status": "INSUFFICIENT_BALANCE",
                    "message": "User has not enough balance to validate operation",
                },
            },
        )

    result = processor(handler).create_payout(payout_request())
    assert result.status == PayoutStatus.FAILED
    assert result.failure_code == "CINETPAY_PAYOUT_REJECTED"
    assert result.provider_status == "insufficient_balance"
    assert result.provider_reference == "ER-T-1"
    assert "enough balance" in result.failure_message


def test_create_payout_transport_error_raises_processor_call_uncertain():
    def handler(http_request):
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ProcessorCallUncertain):
        processor(handler).create_payout(payout_request())


def test_verify_payout_success():
    # Flat top-level status, matching the live /v1/payment verify shape (no `data` wrapper).
    def handler(http_request):
        assert http_request.url.path.startswith("/v1/transfer/")
        return httpx.Response(200, json={"code": 200, "status": "SUCCESS", "transaction_id": "tr-9"})

    result = processor(handler).verify_payout("tr-9")
    assert result.status == PayoutStatus.SUCCEEDED
    assert result.provider_reference == "tr-9"


def test_verify_payout_not_found_stays_processing():
    def handler(http_request):
        return httpx.Response(404, json={})

    result = processor(handler).verify_payout("tr-missing")
    assert result.status == PayoutStatus.PROCESSING
    assert result.failure_code == "CINETPAY_VERIFICATION_FAILED"


# -- unsupported ---------------------------------------------------------------------


def test_refund_payment_not_implemented():
    req = RefundRequest(refund_id=uuid.uuid4(), provider_reference="cp-1", money=Money(1_000))
    with pytest.raises(NotImplementedError, match="no refund endpoint"):
        processor(lambda r: httpx.Response(200)).refund_payment(req)


def test_parse_webhook_not_implemented():
    with pytest.raises(NotImplementedError, match="BCEAO"):
        processor(lambda r: httpx.Response(200)).parse_webhook(b"{}", {})
