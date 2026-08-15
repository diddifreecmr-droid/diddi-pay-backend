import hashlib
import hmac
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from payfund_app.modules.payments.application.deliveries import (
    PaymentEventDeliveryUseCases,
)
from payfund_app.modules.payments.application.ports import CallbackTarget
from payfund_app.modules.payments.infra.callback_delivery import HttpSignedCallbackSender
from payfund_app.modules.payments.infra.repositories import PaymentOutboxRepository
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.core.config import Settings


def test_payment_event_delivery_is_signed_and_marked_delivered(session):
    repo = PaymentOutboxRepository(session)
    row = repo.enqueue(
        client_id="diddigo",
        event_type="payment.succeeded",
        aggregate_id=uuid.uuid4(),
        payload={"payment_intent_id": "pi_1", "status": "succeeded"},
    )
    session.commit()
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = PaymentEventDeliveryUseCases(
        repo,
        SqlAlchemyUnitOfWork(session),
        HttpSignedCallbackSender(client),
        {
            "diddigo": CallbackTarget(
                "https://go.test/webhooks/diddipay", "secret"
            )
        },
    ).run()

    request = captured["request"]
    envelope = json.loads(request.content)
    expected_signature = hmac.new(
        b"secret", request.content, hashlib.sha256
    ).hexdigest()
    assert result.delivered == 1
    assert row.status == "delivered"
    assert request.headers["X-DiddiPay-Event-ID"] == str(row.id)
    assert request.headers["X-DiddiPay-Signature"] == expected_signature
    assert envelope["id"] == str(row.id)
    assert envelope["type"] == "payment.succeeded"
    assert envelope["data"]["payment_intent_id"] == "pi_1"
    assert envelope["occurred_at"]


def test_failed_delivery_is_scheduled_for_retry(session):
    repo = PaymentOutboxRepository(session)
    row = repo.enqueue(
        client_id="diddigo",
        event_type="payment.succeeded",
        aggregate_id=uuid.uuid4(),
        payload={"status": "succeeded"},
    )
    session.commit()
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))

    result = PaymentEventDeliveryUseCases(
        repo,
        SqlAlchemyUnitOfWork(session),
        HttpSignedCallbackSender(client),
        {
            "diddigo": CallbackTarget(
                "https://go.test/webhooks/diddipay", "secret"
            )
        },
    ).run()

    assert result.retried == 1
    assert row.status == "pending"
    assert row.attempts == 1


def test_missing_callback_target_is_reported_without_losing_event(session):
    repo = PaymentOutboxRepository(session)
    row = repo.enqueue(
        client_id="unknown-module",
        event_type="payment.succeeded",
        aggregate_id=uuid.uuid4(),
        payload={"status": "succeeded"},
    )
    session.commit()

    def unexpected_request(_):
        raise AssertionError("callback sender must not run without a configured target")

    sender = HttpSignedCallbackSender(
        httpx.Client(transport=httpx.MockTransport(unexpected_request))
    )

    result = PaymentEventDeliveryUseCases(
        repo,
        SqlAlchemyUnitOfWork(session),
        sender,
        {},
    ).run()

    assert result.unavailable == 1
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.last_error == "callback target not configured"


def test_payment_callback_targets_are_loaded_from_json_environment(monkeypatch):
    monkeypatch.setenv(
        "PAYMENT_CALLBACK_TARGETS",
        json.dumps(
            {
                "diddigo": {
                    "url": "https://go.diddifree.com/internal/webhooks/diddipay",
                    "secret": "a-secret-with-16-chars",
                }
            }
        ),
    )

    settings = Settings(_env_file=None)

    target = settings.payment_callback_targets["diddigo"]
    assert str(target.url) == "https://go.diddifree.com/internal/webhooks/diddipay"
    assert target.secret == "a-secret-with-16-chars"


def test_claim_prevents_parallel_delivery_and_recovers_stale_worker(session):
    repo = PaymentOutboxRepository(session)
    row = repo.enqueue(
        client_id="diddigo",
        event_type="payment.succeeded",
        aggregate_id=uuid.uuid4(),
        payload={"status": "succeeded"},
    )
    session.commit()

    assert repo.claim(limit=1) == [row]
    session.commit()
    assert row.status == "delivering"
    assert repo.claim(limit=1) == []

    row.locked_at -= timedelta(minutes=10)
    session.commit()
    assert repo.claim(limit=1, lease_seconds=300) == [row]


def test_callback_configuration_rejects_insecure_external_http():
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            _env_file=None,
            payment_callback_targets={
                "diddigo": {
                    "url": "http://example.com/webhooks/diddipay",
                    "secret": "a-secret-with-16-chars",
                }
            },
        )
