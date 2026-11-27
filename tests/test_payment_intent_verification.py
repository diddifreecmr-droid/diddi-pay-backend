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


def _intent(*, owner="diddishop-staging-diddipay", status=PaymentIntentStatus.SUCCEEDED):
    return PaymentIntent(
        id=uuid.uuid4(),
        client_id=owner,
        business_reference="diddish:order:order-123",
        money=Money(12000, "XOF"),
        idempotency_key="idem-123",
        request_fingerprint="fingerprint",
        status=status,
    )


def test_verification_accepts_only_matching_succeeded_owned_intent():
    intent = _intent()

    view = _use_cases(intent).verify(
        intent.id,
        allowed_owner_client_ids={"diddishop-staging-diddipay"},
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
