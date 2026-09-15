"""OBS-2 business metrics remain useful without leaking unbounded identifiers."""

from __future__ import annotations

import httpx
from prometheus_client import REGISTRY, generate_latest

from payfund_app.core.config import get_settings
from payfund_app.core.observability.business_metrics import (
    observe_delivery_summary,
    observe_payment_intent_created,
    observe_provider_call,
    observe_reconciliation_summary,
    observe_webhook,
    set_outbox_status_counts,
)
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor


def _enable_metrics(monkeypatch) -> None:
    monkeypatch.setenv("METRICS_ENABLED", "true")
    get_settings.cache_clear()


def test_payment_lifecycle_metrics_are_exported(monkeypatch) -> None:
    _enable_metrics(monkeypatch)

    observe_payment_intent_created(
        processor="paystack", status="requires_action", currency="XOF"
    )
    observe_provider_call(
        provider="paystack",
        operation="initialize",
        outcome="returned",
        duration_seconds=0.125,
    )
    observe_webhook(provider="paystack", outcome="processed")
    observe_reconciliation_summary(
        provider="paystack", succeeded=2, failed=1, pending=3, mismatched=1
    )
    observe_delivery_summary(delivered=4, retried=2, unavailable=1)
    set_outbox_status_counts(
        {"pending": 5, "delivering": 1, "delivered": 9, "dead_letter": 2}
    )
    metrics = generate_latest().decode()
    get_settings.cache_clear()

    assert "diddipay_payment_intents_created_total" in metrics
    assert "diddipay_provider_calls_total" in metrics
    assert "diddipay_provider_call_duration_seconds" in metrics
    assert "diddipay_webhook_events_total" in metrics
    assert "diddipay_reconciliation_results_total" in metrics
    assert "diddipay_outbox_deliveries_total" in metrics
    assert 'diddipay_outbox_events{status="dead_letter"} 2.0' in metrics


def test_untrusted_metric_labels_collapse_to_unknown(monkeypatch) -> None:
    _enable_metrics(monkeypatch)
    untrusted = "customer-123456789"

    observe_provider_call(
        provider=untrusted,
        operation=untrusted,
        outcome=untrusted,
        duration_seconds=0.01,
    )
    observe_payment_intent_created(
        processor=untrusted,
        status=untrusted,
        currency=untrusted,
    )
    metrics = generate_latest().decode()
    get_settings.cache_clear()

    assert untrusted not in metrics
    assert 'operation="unknown",outcome="unknown",provider="unknown"' in metrics
    assert 'currency="unknown",processor="unknown",status="unknown"' in metrics


def test_paystack_adapter_records_provider_call(monkeypatch) -> None:
    _enable_metrics(monkeypatch)
    labels = {
        "provider": "paystack",
        "operation": "verify",
        "outcome": "returned",
    }
    before = REGISTRY.get_sample_value("diddipay_provider_calls_total", labels) or 0
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": True,
                    "data": {
                        "reference": "dpi_obs2",
                        "status": "success",
                        "amount": 5_000,
                        "currency": "XOF",
                    },
                },
            )
        ),
        base_url="https://api.paystack.test",
    )
    processor = PaystackPaymentProcessor(secret_key="test-key", client=client)

    processor.verify_payment("dpi_obs2")
    after = REGISTRY.get_sample_value("diddipay_provider_calls_total", labels)
    get_settings.cache_clear()

    assert after == before + 1
