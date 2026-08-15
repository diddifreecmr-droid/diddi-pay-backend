import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    NextAction,
    NextActionType,
    PaymentAttempt,
    PaymentIntent,
)
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
    ProviderEventRepository,
)


def make_intent(*, key: str = "ride-42") -> PaymentIntent:
    return PaymentIntent(
        client_id="diddigo",
        business_reference="ride:42",
        payer_user_id=uuid.uuid4(),
        payee_user_id=uuid.uuid4(),
        money=Money(5_000),
        idempotency_key=key,
        request_fingerprint="a" * 64,
        metadata={"ride_id": "42"},
    )


def test_intent_round_trip_keeps_business_and_idempotency_data(session):
    repository = PaymentIntentRepository(session)
    intent = make_intent()
    repository.add(intent)
    session.commit()

    loaded = repository.get(intent.id)
    assert loaded is not None
    assert loaded.money == Money(5_000)
    assert loaded.business_reference == "ride:42"
    assert loaded.metadata == {"ride_id": "42"}
    assert repository.get_by_idempotency("diddigo", "ride-42") == loaded


def test_idempotency_key_is_scoped_to_client(session):
    repository = PaymentIntentRepository(session)
    first = make_intent(key="shared-key")
    second = make_intent(key="shared-key")
    second.client_id = "diddifund"
    repository.add(first)
    repository.add(second)
    session.commit()

    assert repository.get_by_idempotency("diddigo", "shared-key").id == first.id
    assert repository.get_by_idempotency("diddifund", "shared-key").id == second.id


def test_database_rejects_duplicate_client_idempotency_key(session):
    repository = PaymentIntentRepository(session)
    repository.add(make_intent(key="duplicate"))
    with pytest.raises(IntegrityError):
        repository.add(make_intent(key="duplicate"))


def test_attempt_round_trip_preserves_generic_next_action(session):
    intents = PaymentIntentRepository(session)
    attempts = PaymentAttemptRepository(session)
    intent = make_intent()
    intents.add(intent)
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor="paystack",
        channel="mobile_money",
        network="orange",
        money=intent.money,
        attempt_number=1,
        status=AttemptStatus.REQUIRES_ACTION,
        provider_reference="ps-42",
        next_action=NextAction(NextActionType.REDIRECT, url="https://checkout.test/42"),
    )
    attempts.add(attempt)
    session.commit()

    loaded = attempts.get_by_provider_reference("paystack", "ps-42")
    assert loaded is not None
    assert loaded.next_action == attempt.next_action
    assert loaded.network == "orange"


def test_database_allows_only_one_successful_attempt_per_intent(session):
    intents = PaymentIntentRepository(session)
    attempts = PaymentAttemptRepository(session)
    intent = make_intent()
    intents.add(intent)
    attempts.add(
        PaymentAttempt(
            payment_intent_id=intent.id,
            processor="paystack",
            money=intent.money,
            attempt_number=1,
            status=AttemptStatus.SUCCEEDED,
            provider_reference="ps-1",
        )
    )
    with pytest.raises(IntegrityError):
        attempts.add(
            PaymentAttempt(
                payment_intent_id=intent.id,
                processor="paystack",
                money=intent.money,
                attempt_number=2,
                status=AttemptStatus.SUCCEEDED,
                provider_reference="ps-2",
            )
        )


def test_provider_event_is_unique_per_processor(session):
    events = ProviderEventRepository(session)
    events.add(
        processor="paystack",
        event_key="charge.success:ps-42",
        event_type="charge.success",
        payload_hash="b" * 64,
        payload={"reference": "ps-42"},
    )
    assert events.get("paystack", "charge.success:ps-42") is not None
