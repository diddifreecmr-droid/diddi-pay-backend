"""Pilotage-only aggregate of confirmed payment journal events."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel

from payfund_app.core.config import get_settings
from payfund_app.core.errors import Unauthenticated
from payfund_app.core.security import ServicePrincipal, decode_service_token
from payfund_app.modules.payments.application.accounting_summary import (
    PaymentAccountingSummaryUseCases,
)
from payfund_app.modules.payments.application.daily_summary import (
    PaymentDailySummary,
    PaymentDailySummaryUseCases,
)
from payfund_app.modules.payments.application.health_summary import (
    PaymentHealthSummaryUseCases,
)
from payfund_app.modules.payments.infra.accounting_summary import (
    SqlPaymentAccountingSummaryRepository,
)
from payfund_app.modules.payments.infra.daily_summary import (
    SqlPaymentDailySummaryRepository,
)
from payfund_app.modules.payments.infra.health_summary import (
    SqlPaymentHealthSummaryRepository,
)
from payfund_app.modules.payments.presentation.capability_declarations import (
    WalletCapabilityDeclaration,
    wallet_capability_declarations,
)
from payfund_app.modules.payments.presentation.deps import SessionDep

router = APIRouter(prefix="/internal", tags=["internal-pilotage"])


class PaymentSummaryResponse(BaseModel):
    date: date
    timezone: str
    currency: str
    confirmed_payments_count: int
    confirmed_payments_amount_xof: int
    confirmed_refunds_count: int
    confirmed_refunds_amount_xof: int
    calculated_at: datetime
    source: str


class PilotageMetric(BaseModel):
    name: str
    value: int
    unit: str


class PilotageSource(BaseModel):
    module: str
    record_type: str


class PilotageDeepLink(BaseModel):
    label: str
    href: str


class PilotageDailySummaryResponse(BaseModel):
    contract_version: str = "pilotage.v1"
    module: str = "diddipay"
    date: date
    timezone: str
    is_final: bool
    metrics: list[PilotageMetric]
    calculated_at: datetime
    sources: list[PilotageSource]
    deep_links: list[PilotageDeepLink]


class PilotageAccountingEntry(BaseModel):
    service: Literal["diddigo", "diddisend", "diddifood", "diddifund", "diddifree", "unknown"]
    processor: str
    flow_type: Literal[
        "service_payment",
        "wallet_topup",
        "refund",
        "settlement",
        "payout",
        "wallet_deposit",
        "wallet_withdrawal",
        "wallet_transfer",
        "adjustment",
        "unknown",
    ]
    status: Literal["succeeded"]
    transactions_count: int
    gross_amount_xof: int
    refund_amount_xof: int
    processor_fees_xof: int
    net_expected_xof: int
    settled_amount_xof: int
    outstanding_amount_xof: int


class PilotageAccountingSummaryResponse(BaseModel):
    contract_version: Literal["pilotage.accounting-source.v1"] = (
        "pilotage.accounting-source.v1"
    )
    module: Literal["diddipay"] = "diddipay"
    date: date
    timezone: Literal["Africa/Abidjan"] = "Africa/Abidjan"
    currency: Literal["XOF"] = "XOF"
    is_final: bool
    entries: list[PilotageAccountingEntry]
    calculated_at: datetime
    sources: list[PilotageSource]


class PilotageHealthSummaryResponse(BaseModel):
    contract_version: str = "pilotage.v1"
    module: str = "diddipay"
    status: Literal["healthy", "degraded", "unavailable"]
    pending_callbacks_count: int | None
    dead_letter_callbacks_count: int | None
    calculated_at: datetime
    sources: list[PilotageSource]
    deep_links: list[PilotageDeepLink]


class PilotageCapabilitiesResponse(BaseModel):
    contract_version: Literal["pilotage.v1"] = "pilotage.v1"
    module: Literal["diddipay"] = "diddipay"
    authentication: Literal["diddifreeid_service_token"] = "diddifreeid_service_token"
    scope: str
    timezone: Literal["Africa/Abidjan"] = "Africa/Abidjan"
    currency: Literal["XOF"] = "XOF"
    aggregates: list[str]
    pro_capabilities: list[WalletCapabilityDeclaration]


def require_pilotage_service(
    authorization: Annotated[str | None, Header()] = None,
    client_id: Annotated[str | None, Header(alias="X-Client-ID")] = None,
) -> ServicePrincipal:
    settings = get_settings()
    if not settings.payment_summary_client_id:
        raise Unauthenticated("Client Pilotage non configure.")
    if not authorization or not authorization.startswith("Bearer ") or not client_id:
        raise Unauthenticated()
    accepted_scopes = {settings.payment_summary_scope}
    if settings.payment_summary_legacy_scope:
        accepted_scopes.add(settings.payment_summary_legacy_scope)
    principal = decode_service_token(
        authorization.removeprefix("Bearer ").strip(),
        audience=settings.payment_summary_audience,
        client_id_header=client_id,
        required_scopes=accepted_scopes,
        allowed_client_ids={settings.payment_summary_client_id},
    )
    if principal.subject != "service:pilotage":
        raise Unauthenticated("Le jeton doit appartenir au service Pilotage.")
    return principal


@router.get("/pilotage/capabilities", response_model=PilotageCapabilitiesResponse)
def pilotage_capabilities(
    _: Annotated[ServicePrincipal, Depends(require_pilotage_service)],
) -> PilotageCapabilitiesResponse:
    return PilotageCapabilitiesResponse(
        scope=get_settings().payment_summary_scope,
        aggregates=["daily_summary", "accounting_summary", "health_summary"],
        pro_capabilities=wallet_capability_declarations(),
    )


@router.get("/v1/payment-summary", response_model=PaymentSummaryResponse)
def payment_summary(
    session: SessionDep,
    date_: Annotated[date, Query(alias="date", description="Business day in Africa/Abidjan")],
    _: Annotated[ServicePrincipal, Depends(require_pilotage_service)],
) -> PaymentSummaryResponse:
    result: PaymentDailySummary = PaymentDailySummaryUseCases(
        SqlPaymentDailySummaryRepository(session)
    ).get(date_)
    return PaymentSummaryResponse.model_validate(result, from_attributes=True)


@router.get("/pilotage/daily-summary", response_model=PilotageDailySummaryResponse)
def pilotage_daily_summary(
    session: SessionDep,
    date_: Annotated[date, Query(alias="date", description="Business day in Africa/Abidjan")],
    _: Annotated[ServicePrincipal, Depends(require_pilotage_service)],
) -> PilotageDailySummaryResponse:
    result = PaymentDailySummaryUseCases(SqlPaymentDailySummaryRepository(session)).get(date_)
    net_expected_delta = (
        result.confirmed_payments_amount_xof
        - result.confirmed_refunds_amount_xof
        - result.processor_fees_amount_xof
    )
    metrics = [
        PilotageMetric(
            name="confirmed_payments_count",
            value=result.confirmed_payments_count,
            unit="count",
        ),
        PilotageMetric(
            name="confirmed_payments_amount_xof",
            value=result.confirmed_payments_amount_xof,
            unit="XOF",
        ),
        PilotageMetric(
            name="confirmed_refunds_count",
            value=result.confirmed_refunds_count,
            unit="count",
        ),
        PilotageMetric(
            name="confirmed_refunds_amount_xof",
            value=result.confirmed_refunds_amount_xof,
            unit="XOF",
        ),
        PilotageMetric(
            name="processor_fees_amount_xof",
            value=result.processor_fees_amount_xof,
            unit="XOF",
        ),
        PilotageMetric(
            name="net_expected_delta_xof",
            value=net_expected_delta,
            unit="XOF",
        ),
        PilotageMetric(
            name="settlements_count",
            value=result.settlements_count,
            unit="count",
        ),
        PilotageMetric(
            name="settlements_amount_xof",
            value=result.settlements_amount_xof,
            unit="XOF",
        ),
        PilotageMetric(
            name="unsettled_receivable_delta_xof",
            value=net_expected_delta - result.settlements_amount_xof,
            unit="XOF",
        ),
        PilotageMetric(
            name="payouts_count",
            value=result.payouts_count,
            unit="count",
        ),
        PilotageMetric(
            name="payouts_amount_xof",
            value=result.payouts_amount_xof,
            unit="XOF",
        ),
    ]
    today = datetime.now(ZoneInfo(result.timezone)).date()
    return PilotageDailySummaryResponse(
        date=result.date,
        timezone=result.timezone,
        is_final=result.date < today,
        metrics=metrics,
        calculated_at=result.calculated_at,
        sources=[PilotageSource(module="diddipay", record_type=result.source)],
        deep_links=[
            PilotageDeepLink(
                label="Open payment operations in Backoffice",
                href="/backoffice/#diddipay-payments",
            )
        ],
    )


@router.get(
    "/pilotage/accounting-summary",
    response_model=PilotageAccountingSummaryResponse,
    summary="Ventilation comptable quotidienne DiddiPay",
    description=(
        "Agrège les journaux financiers confirmés par service, processeur et type de flux. "
        "Les montants sont des entiers XOF et la journée civile est Africa/Abidjan. "
        "Aucune donnée personnelle n'est exposée. Une erreur de source est propagée et n'est "
        "jamais remplacée par de faux zéros."
    ),
)
def pilotage_accounting_summary(
    session: SessionDep,
    date_: Annotated[date, Query(alias="date", description="Business day in Africa/Abidjan")],
    _: Annotated[ServicePrincipal, Depends(require_pilotage_service)],
) -> PilotageAccountingSummaryResponse:
    result = PaymentAccountingSummaryUseCases(
        SqlPaymentAccountingSummaryRepository(session)
    ).get(date_)
    return PilotageAccountingSummaryResponse(
        date=result.date,
        is_final=result.is_final,
        entries=[
            PilotageAccountingEntry.model_validate(entry, from_attributes=True)
            for entry in result.entries
        ],
        calculated_at=result.calculated_at,
        sources=[PilotageSource(module="diddipay", record_type="financial-journals")],
    )


@router.get("/pilotage/health-summary", response_model=PilotageHealthSummaryResponse)
def pilotage_health_summary(
    session: SessionDep,
    _: Annotated[ServicePrincipal, Depends(require_pilotage_service)],
) -> PilotageHealthSummaryResponse:
    result = PaymentHealthSummaryUseCases(SqlPaymentHealthSummaryRepository(session)).get()
    return PilotageHealthSummaryResponse(
        status=result.status,
        pending_callbacks_count=result.pending_callbacks_count,
        dead_letter_callbacks_count=result.dead_letter_callbacks_count,
        calculated_at=result.calculated_at,
        sources=[PilotageSource(module="diddipay", record_type="payments.outbox_events")],
        deep_links=[
            PilotageDeepLink(
                label="Open payment operations in Backoffice",
                href="/backoffice/#diddipay-payments",
            )
        ],
    )
