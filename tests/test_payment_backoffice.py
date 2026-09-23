from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from payfund_app.core.errors import Unauthenticated
from payfund_app.core.security import ServicePrincipal
from payfund_app.main import app
from payfund_app.modules.payments.application.backoffice import (
    BackofficePayment,
    BackofficePaymentQueries,
)
from payfund_app.modules.payments.presentation import backoffice_router


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
    response = TestClient(app).get(base)
    assert response.status_code == 401


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
