"""OBS-1 contract tests: correlation, safe logs, and protected metrics."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from payfund_app.core.config import get_settings
from payfund_app.core.observability.context import bind_request_id, reset_request_id
from payfund_app.core.observability.redaction import REDACTED, redact
from payfund_app.main import app
from payfund_app.shared_kernel.logging import build_log_payload


def test_redaction_removes_nested_secrets_and_personal_data() -> None:
    safe = redact(
        {
            "authorization": "Bearer visible-token",
            "customer": {
                "email": "awa@example.com",
                "phone_number": "+237600000000",
                "display_name": "Awa K.",
            },
            "provider_message": "credential sk_test_should-never-leak",
            "shipping_address": "Douala",
        }
    )

    assert safe["authorization"] == REDACTED
    assert safe["customer"]["email"] == REDACTED
    assert safe["customer"]["phone_number"] == REDACTED
    assert safe["customer"]["display_name"] == "Awa K."
    assert "sk_test_should-never-leak" not in safe["provider_message"]
    assert safe["shipping_address"] == "Douala"


def test_structured_log_envelope_includes_request_context(monkeypatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_ENVIRONMENT", "test")
    monkeypatch.setenv("OBSERVABILITY_RELEASE_SHA", "abc123")
    get_settings.cache_clear()
    token = bind_request_id("req-contract-test")
    try:
        payload = build_log_payload(
            "INFO",
            "payment.intent.created",
            {
                "request_id": "cannot-override-context",
                "payment_intent_id": "pi_123",
                "api_key": "must-not-leak",
            },
        )
    finally:
        reset_request_id(token)
        get_settings.cache_clear()

    assert payload["level"] == "info"
    assert payload["environment"] == "test"
    assert payload["release"] == "abc123"
    assert payload["request_id"] == "req-contract-test"
    assert payload["payment_intent_id"] == "pi_123"
    assert payload["api_key"] == REDACTED


def test_request_id_is_propagated_or_generated() -> None:
    client = TestClient(app)

    propagated = client.get(
        "/payfund/v1/health", headers={"X-Request-ID": "diddigo-req-42"}
    )
    generated = client.get("/payfund/v1/health", headers={"X-Request-ID": "invalid id"})

    assert propagated.status_code == 200
    assert propagated.headers["x-request-id"] == "diddigo-req-42"
    assert re.fullmatch(r"[0-9a-f]{32}", generated.headers["x-request-id"])


def test_metrics_are_protected_and_hidden_from_openapi(monkeypatch) -> None:
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "test-metrics-token-with-at-least-32-characters")
    get_settings.cache_clear()
    client = TestClient(app)

    missing = client.get("/internal/metrics")
    wrong = client.get(
        "/internal/metrics", headers={"Authorization": "Bearer wrong-token"}
    )
    allowed = client.get(
        "/internal/metrics",
        headers={
            "Authorization": "Bearer test-metrics-token-with-at-least-32-characters"
        },
    )
    schema = client.get("/payfund/v1/openapi.json").json()
    get_settings.cache_clear()

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    assert "diddipay_http_requests_total" in allowed.text
    assert "/internal/metrics" not in schema["paths"]


def test_metrics_use_route_templates_not_resource_ids(monkeypatch) -> None:
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("METRICS_TOKEN", "test-metrics-token-with-at-least-32-characters")
    get_settings.cache_clear()
    client = TestClient(app)
    intent_id = "76592027-9b26-4f3d-a85f-2381fa647268"

    client.get(f"/payfund/v1/payment-intents/{intent_id}")
    metrics = client.get(
        "/internal/metrics",
        headers={
            "Authorization": "Bearer test-metrics-token-with-at-least-32-characters"
        },
    ).text
    get_settings.cache_clear()

    assert intent_id not in metrics
    assert 'route="/payment-intents/{intent_id}"' in metrics
