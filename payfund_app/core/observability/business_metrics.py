"""Bounded-cardinality metrics for the DiddiPay payment lifecycle."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from payfund_app.core.config import get_settings


PAYMENT_INTENTS_CREATED_TOTAL = Counter(
    "diddipay_payment_intents_created_total",
    "New provider-neutral payment intents created by DiddiPay.",
    ("processor", "status", "currency"),
)
PROVIDER_CALLS_TOTAL = Counter(
    "diddipay_provider_calls_total",
    "Calls made from DiddiPay to an external payment provider.",
    ("provider", "operation", "outcome"),
)
PROVIDER_CALL_DURATION_SECONDS = Histogram(
    "diddipay_provider_call_duration_seconds",
    "External payment provider call duration in seconds.",
    ("provider", "operation"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30),
)
WEBHOOK_EVENTS_TOTAL = Counter(
    "diddipay_webhook_events_total",
    "Provider webhook events received by outcome.",
    ("provider", "outcome"),
)
RECONCILIATION_RESULTS_TOTAL = Counter(
    "diddipay_reconciliation_results_total",
    "Payment reconciliation results by provider and outcome.",
    ("provider", "outcome"),
)
OUTBOX_DELIVERIES_TOTAL = Counter(
    "diddipay_outbox_deliveries_total",
    "Module callback delivery results.",
    ("outcome",),
)
OUTBOX_EVENTS = Gauge(
    "diddipay_outbox_events",
    "Current payment callback outbox records by state.",
    ("status",),
)

_PROVIDERS = frozenset(
    {"paystack", "sandbox", "orange_money", "mtn_momo", "wave", "moov", "unknown"}
)
_INTENT_STATUSES = frozenset(
    {"pending", "requires_action", "processing", "succeeded", "failed", "cancelled"}
)
_CURRENCIES = frozenset({"xof"})
_PROVIDER_OPERATIONS = frozenset({"initialize", "verify", "refund"})
_PROVIDER_OUTCOMES = frozenset({"returned", "http_error", "transport_error"})
_WEBHOOK_OUTCOMES = frozenset({"processed", "duplicate", "ignored", "failed", "rejected"})
_RECONCILIATION_OUTCOMES = frozenset({"succeeded", "failed", "pending", "mismatched"})
_DELIVERY_OUTCOMES = frozenset({"delivered", "retried", "unavailable"})
_OUTBOX_STATUSES = frozenset({"pending", "delivering", "delivered", "dead_letter"})


def _label(value: object, allowed: frozenset[str]) -> str:
    normalized = str(value).lower()
    return normalized if normalized in allowed else "unknown"


def observe_payment_intent_created(*, processor: str, status: str, currency: str) -> None:
    if not get_settings().metrics_enabled:
        return
    PAYMENT_INTENTS_CREATED_TOTAL.labels(
        _label(processor, _PROVIDERS),
        _label(status, _INTENT_STATUSES),
        _label(currency.upper(), _CURRENCIES),
    ).inc()


def observe_provider_call(
    *, provider: str, operation: str, outcome: str, duration_seconds: float
) -> None:
    if not get_settings().metrics_enabled:
        return
    provider_label = _label(provider, _PROVIDERS)
    operation_label = _label(operation, _PROVIDER_OPERATIONS)
    PROVIDER_CALLS_TOTAL.labels(
        provider_label,
        operation_label,
        _label(outcome, _PROVIDER_OUTCOMES),
    ).inc()
    PROVIDER_CALL_DURATION_SECONDS.labels(provider_label, operation_label).observe(
        duration_seconds
    )


def observe_webhook(*, provider: str, outcome: str) -> None:
    if get_settings().metrics_enabled:
        WEBHOOK_EVENTS_TOTAL.labels(
            _label(provider, _PROVIDERS), _label(outcome, _WEBHOOK_OUTCOMES)
        ).inc()


def observe_reconciliation_summary(
    *, provider: str, succeeded: int, failed: int, pending: int, mismatched: int = 0
) -> None:
    if not get_settings().metrics_enabled:
        return
    provider_label = _label(provider, _PROVIDERS)
    for outcome, count in {
        "succeeded": succeeded,
        "failed": failed,
        "pending": pending,
        "mismatched": mismatched,
    }.items():
        if count > 0:
            RECONCILIATION_RESULTS_TOTAL.labels(provider_label, outcome).inc(count)


def observe_delivery_summary(*, delivered: int, retried: int, unavailable: int) -> None:
    if not get_settings().metrics_enabled:
        return
    for outcome, count in {
        "delivered": delivered,
        "retried": retried,
        "unavailable": unavailable,
    }.items():
        if count > 0:
            OUTBOX_DELIVERIES_TOTAL.labels(_label(outcome, _DELIVERY_OUTCOMES)).inc(count)


def set_outbox_status_counts(counts: dict[str, int]) -> None:
    if not get_settings().metrics_enabled:
        return
    for status in _OUTBOX_STATUSES:
        OUTBOX_EVENTS.labels(status).set(max(0, counts.get(status, 0)))
