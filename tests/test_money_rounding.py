"""Ticket 144: every amount is a whole franc, nearest, .5 up."""

import uuid
from decimal import Decimal

import httpx
import pytest

from payfund_app.modules.payments.infra.cinetpay_processor import CinetpayPaymentProcessor
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor
from payfund_app.shared_kernel.money_rounding import round_money, round_money_from_subunits


@pytest.mark.parametrize(
    "value,expected",
    [
        (5000, 5000),
        ("5000", 5000),
        ("5000.00", 5000),
        (Decimal("5000.49"), 5000),
        (Decimal("5000.50"), 5001),
        ("4999.5", 5000),
        (2.4, 2),
        (2.5, 3),  # float 2.5 is exact; .5 goes up
        (0.1 + 0.2, 0),  # str() avoids binary float surprises
        (-2.5, -3),
    ],
)
def test_round_money_is_nearest_half_up(value, expected):
    assert round_money(value) == expected


@pytest.mark.parametrize("value", [True, "abc", "NaN", float("inf"), None])
def test_round_money_refuses_non_amounts(value):
    with pytest.raises((ValueError, TypeError)):
        round_money(value)


def test_subunits_round_half_up():
    assert round_money_from_subunits(500_049, 100) == 5000
    assert round_money_from_subunits(500_050, 100) == 5001


def test_providers_round_instead_of_truncating_or_refusing():
    # Paystack XOF amounts are x100 subunits.
    assert PaystackPaymentProcessor._from_paystack_amount(500_050, "XOF") == 5001
    assert PaystackPaymentProcessor._from_paystack_fee(149, "XOF") == 1  # was always up -> 2
    assert PaystackPaymentProcessor._from_paystack_fee(150, "XOF") == 2
    assert PawapayPaymentProcessor._parse_amount("100.50") == 101
    assert PawapayPaymentProcessor._parse_amount("100.00") == 100
    assert CinetpayPaymentProcessor._parse_amount_currency({"amount": "5000.7", "currency": "xof"}) == (5001, "XOF")


HEADERS = {"X-Client-ID": "diddigo", "X-Service-Key": "test-service-key"}


def test_payment_intent_amount_with_decimals_is_rounded(client):
    response = client.post(
        "/payfund/v1/payment-intents",
        headers={**HEADERS, "Idempotency-Key": f"round-{uuid.uuid4()}"},
        json={
            "business_reference": f"ride:{uuid.uuid4()}",
            "amount": 4999.5,
            "currency": "XOF",
            "channel": "mobile_money",
            "network": "orange",
            "customer_phone": "+2250700000000",
            "callback_url": "https://go.diddifree.com/payments/return",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["amount"] == 5000


def test_amount_rounding_to_zero_is_still_refused(client):
    response = client.post(
        "/payfund/v1/payment-intents",
        headers={**HEADERS, "Idempotency-Key": f"round-zero-{uuid.uuid4()}"},
        json={"business_reference": f"ride:{uuid.uuid4()}", "amount": 0.4, "currency": "XOF"},
    )

    assert response.status_code == 422


def test_text_amount_is_still_a_validation_error(client):
    response = client.post(
        "/payfund/v1/payment-intents",
        headers={**HEADERS, "Idempotency-Key": f"round-text-{uuid.uuid4()}"},
        json={"business_reference": f"ride:{uuid.uuid4()}", "amount": "abc", "currency": "XOF"},
    )

    assert response.status_code == 422
