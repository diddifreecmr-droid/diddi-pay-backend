from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from payfund_app.modules.payments.application.ports import ProviderResult
from payfund_app.modules.payments.application.processor_router import (
    ProcessorRoutingError,
)
from payfund_app.modules.payments.application.reconciliation import (
    PaymentReconciliationUseCases,
)
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)


class MemoryAttempts:
    def __init__(self, attempt):
        self.attempt = attempt

    def pending_for_reconciliation(self, **kwargs):
        return [self.attempt] if self.attempt.status == AttemptStatus.UNKNOWN else []

    def save(self, attempt):
        self.attempt = attempt


class MemoryIntents:
    def __init__(self, intent):
        self.intent = intent

    def get(self, intent_id, **kwargs):
        return self.intent

    def save(self, intent):
        self.intent = intent


class MemoryEvents:
    def add(self, **kwargs):
        return SimpleNamespace(**kwargs)

    def mark(self, *args, **kwargs):
        pass


class Processor:
    def verify_payment(self, reference):
        return ProviderResult(reference, AttemptStatus.SUCCEEDED, "success", amount=5000, currency="XOF", fee=100)


def test_fallback_emits_exactly_one_capture_and_module_event():
    intent = PaymentIntent("diddigo", "ride:123", Money(5000), str(uuid4()), "a" * 64)
    attempt = PaymentAttempt(intent.id, "paystack", intent.money, 1, status=AttemptStatus.UNKNOWN,
                             provider_reference="dpi_test", updated_at=datetime.now(UTC) - timedelta(minutes=10))
    intents, attempts = MemoryIntents(intent), MemoryAttempts(attempt)
    outbox_events, captures = [], []
    outbox = SimpleNamespace(enqueue=lambda **kwargs: outbox_events.append(kwargs))
    accounting = SimpleNamespace(record_capture=lambda *args, **kwargs: captures.append((args, kwargs)))
    use_cases = PaymentReconciliationUseCases(
        intents, attempts, MemoryEvents(), SimpleNamespace(get=lambda _: Processor()),
        SimpleNamespace(commit=lambda: None), outbox, accounting,
    )
    assert use_cases.run(minimum_age_seconds=0).succeeded == 1
    assert use_cases.run(minimum_age_seconds=0).scanned == 0
    assert intent.status == PaymentIntentStatus.SUCCEEDED
    assert outbox_events[0]["event_type"] == "payment.succeeded"
    assert outbox_events[0]["payload"]["business_reference"] == "ride:123"
    assert len(outbox_events) == len(captures) == 1
    assert captures[0][1]["fee"] == 100


def test_reconciliation_isolates_historical_unregistered_processor():
    intent = PaymentIntent("diddigo", "ride:legacy", Money(5000), str(uuid4()), "b" * 64)
    attempt = PaymentAttempt(
        intent.id,
        "sandbox",
        intent.money,
        1,
        status=AttemptStatus.UNKNOWN,
        provider_reference="sandbox_legacy",
        updated_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    commits = []

    def unavailable(_):
        raise ProcessorRoutingError("processor 'sandbox' is not registered")

    summary = PaymentReconciliationUseCases(
        MemoryIntents(intent),
        MemoryAttempts(attempt),
        MemoryEvents(),
        SimpleNamespace(get=unavailable),
        SimpleNamespace(commit=lambda: commits.append(True)),
    ).run(minimum_age_seconds=0)

    assert summary.scanned == 1
    assert summary.pending == 1
    assert summary.succeeded == 0
    assert commits == [True]
    assert intent.status == PaymentIntentStatus.PROCESSING
