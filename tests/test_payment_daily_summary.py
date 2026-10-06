import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from payfund_app.core.errors import Unauthenticated
from payfund_app.core.security import ServicePrincipal
from payfund_app.main import app
from payfund_app.modules.payments.application.accounting_summary import (
    AccountingSourceRow,
    PaymentAccountingSummaryUseCases,
    classify_flow,
    normalize_service,
)
from payfund_app.modules.payments.application.daily_summary import (
    PaymentDailySummaryUseCases,
    day_bounds,
)
from payfund_app.modules.payments.application.health_summary import (
    PaymentHealthSummaryUseCases,
)
from payfund_app.modules.payments.infra.accounting_summary import (
    SqlPaymentAccountingSummaryRepository,
)
from payfund_app.modules.payments.infra.models import (
    FinancialJournalRecord,
    PaymentAttemptRecord,
    PaymentIntentRecord,
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

    health_path = "/payfund/v1/internal/pilotage/health-summary"
    assert health_path in app.openapi()["paths"]
    response = TestClient(app).get(health_path)
    assert response.status_code == 401

    pilotage_path = "/payfund/v1/internal/pilotage/daily-summary"
    assert pilotage_path in app.openapi()["paths"]
    response = TestClient(app).get(pilotage_path, params={"date": "2026-09-18"})
    assert response.status_code == 401
    assert "/payfund/v1/internal/pilotage/capabilities" in app.openapi()["paths"]


def test_pilotage_capabilities_are_versioned(monkeypatch):
    monkeypatch.setattr(
        summary_router,
        "get_settings",
        lambda: SimpleNamespace(payment_summary_scope="diddipay:payment-summary:read"),
    )
    result = summary_router.pilotage_capabilities(
        ServicePrincipal("pilotage", "service:pilotage", frozenset())
    )
    assert result.contract_version == "pilotage.v1"
    assert result.currency == "XOF"
    assert result.aggregates == ["daily_summary", "accounting_summary", "health_summary"]
    [wallet] = result.pro_capabilities
    assert wallet.service == "diddipay"
    assert wallet.capability_type == "wallet"
    assert wallet.projection_authorizes_financial_operations is False
    assert wallet.financial_operations_allowed == []


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


def test_summary_auth_requires_exact_pilotage_subject(monkeypatch):
    settings = SimpleNamespace(
        payment_summary_client_id="pilotage-staging-diddipay",
        payment_summary_audience="diddipay",
        payment_summary_scope="diddipay:payment-summary:read",
        payment_summary_legacy_scope="",
    )
    monkeypatch.setattr(summary_router, "get_settings", lambda: settings)
    monkeypatch.setattr(
        summary_router,
        "decode_service_token",
        lambda *args, **kwargs: ServicePrincipal(
            "pilotage-staging-diddipay", "service:other", frozenset()
        ),
    )
    with pytest.raises(Unauthenticated):
        summary_router.require_pilotage_service(
            "Bearer token", "pilotage-staging-diddipay"
        )


def test_accounting_summary_separates_business_topup_and_payout_flows():
    rows = [
        AccountingSourceRow(
            "diddisend",
            "paystack",
            "diddisend:delivery:42",
            {"customer_phone": "+22500000000"},
            "capture",
            2,
            60000,
        ),
        AccountingSourceRow(
            "diddisend",
            "paystack",
            "diddisend:delivery:42",
            {},
            "refund",
            1,
            5000,
        ),
        AccountingSourceRow(
            "diddisend",
            "paystack",
            "diddisend:delivery:42",
            {},
            "processor_fee",
            2,
            1200,
        ),
        AccountingSourceRow(
            "diddisend",
            "paystack",
            "diddisend:delivery:42",
            {},
            "settlement",
            1,
            50000,
        ),
        AccountingSourceRow(
            "diddisend",
            "paystack",
            "diddisend:courier_topup:43",
            {},
            "capture",
            3,
            15000,
        ),
        AccountingSourceRow(
            "diddifood-staging",
            "sandbox",
            "diddifood:order:44:restaurant-payout:v1",
            {},
            "payout",
            1,
            7000,
        ),
    ]

    class Repository:
        def rows(self, start, end):
            assert start < end
            return rows

    result = PaymentAccountingSummaryUseCases(
        Repository(), now=lambda: datetime(2026, 10, 6, 12, tzinfo=UTC)
    ).get(date(2026, 10, 6))
    entries = {(entry.service, entry.flow_type): entry for entry in result.entries}

    payment = entries[("diddisend", "service_payment")]
    assert payment.transactions_count == 2
    assert payment.net_expected_xof == 53800
    assert payment.outstanding_amount_xof == 3800
    assert entries[("diddisend", "wallet_topup")].gross_amount_xof == 15000
    payout = entries[("diddifood", "payout")]
    assert payout.gross_amount_xof == 7000
    assert payout.net_expected_xof == 0
    assert payout.outstanding_amount_xof == 0
    assert result.is_final is False


def test_accounting_classification_prefers_explicit_metadata_and_has_safe_fallbacks():
    assert normalize_service("diddigo-production") == "diddigo"
    assert normalize_service("unregistered-module") == "unknown"
    assert classify_flow("anything", {"flow_type": "wallet_deposit"}) == "wallet_deposit"
    assert classify_flow("diddisend:courier_topup:42", {}) == "wallet_topup"
    assert classify_flow("diddigo:ride:42", {}) == "service_payment"


def test_accounting_summary_propagates_source_failure():
    class Unavailable:
        def rows(self, start, end):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        PaymentAccountingSummaryUseCases(Unavailable()).get(date(2026, 10, 6))


def test_accounting_repository_reads_confirmed_journals_with_processor(session):
    intent_id = uuid.uuid4()
    session.add(
        PaymentIntentRecord(
            id=intent_id,
            client_id="diddigo",
            business_reference="diddigo:ride:accounting-summary",
            amount=5000,
            currency="XOF",
            status="succeeded",
            idempotency_key=str(uuid.uuid4()),
            request_fingerprint="a" * 64,
            metadata_json={"flow_type": "service_payment"},
            refunded_amount=0,
        )
    )
    session.flush()
    session.add(
        PaymentAttemptRecord(
            id=uuid.uuid4(),
            payment_intent_id=intent_id,
            attempt_number=1,
            processor="paystack",
            amount=5000,
            currency="XOF",
            status="succeeded",
        )
    )
    session.add(
        FinancialJournalRecord(
            payment_intent_id=intent_id,
            event_type="capture",
            event_reference=f"accounting-summary:{intent_id}",
            amount=5000,
            currency="XOF",
            created_at=datetime(2026, 10, 6, 10, tzinfo=UTC),
        )
    )
    session.flush()

    rows = SqlPaymentAccountingSummaryRepository(session).rows(
        datetime(2026, 10, 6, tzinfo=UTC),
        datetime(2026, 10, 7, tzinfo=UTC),
    )

    [row] = rows
    assert row.client_id == "diddigo"
    assert row.processor == "paystack"
    assert row.event_type == "capture"
    assert row.amount_xof == 5000


def test_accounting_route_is_documented_without_personal_fields():
    path = "/payfund/v1/internal/pilotage/accounting-summary"
    schema = app.openapi()
    assert path in schema["paths"]
    response_schema = schema["components"]["schemas"]["PilotageAccountingSummaryResponse"]
    entry_schema = schema["components"]["schemas"]["PilotageAccountingEntry"]
    serialized = str(response_schema) + str(entry_schema)
    assert "phone" not in serialized
    assert "user_id" not in serialized
    assert "display_name" not in serialized

    response = TestClient(app).get(path, params={"date": "2026-10-06"})
    assert response.status_code == 401


def test_health_summary_distinguishes_degraded_and_unavailable_sources():
    class DeadLetters:
        def callback_status_counts(self):
            return {"pending": 3, "dead_letter": 2}

    class Unavailable:
        def callback_status_counts(self):
            raise RuntimeError("database unavailable")

    degraded = PaymentHealthSummaryUseCases(DeadLetters()).get()
    unavailable = PaymentHealthSummaryUseCases(Unavailable()).get()

    assert degraded.status == "degraded"
    assert degraded.pending_callbacks_count == 3
    assert degraded.dead_letter_callbacks_count == 2
    assert unavailable.status == "unavailable"
    assert unavailable.pending_callbacks_count is None
    assert unavailable.dead_letter_callbacks_count is None
