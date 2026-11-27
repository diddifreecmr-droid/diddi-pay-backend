import uuid

import pytest

from payfund_app.modules.payments.application.errors import (
    PaymentNotFound,
    PaymentVerificationFailed,
)
from payfund_app.modules.payments.application.use_cases import PaymentUseCases
from payfund_app.modules.payments.domain.entities import (
    Money,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.presentation.schemas import VerifyPaymentIntentRequest


class _IntentRepository:
    def __init__(self, intent):
        self.intent = intent

    def get(self, intent_id, *, for_update=False):
        return self.intent if self.intent and self.intent.id == intent_id else None


class _AttemptRepository:
    def list_for_intent(self, intent_id):
        return []


def _use_cases(intent):
    return PaymentUseCases(
        _IntentRepository(intent),
        _AttemptRepository(),
        processors=None,
        uow=None,
    )


def _intent(
    *,
    owner="diddishop-staging-diddipay",
    status=PaymentIntentStatus.SUCCEEDED,
    amount=12000,
):
    return PaymentIntent(
        id=uuid.uuid4(),
        client_id=owner,
        business_reference="diddish:order:order-123",
        money=Money(amount, "XOF"),
        idempotency_key="idem-123",
        request_fingerprint="fingerprint",
        status=status,
    )


@pytest.mark.parametrize("owner", ["diddishop-staging-diddipay", "diddifood-staging"])
def test_verification_accepts_matching_succeeded_shop_or_food_intent(owner):
    intent = _intent(owner=owner)

    view = _use_cases(intent).verify(
        intent.id,
        allowed_owner_client_ids={"diddishop-staging-diddipay", "diddifood-staging"},
        expected_amount=12000,
        expected_currency="XOF",
        expected_business_reference="diddish:order:order-123",
    )

    assert view.intent.id == intent.id
    assert view.intent.status is PaymentIntentStatus.SUCCEEDED


def test_verification_hides_intents_owned_by_other_modules():
    intent = _intent(owner="diddigo-staging")

    with pytest.raises(PaymentNotFound):
        _use_cases(intent).verify(
            intent.id,
            allowed_owner_client_ids={"diddishop-staging-diddipay"},
            expected_amount=12000,
            expected_currency="XOF",
            expected_business_reference="diddish:order:order-123",
        )


def test_coverage_verification_accepts_a_payment_above_required_amount():
    intent = _intent(amount=5640)

    view = _use_cases(intent).verify_coverage(
        intent.id,
        allowed_owner_client_ids={"diddishop-staging-diddipay", "diddifood-staging"},
        required_amount=4140,
        expected_currency="XOF",
        expected_business_reference="diddish:order:order-123",
    )

    assert view.intent.money.amount == 5640
    assert view.intent.status is PaymentIntentStatus.SUCCEEDED


def test_coverage_verification_rejects_an_insufficient_payment():
    intent = _intent(amount=4000)

    with pytest.raises(PaymentVerificationFailed) as error:
        _use_cases(intent).verify_coverage(
            intent.id,
            allowed_owner_client_ids={"diddishop-staging-diddipay"},
            required_amount=4140,
            expected_currency="XOF",
            expected_business_reference="diddish:order:order-123",
        )

    assert error.value.reason == "payment_amount_insufficient"


def test_verification_request_keeps_exact_mode_as_the_default():
    request = VerifyPaymentIntentRequest(
        amount=5640,
        currency="XOF",
        business_reference="diddifood:order:order-123",
    )

    assert request.verification_mode == "exact"
    assert request.amount == 5640
    assert request.required_amount is None


def test_verification_request_requires_the_matching_amount_field_for_each_mode():
    coverage = VerifyPaymentIntentRequest(
        verification_mode="coverage",
        required_amount=4140,
        currency="XOF",
        business_reference="diddifood:order:order-123",
    )

    assert coverage.required_amount == 4140

    with pytest.raises(ValueError):
        VerifyPaymentIntentRequest(
            verification_mode="coverage",
            amount=5640,
            required_amount=4140,
            currency="XOF",
            business_reference="diddifood:order:order-123",
        )


@pytest.mark.parametrize(
    ("status", "amount", "currency", "business_reference", "reason"),
    [
        (PaymentIntentStatus.PROCESSING, 12000, "XOF", "diddish:order:order-123", "status_not_succeeded"),
        (PaymentIntentStatus.SUCCEEDED, 10000, "XOF", "diddish:order:order-123", "amount_mismatch"),
        (PaymentIntentStatus.SUCCEEDED, 12000, "XOF", "diddish:order:other", "business_reference_mismatch"),
    ],
)
def test_verification_rejects_business_mismatches(
    status, amount, currency, business_reference, reason
):
    intent = _intent(status=status)

    with pytest.raises(PaymentVerificationFailed) as error:
        _use_cases(intent).verify(
            intent.id,
            allowed_owner_client_ids={"diddishop-staging-diddipay"},
            expected_amount=amount,
            expected_currency=currency,
            expected_business_reference=business_reference,
        )

    assert error.value.reason == reason
