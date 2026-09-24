import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from payfund_app.core.errors import BadRequest, Conflict, Unauthenticated
from payfund_app.core.security import ServicePrincipal
from payfund_app.main import app
from payfund_app.modules.payments.application.backoffice import (
    BackofficePayment,
    BackofficePaymentQueries,
)
from payfund_app.modules.payments.application.backoffice_commands import (
    BackofficeCommand,
    BackofficeCommandService,
)
from payfund_app.modules.payments.presentation import backoffice_router


BACKOFFICE_MANIFEST = (
    Path(__file__).parents[1]
    / "docs"
    / "manifests"
    / "diddipay-backoffice-v1.json"
)


def _payment():
    now = datetime.now(UTC)
    return BackofficePayment(
        id=uuid.uuid4(),
        client_id="diddigo",
        business_reference="ride:42",
        amount=5000,
        currency="XOF",
        status="processing",
        refunded_amount=0,
        created_at=now,
        updated_at=now,
    )


def test_backoffice_queries_preserve_bounded_page_contract():
    class Repository:
        def list(self, **kwargs):
            assert kwargs == {"page": 2, "page_size": 20, "status": "processing"}
            return [_payment()], 21

        def get(self, payment_intent_id):
            return None

    page = BackofficePaymentQueries(Repository()).list(
        page=2, page_size=20, status="processing"
    )
    assert page.page == 2
    assert page.page_size == 20
    assert page.total == 21
    assert len(page.items) == 1


def test_backoffice_routes_are_documented_and_fail_closed():
    paths = app.openapi()["paths"]
    base = "/payfund/v1/internal/backoffice/payments"
    assert base in paths
    assert f"{base}/{{payment_intent_id}}" in paths
    assert f"{base}/{{payment_intent_id}}/callbacks/{{event_id}}/retry" in paths
    assert f"{base}/{{payment_intent_id}}/settlements" in paths
    assert "/payfund/v1/internal/backoffice/commands/{command_id}" in paths
    assert "/payfund/v1/internal/backoffice/capabilities" in paths
    response = TestClient(app).get(base)
    assert response.status_code == 401


def test_backoffice_manifest_matches_openapi_and_security_contract():
    manifest = json.loads(BACKOFFICE_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["contract_version"] == "backoffice.v1"
    assert manifest["module"] == "diddipay"
    [wallet] = manifest["pro_capabilities"]
    assert wallet["service"] == "diddipay"
    assert wallet["capability_type"] == "wallet"
    assert wallet["projection_authorizes_financial_operations"] is False
    assert wallet["financial_operations_allowed"] == []
    assert "balance_read" in wallet["forbidden_by_projection"]
    assert "payment_authorization" in wallet["forbidden_by_projection"]

    openapi_paths = app.openapi()["paths"]
    expected_names = {
        "list_payments",
        "get_payment_detail",
        "retry_payment_callback",
        "record_payment_settlement",
    }
    commands = manifest["commands"]
    assert {command["name"] for command in commands} == expected_names

    for command in commands:
        method = command["method"].lower()
        assert command["path"] in openapi_paths
        assert method in openapi_paths[command["path"]]
        assert command["execution_mode"] in {"interactive", "command"}
        assert isinstance(command["input_fields"], list)

        if command["permission"] == "read":
            assert command["service_scope"] == "diddipay:operations:read"
            assert command["requires_reason"] is False
            assert command["requires_idempotency"] is False
        else:
            assert command["permission"] == "write"
            assert command["service_scope"] == "diddipay:operations:write"
            assert command["requires_reason"] is True
            assert command["requires_idempotency"] is True
            assert {field["name"] for field in command["input_fields"]} >= {"reason"}


def test_backoffice_capabilities_are_versioned(monkeypatch):
    monkeypatch.setattr(
        backoffice_router,
        "get_settings",
        lambda: SimpleNamespace(
            backoffice_read_scope="diddipay:operations:read",
            backoffice_command_scope="diddipay:operations:write",
        ),
    )
    result = backoffice_router.backoffice_capabilities(
        ServicePrincipal("backoffice", "service:backoffice", frozenset())
    )
    assert result.contract_version == "backoffice.v1"
    assert result.commands == ["retry_callback", "record_settlement"]
    assert "Idempotency-Key" in result.command_headers
    [wallet] = result.pro_capabilities
    assert wallet.service == "diddipay"
    assert wallet.capability_type == "wallet"
    assert wallet.projection_authorizes_financial_operations is False
    assert wallet.financial_operations_allowed == []
    assert "pin_validation" in wallet.forbidden_by_projection
    assert "withdrawal" in wallet.forbidden_by_projection


def test_backoffice_reader_uses_scoped_service_identity(monkeypatch):
    settings = SimpleNamespace(
        backoffice_client_id_set={"backoffice-staging-diddipay"},
        backoffice_audience="diddipay",
        backoffice_read_scope="diddipay:operations:read",
    )
    captured = {}

    def verify(token, **kwargs):
        captured.update(token=token, **kwargs)
        return ServicePrincipal(
            client_id="backoffice-staging-diddipay",
            subject="service:backoffice",
            scopes=frozenset({"diddipay:operations:read"}),
        )

    monkeypatch.setattr(backoffice_router, "get_settings", lambda: settings)
    monkeypatch.setattr(backoffice_router, "decode_service_token", verify)
    principal = backoffice_router.require_backoffice_reader(
        "Bearer token", "backoffice-staging-diddipay"
    )
    assert principal.subject == "service:backoffice"
    assert captured["required_scopes"] == {"diddipay:operations:read"}


def test_backoffice_reader_rejects_unprovisioned_surface(monkeypatch):
    monkeypatch.setattr(
        backoffice_router,
        "get_settings",
        lambda: SimpleNamespace(backoffice_client_id_set=set()),
    )
    with pytest.raises(Unauthenticated):
        backoffice_router.require_backoffice_reader(None, None)


def test_backoffice_command_replay_does_not_repeat_side_effect():
    target_id = uuid.uuid4()
    stored = []

    class Repository:
        def find_replay(self, **kwargs):
            return stored[0] if stored else None

        def create(self, **values):
            command = BackofficeCommand(
                id=uuid.uuid4(), status="processing", result={}, **values
            )
            stored.append(command)
            return command

        def complete(self, command, result):
            completed = BackofficeCommand(
                **{
                    **{field: getattr(command, field) for field in command.__dataclass_fields__},
                    "status": "completed",
                    "result": result,
                }
            )
            stored[0] = completed
            return completed

    class Operations:
        calls = 0

        def retry_callback(self, **kwargs):
            self.calls += 1
            return {"delivery_status": "pending"}

    operations = Operations()
    service = BackofficeCommandService(Repository(), operations)
    values = {
        "client_id": "backoffice",
        "command_id": "cmd-12345678",
        "actor_user_id": str(uuid.uuid4()),
        "idempotency_key": "idem-12345678",
        "action": "payment.callback.retry",
        "target_type": "payment_intent",
        "target_id": target_id,
        "reason": "Relancer après correction du callback DiddiGo",
        "payload": {"event_id": str(uuid.uuid4())},
    }
    first = service.execute(**values)
    replay = service.execute(**values)
    assert first.id == replay.id
    assert operations.calls == 1


def test_backoffice_command_rejects_divergent_replay():
    class Repository:
        def find_replay(self, **kwargs):
            return BackofficeCommand(
                id=uuid.uuid4(),
                client_id="backoffice",
                command_id="cmd-12345678",
                actor_user_id=str(uuid.uuid4()),
                idempotency_key="idem-12345678",
                action="payment.settlement.record",
                target_type="payment_intent",
                target_id=uuid.uuid4(),
                reason="Original",
                request_fingerprint="different",
                status="completed",
                result={},
            )

    with pytest.raises(Conflict) as error:
        BackofficeCommandService(Repository(), object()).execute(
            client_id="backoffice",
            command_id="cmd-12345678",
            actor_user_id=str(uuid.uuid4()),
            idempotency_key="idem-12345678",
            action="payment.settlement.record",
            target_type="payment_intent",
            target_id=uuid.uuid4(),
            reason="Changed",
            payload={"amount": 100, "settlement_reference": "set-1"},
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_backoffice_command_requires_matching_idempotency_headers():
    body = backoffice_router.RetryCallbackBody(
        reason="Relance contrôlée", idempotency_key="body-key-123"
    )
    with pytest.raises(BadRequest) as error:
        backoffice_router._validate_command_headers(
            body, "actor-id", "command-id", "header-key-123"
        )
    assert error.value.code == "IDEMPOTENCY_KEY_MISMATCH"
