"""OBS-1 contract tests: correlation, safe logs, and protected metrics."""

from __future__ import annotations

import json
import re
from logging.handlers import RotatingFileHandler

from fastapi.testclient import TestClient

from payfund_app.core.config import get_settings
from payfund_app.core.observability.context import bind_request_id, reset_request_id
from payfund_app.core.observability.redaction import REDACTED, redact
from payfund_app.main import app
from payfund_app.shared_kernel.logging import (
    build_log_payload,
    configure_logging,
    emit,
    logger,
)


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


def test_optional_file_logs_are_bounded_and_redacted(monkeypatch, tmp_path) -> None:
    path = tmp_path / "app.jsonl"
    monkeypatch.setenv("OBSERVABILITY_LOG_FILE", str(path))
    get_settings.cache_clear()
    try:
        logger.disabled = True
        configure_logging()
        configure_logging()
        assert logger.disabled is False
        handlers = [
            handler for handler in logger.handlers
            if getattr(handler, "_diddipay_file", None) == str(path)
        ]
        assert len(handlers) == 1
        assert isinstance(handlers[0], RotatingFileHandler)
        assert handlers[0].maxBytes == 10 * 1024 * 1024
        assert handlers[0].backupCount == 5
        emit("info", "payment.test", api_key="secret-value")
        handlers[0].flush()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["api_key"] == REDACTED
        assert "secret-value" not in lines[0]
    finally:
        for handler in logger.handlers[:]:
            if getattr(handler, "_diddipay_file", None) == str(path):
                logger.removeHandler(handler)
                handler.close()
        get_settings.cache_clear()


def test_request_id_is_propagated_or_generated() -> None:
    client = TestClient(app)

    propagated = client.get(
        "/payfund/v1/health", headers={"X-Request-ID": "diddigo-req-42"}
    )
    generated = client.get("/payfund/v1/health", headers={"X-Request-ID": "invalid id"})

    assert propagated.status_code == 200
    assert propagated.headers["x-request-id"] == "diddigo-req-42"
    assert re.fullmatch(r"[0-9a-f]{32}", generated.headers["x-request-id"])


def test_error_body_carries_the_same_request_id_as_the_response() -> None:
    response = TestClient(app).get(
        "/route-that-does-not-exist", headers={"X-Request-ID": "req-error-contract"}
    )

    assert response.status_code == 404
    assert response.headers["x-request-id"] == "req-error-contract"
    assert response.json()["error"]["request_id"] == "req-error-contract"


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
