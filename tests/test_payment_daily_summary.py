from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from payfund_app.core.errors import Unauthenticated
from payfund_app.core.security import ServicePrincipal
from payfund_app.main import app
from payfund_app.modules.payments.application.daily_summary import (
    PaymentDailySummaryUseCases,
    day_bounds,
)
from payfund_app.modules.payments.presentation import summary_router


def test_business_day_uses_half_open_abidjan_window():
    assert day_bounds(date(2026, 9, 18)) == (
        datetime(2026, 9, 18, tzinfo=UTC),
        datetime(2026, 9, 19, tzinfo=UTC),
    )


def test_empty_day_is_real_zero():
    class Repository:
        def totals(self, start, end):
            assert start < end
            return {}

    result = PaymentDailySummaryUseCases(Repository()).get(date(2026, 9, 18))
    assert result.confirmed_payments_count == 0
    assert result.confirmed_payments_amount_xof == 0
    assert result.confirmed_refunds_count == 0
    assert result.confirmed_refunds_amount_xof == 0
    assert result.processor_fees_amount_xof == 0
    assert result.settlements_amount_xof == 0
    assert result.payouts_amount_xof == 0


def test_aggregates_capture_and_partial_refunds():
    class Repository:
        def totals(self, start, end):
            return {
                "capture": (2, 5000),
                "refund": (1, 1000),
                "processor_fee": (2, 125),
                "settlement": (1, 3500),
                "payout": (1, 900),
            }

    result = PaymentDailySummaryUseCases(Repository()).get(date(2026, 9, 18))
    assert (result.confirmed_payments_count, result.confirmed_payments_amount_xof) == (2, 5000)
    assert (result.confirmed_refunds_count, result.confirmed_refunds_amount_xof) == (1, 1000)
    assert result.processor_fees_amount_xof == 125
    assert (result.settlements_count, result.settlements_amount_xof) == (1, 3500)
    assert (result.payouts_count, result.payouts_amount_xof) == (1, 900)


def test_summary_auth_fails_closed_without_provisioned_client(monkeypatch):
    monkeypatch.setattr(summary_router, "get_settings", lambda: SimpleNamespace(payment_summary_client_id=""))
    with pytest.raises(Unauthenticated):
        summary_router.require_pilotage_service(None, None)


def test_summary_route_is_in_openapi_and_rejects_unauthenticated_requests():
    path = "/payfund/v1/internal/v1/payment-summary"
    assert path in app.openapi()["paths"]
    response = TestClient(app).get(path, params={"date": "2026-09-18"})
    assert response.status_code == 401

    pilotage_path = "/payfund/v1/internal/pilotage/daily-summary"
    assert pilotage_path in app.openapi()["paths"]
    response = TestClient(app).get(pilotage_path, params={"date": "2026-09-18"})
    assert response.status_code == 401


def test_pilotage_v1_response_is_stable_and_financially_explicit(monkeypatch):
    summary = SimpleNamespace(
        date=date(2026, 9, 18),
        timezone="Africa/Abidjan",
        confirmed_payments_count=2,
        confirmed_payments_amount_xof=5000,
        confirmed_refunds_count=1,
        confirmed_refunds_amount_xof=1000,
        processor_fees_amount_xof=125,
        settlements_count=1,
        settlements_amount_xof=3500,
        payouts_count=1,
        payouts_amount_xof=900,
        calculated_at=datetime(2026, 9, 19, tzinfo=UTC),
        source="payments.financial_journals",
    )
    monkeypatch.setattr(
        summary_router.PaymentDailySummaryUseCases,
        "get",
        lambda self, day: summary,
    )
    response = summary_router.pilotage_daily_summary(None, summary.date, None)
    metrics = {metric.name: metric.value for metric in response.metrics}

    assert response.contract_version == "pilotage.v1"
    assert response.module == "diddipay"
    assert response.is_final is True
    assert metrics["net_expected_delta_xof"] == 3875
    assert metrics["unsettled_receivable_delta_xof"] == 375
    assert metrics["payouts_amount_xof"] == 900
    assert response.sources[0].record_type == "payments.financial_journals"


def test_summary_auth_checks_identity_and_scope(monkeypatch):
    settings = SimpleNamespace(
        payment_summary_client_id="pilotage-staging-diddipay",
        payment_summary_audience="diddipay",
        payment_summary_scope="diddipay:payment-summary:read",
        payment_summary_legacy_scope="payment-summary:read",
        diddifreeid_issuer="diddifree-id",
    )
    monkeypatch.setattr(summary_router, "get_settings", lambda: settings)
    captured = {}

    def verify(token, **kwargs):
        captured.update(token=token, **kwargs)
        return ServicePrincipal(
            client_id="pilotage-staging-diddipay",
            subject="service:pilotage",
            scopes=frozenset({"diddipay:payment-summary:read"}),
        )

    monkeypatch.setattr(summary_router, "decode_service_token", verify)
    principal = summary_router.require_pilotage_service(
        "Bearer token", "pilotage-staging-diddipay"
    )
    assert principal.client_id == "pilotage-staging-diddipay"
    assert captured["required_scopes"] == {
        "diddipay:payment-summary:read",
        "payment-summary:read",
    }
    assert captured["allowed_client_ids"] == {"pilotage-staging-diddipay"}
