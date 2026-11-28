"""Ticket 023: refunds stuck in processing are read back and finished by the worker."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from payfund_app.modules.payments.application.errors import ProcessorCallUncertain
from payfund_app.modules.payments.application.ports import (
    PaymentDirection,
    ProcessorCapabilities,
    RefundResult,
)
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
    Refund,
    RefundStatus,
)
from payfund_app.modules.payments.infra.models import PaymentOutboxRecord, RefundRecord
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
    RefundRepository,
)
from payfund_app.ops import maintenance


# -- Paystack adapter: GET /refund/{id} ------------------------------------------------


def _paystack(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.paystack.test")
    return PaystackPaymentProcessor(
        secret_key="sk_test_secret", base_url="https://api.paystack.test", client=client
    )


def test_paystack_verify_refund_reads_final_state_and_amount():
    def handler(request):
        assert request.method == "GET" and request.url.path == "/refund/15581137"
        return httpx.Response(
            200,
            json={"status": True, "data": {"id": 15581137, "status": "processed", "amount": 40_000, "currency": "XOF"}},
        )

    result = _paystack(handler).verify_refund("15581137")

    assert result.status == RefundStatus.SUCCEEDED
    assert result.amount == 400  # Paystack XOF amounts are in subunits (x100)
    assert result.currency == "XOF"


@pytest.mark.parametrize("provider_status", ["pending", "processing", "needs-attention"])
def test_paystack_unfinished_refund_keeps_waiting(provider_status):
    def handler(request):
        return httpx.Response(200, json={"status": True, "data": {"id": 1, "status": provider_status}})

    assert _paystack(handler).verify_refund("1").status == RefundStatus.PROCESSING


def test_paystack_failed_lookup_is_not_a_failed_refund():
    result = _paystack(lambda r: httpx.Response(404, json={"status": False})).verify_refund("1")

    assert result.status == RefundStatus.PROCESSING
    assert result.provider_status == "http_404"


def test_paystack_refund_lookup_transport_error_is_uncertain():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ProcessorCallUncertain):
        _paystack(handler).verify_refund("1")


# -- Worker reconciliation ------------------------------------------------------------


class _FakePaystack:
    name = "paystack"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset({PaymentDirection.COLLECTION, PaymentDirection.REFUND}),
        refund_verification=True,
    )

    def __init__(self, result):
        self.result = result
        self.calls = []

    def verify_refund(self, provider_reference):
        self.calls.append(provider_reference)
        return self.result


class _CallbackOnly:
    """Stands in for PawaPay: refunds finish by callback, no read-back."""

    name = "pawapay"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset({PaymentDirection.COLLECTION, PaymentDirection.REFUND}),
    )


def _processing_refund(session, *, processor="paystack", amount=400, age_minutes=10):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference=f"ride:{uuid.uuid4()}",
        money=Money(1_000, "XOF"),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="f" * 64,
        status=PaymentIntentStatus.SUCCEEDED,
    )
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor=processor,
        money=intent.money,
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        provider_reference=f"{processor}-{uuid.uuid4()}",
    )
    refund = Refund(
        payment_intent_id=intent.id,
        payment_attempt_id=attempt.id,
        money=Money(amount, "XOF"),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="r" * 64,
        status=RefundStatus.PROCESSING,
        provider_reference=str(uuid.uuid4().int % 10**9),
        provider_status="pending",
    )
    PaymentIntentRepository(session).add(intent)
    PaymentAttemptRepository(session).add(attempt)
    RefundRepository(session).add(refund, client_id="diddigo", processor=processor)
    session.flush()
    session.execute(
        update(RefundRecord)
        .where(RefundRecord.id == refund.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=age_minutes))
    )
    session.commit()
    return intent, refund


def _use_registry(monkeypatch, *processors):
    registry = ProcessorRegistry()
    for processor in processors:
        registry.register(processor)
    monkeypatch.setattr(maintenance, "get_processor_registry", lambda: registry)


def _refund_events(session):
    return session.scalar(
        select(func.count()).select_from(PaymentOutboxRecord).where(
            PaymentOutboxRecord.event_type == "diddipay.refund.succeeded.v1"
        )
    )


def test_stuck_paystack_refund_is_finished_once(monkeypatch, session):
    intent, refund = _processing_refund(session)
    fake = _FakePaystack(
        RefundResult(provider_reference=refund.provider_reference, status=RefundStatus.SUCCEEDED,
                     provider_status="processed", amount=400, currency="XOF")
    )
    _use_registry(monkeypatch, fake)

    first = maintenance.reconcile_pending_refunds(session)
    second = maintenance.reconcile_pending_refunds(session, minimum_age_seconds=0)

    assert (first.scanned, first.succeeded) == (1, 1)
    assert second.scanned == 0  # no longer processing
    session.expire_all()
    assert session.get(RefundRecord, refund.id).status == "succeeded"
    updated = PaymentIntentRepository(session).get(intent.id)
    assert updated.refunded_amount == 400
    assert updated.status == PaymentIntentStatus.PARTIALLY_REFUNDED
    assert _refund_events(session) == 1


def test_refund_amount_mismatch_is_not_applied(monkeypatch, session):
    intent, refund = _processing_refund(session, amount=400)
    _use_registry(
        monkeypatch,
        _FakePaystack(RefundResult(provider_reference=refund.provider_reference, status=RefundStatus.SUCCEEDED,
                                   provider_status="processed", amount=999, currency="XOF")),
    )

    result = maintenance.reconcile_pending_refunds(session)

    assert result.pending == 1
    session.expire_all()
    assert session.get(RefundRecord, refund.id).status == "processing"
    assert PaymentIntentRepository(session).get(intent.id).refunded_amount == 0


def test_young_and_callback_only_refunds_are_not_scanned(monkeypatch, session):
    _processing_refund(session, age_minutes=1)  # younger than 5 minutes
    _processing_refund(session, processor="pawapay")  # finished by callback
    fake = _FakePaystack(RefundResult(provider_reference="x", status=RefundStatus.SUCCEEDED))
    _use_registry(monkeypatch, fake, _CallbackOnly())

    result = maintenance.reconcile_pending_refunds(session)

    assert result.scanned == 0
    assert fake.calls == []


def test_unfinished_refund_stays_processing(monkeypatch, session):
    _, refund = _processing_refund(session)
    _use_registry(
        monkeypatch,
        _FakePaystack(RefundResult(provider_reference=refund.provider_reference, status=RefundStatus.PROCESSING,
                                   provider_status="pending")),
    )

    result = maintenance.reconcile_pending_refunds(session)

    assert (result.scanned, result.pending) == (1, 1)
    session.expire_all()
    assert session.get(RefundRecord, refund.id).status == "processing"
