import uuid

import pytest

from payfund_app.modules.payments.domain import (
    AttemptStatus,
    InvalidAmount,
    InvalidStateTransition,
    Money,
    NextAction,
    NextActionType,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
    Refund,
    RefundStatus,
)


def make_intent(amount: int = 5_000) -> PaymentIntent:
    return PaymentIntent(
        client_id="diddigo",
        business_reference="ride:42",
        money=Money(amount, "xof"),
        idempotency_key="ride-42-payment",
        request_fingerprint="fingerprint",
    )


def test_money_requires_positive_integer_minor_units():
    assert Money(5_000, "xof") == Money(5_000, "XOF")
    with pytest.raises(InvalidAmount):
        Money(0)
    with pytest.raises(InvalidAmount):
        Money(True)


def test_redirect_action_requires_url():
    with pytest.raises(ValueError, match="requires a URL"):
        NextAction(NextActionType.REDIRECT)


def test_intent_follows_safe_payment_lifecycle():
    intent = make_intent()
    intent.transition_to(PaymentIntentStatus.REQUIRES_ACTION)
    intent.transition_to(PaymentIntentStatus.PROCESSING)
    intent.transition_to(PaymentIntentStatus.SUCCEEDED)

    assert intent.status == PaymentIntentStatus.SUCCEEDED
    with pytest.raises(InvalidStateTransition):
        intent.transition_to(PaymentIntentStatus.FAILED)


def test_failed_intent_can_start_a_new_attempt_but_cancelled_cannot():
    intent = make_intent()
    intent.transition_to(PaymentIntentStatus.FAILED)
    intent.transition_to(PaymentIntentStatus.PROCESSING)
    assert intent.status == PaymentIntentStatus.PROCESSING

    cancelled = make_intent()
    cancelled.transition_to(PaymentIntentStatus.CANCELLED)
    with pytest.raises(InvalidStateTransition):
        cancelled.transition_to(PaymentIntentStatus.PROCESSING)


def test_attempt_unknown_is_not_a_failure_and_can_be_reconciled():
    attempt = PaymentAttempt(
        payment_intent_id=uuid.uuid4(),
        processor="paystack",
        channel="mobile_money",
        money=Money(5_000),
        attempt_number=1,
    )
    attempt.transition_to(AttemptStatus.UNKNOWN)
    attempt.transition_to(AttemptStatus.SUCCEEDED)

    assert attempt.status == AttemptStatus.SUCCEEDED
    with pytest.raises(InvalidStateTransition):
        attempt.transition_to(AttemptStatus.FAILED)


def test_refunds_cannot_exceed_captured_amount():
    intent = make_intent()
    intent.transition_to(PaymentIntentStatus.SUCCEEDED)
    intent.apply_refund(2_000)
    assert intent.status == PaymentIntentStatus.PARTIALLY_REFUNDED
    assert intent.refunded_amount == 2_000

    with pytest.raises(InvalidAmount):
        intent.apply_refund(3_001)

    intent.apply_refund(3_000)
    assert intent.status == PaymentIntentStatus.REFUNDED


def test_refund_state_is_final_after_success():
    refund = Refund(
        payment_intent_id=uuid.uuid4(),
        payment_attempt_id=uuid.uuid4(),
        money=Money(1_000),
        idempotency_key="refund-1",
        request_fingerprint="fingerprint",
    )
    refund.transition_to(RefundStatus.PROCESSING)
    refund.transition_to(RefundStatus.SUCCEEDED)
    with pytest.raises(InvalidStateTransition):
        refund.transition_to(RefundStatus.FAILED)
