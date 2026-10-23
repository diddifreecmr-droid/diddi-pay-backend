import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from payfund_app.core.errors import Unauthenticated
from payfund_app.main import app
from payfund_app.modules.payments.application.support_context import (
    SupportAttemptContext,
    SupportPaymentContext,
    SupportPaymentContextQueries,
)
from payfund_app.modules.payments.presentation import support_router
from payfund_app.modules.payments.infra.callback_delivery import HttpSignedCallbackSender
from payfund_app.modules.payments.infra.callback_delivery import RedisStreamEventSender
from payfund_app.modules.payments.application.ports import CallbackTarget
import httpx


def _settings(**overrides):
    values = {
        "support_audience": "diddipay",
        "support_client_id_set": {"diddisupport-staging"},
        "support_context_scope": "diddipay:support-context:read",
        "support_refund_scope": "diddipay:support-actions:refund-request",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_support_context_route_is_documented_and_fail_closed():
    path = "/payfund/v1/internal/support/payment/{payment_intent_id}/context"
    assert path in app.openapi()["paths"]
    response = TestClient(app).get(path.format(payment_intent_id=uuid.uuid4()))
    assert response.status_code == 401
    assert "/payfund/v1/internal/support/refund-requests" in app.openapi()["paths"]
    assert "/payfund/v1/internal/support/refund-requests/{action_id}" in app.openapi()["paths"]


def test_support_reader_requires_configured_allowlist(monkeypatch):
    monkeypatch.setattr(support_router, "get_settings", lambda: _settings(support_client_id_set=set()))
    with pytest.raises(Unauthenticated):
        support_router.require_support_reader(None, None)


def test_support_reader_uses_diddipay_audience_and_dedicated_scope(monkeypatch):
    monkeypatch.setattr(support_router, "get_settings", lambda: _settings())
    captured = {}

    def verify(token, **kwargs):
        captured.update(token=token, **kwargs)
        return SimpleNamespace(client_id="diddisupport-staging", scopes=frozenset())

    monkeypatch.setattr(support_router, "decode_service_token", verify)
    support_router.require_support_reader("Bearer signed-token", "diddisupport-staging")
    assert captured == {
        "token": "signed-token",
        "audience": "diddipay",
        "client_id_header": "diddisupport-staging",
        "required_scopes": {"diddipay:support-context:read"},
        "allowed_client_ids": {"diddisupport-staging"},
    }


def test_support_refunder_uses_dedicated_refund_scope(monkeypatch):
    monkeypatch.setattr(support_router, "get_settings", lambda: _settings())
    captured = {}

    def verify(token, **kwargs):
        captured.update(token=token, **kwargs)
        return SimpleNamespace(client_id="diddisupport-staging", scopes=frozenset())

    monkeypatch.setattr(support_router, "decode_service_token", verify)
    support_router.require_support_refunder("Bearer signed-token", "diddisupport-staging")
    assert captured["required_scopes"] == {"diddipay:support-actions:refund-request"}


def test_support_projection_contains_only_the_safe_payment_fields():
    now = datetime.now(UTC)
    payment_id = uuid.uuid4()
    context = SupportPaymentContext(
        payment_intent_id=payment_id,
        business_reference="ride:opaque-42",
        payment_status="succeeded",
        amount=5000,
        currency="XOF",
        refunded_amount=0,
        refund_status="none",
        latest_attempt=SupportAttemptContext(
            status="succeeded",
            provider_status="completed",
            failure_code=None,
            occurred_at=now,
        ),
        updated_at=now,
    )
    result = SupportPaymentContextQueries(
        SimpleNamespace(get=lambda _: context)
    ).get(payment_id)
    payload = json.loads(
        support_router.SupportPaymentContextResponse.model_validate(
            result, from_attributes=True
        ).model_dump_json()
    )
    assert payload["payment_intent_id"] == str(payment_id)
    assert "payer_user_id" not in payload
    assert "payee_user_id" not in payload
    assert "provider_reference" not in payload
    assert "metadata" not in payload


def test_refund_result_callback_propagates_request_id():
    captured = {}

    def handler(request):
        captured.update(dict(request.headers))
        return httpx.Response(204)

    sender = HttpSignedCallbackSender(httpx.Client(transport=httpx.MockTransport(handler)))
    sender.send(
        CallbackTarget("https://support.example/events", "a-secret-with-16-chars"),
        event_id=uuid.uuid4(),
        event_type="diddipay.refund.succeeded.v1",
        occurred_at=datetime.now(UTC),
        payload={"request_id": "req-123", "status": "succeeded"},
    )
    assert captured["x-request-id"] == "req-123"


def test_redis_stream_sender_writes_versioned_envelope(monkeypatch):
    captured = {}

    class FakeRedis:
        def xadd(self, stream, fields, **kwargs):
            captured.update(stream=stream, fields=fields, kwargs=kwargs)

    monkeypatch.setattr(
        "payfund_app.modules.payments.infra.callback_delivery.redis.Redis.from_url",
        lambda *args, **kwargs: FakeRedis(),
    )
    sender = RedisStreamEventSender("redis://redis:6379/0")
    sender.send(
        CallbackTarget("diddipay.payment.events.v1", ""),
        event_id=uuid.uuid4(),
        event_type="diddipay.refund.succeeded.v1",
        occurred_at=datetime.now(UTC),
        payload={"status": "succeeded"},
    )
    assert captured["stream"] == "diddipay.payment.events.v1"
    assert '"type":"diddipay.refund.succeeded.v1"' in captured["fields"]["event"]
