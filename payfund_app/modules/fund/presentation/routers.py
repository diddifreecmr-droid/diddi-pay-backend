"""Routes du module `fund` (Contrat API §2).

Le décaissement d'un prêt (`pending → disbursed`) n'a pas de route : comme la validation d'une
campagne (`draft → active`), il relève du back-office et sort du contrat public (§3).
"""

from __future__ import annotations

import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from payfund_app.modules.fund.application.use_cases import FundUseCases, LoanUseCases
from payfund_app.modules.fund.infra.scoring import get_scoring
from payfund_app.modules.fund.infra.wallet_client import get_wallet_service
from payfund_app.modules.fund.presentation.schemas import (
    CampaignDetail,
    CampaignItem,
    CreateCampaignRequest,
    CreateCampaignResponse,
    CreateLoanRequest,
    CreateLoanResponse,
    InstallmentItem,
    InvestRequest,
    InvestResponse,
    InvestmentSummary,
    LoanDetail,
    NextInstallment,
    RepayRequest,
    RepayResponse,
    ScheduleResponse,
    SimulateLoanRequest,
    SimulateLoanResponse,
)
from payfund_app.modules.wallet.presentation.deps import (
    CurrentUserDep,
    IdempotencyKeyDep,
    SessionDep,
)
from payfund_app.modules.wallet.presentation.schemas import Page, Pagination

router = APIRouter(prefix="/fund", tags=["fund"])


def _use_cases(session) -> FundUseCases:
    return FundUseCases(session, wallet=get_wallet_service(session))


@router.post("/campaigns", response_model=CreateCampaignResponse, status_code=201)
def create_campaign(
    payload: CreateCampaignRequest, user: CurrentUserDep, session: SessionDep
) -> CreateCampaignResponse:
    campaign = _use_cases(session).creer_campagne(
        owner_user_id=user.user_id,
        title=payload.title,
        goal_amount=payload.goal_amount,
        currency=payload.currency,
    )
    return CreateCampaignResponse(campaign_id=campaign.id, status=campaign.status)


@router.get("/campaigns", response_model=Page[CampaignItem])
def list_campaigns(
    user: CurrentUserDep,
    session: SessionDep,
    status: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[CampaignItem]:
    campaigns, total = _use_cases(session).lister_campagnes(
        status=status, page=page, page_size=page_size
    )
    return Page[CampaignItem](
        data=[
            CampaignItem(
                id=c.id,
                title=c.title,
                goal_amount=int(c.goal_amount),
                raised_amount=int(c.raised_amount),
                currency=c.currency,
                status=c.status,
            )
            for c in campaigns
        ],
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=math.ceil(total / page_size) if total else 0,
        ),
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
def get_campaign(
    campaign_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> CampaignDetail:
    campaign, investments = _use_cases(session).detail_campagne(campaign_id)
    return CampaignDetail(
        id=campaign.id,
        title=campaign.title,
        goal_amount=int(campaign.goal_amount),
        raised_amount=int(campaign.raised_amount),
        currency=campaign.currency,
        status=campaign.status,
        owner_user_id=campaign.owner_user_id,
        created_at=campaign.created_at,
        closed_at=campaign.closed_at,
        latest_investments=[
            InvestmentSummary(amount=int(i.amount), created_at=i.created_at)
            for i in investments
        ],
    )


@router.post("/campaigns/{campaign_id}/invest", response_model=InvestResponse, status_code=201)
def invest(
    campaign_id: uuid.UUID,
    payload: InvestRequest,
    user: CurrentUserDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
) -> InvestResponse:
    result = _use_cases(session).investir(
        campaign_id=campaign_id,
        investor_user_id=user.user_id,
        amount=payload.amount,
        pin=payload.pin,
        idempotency_key=idempotency_key,
    )
    return InvestResponse(
        investment_id=result.investment.id,
        campaign_id=campaign_id,
        amount=int(result.investment.amount),
        wallet_transaction_id=result.wallet_transaction_id,
    )


# --- Prêts -------------------------------------------------------------------


def _loans(session) -> LoanUseCases:
    return LoanUseCases(
        session, wallet=get_wallet_service(session), scoring=get_scoring()
    )


def _installment(echeance) -> InstallmentItem:
    return InstallmentItem(
        installment_no=echeance.installment_no,
        due_date=echeance.due_date,
        amount_due=int(echeance.amount_due),
        amount_paid=int(echeance.amount_paid),
        status=echeance.status,
    )


@router.post("/loans/simulate", response_model=SimulateLoanResponse)
def simulate_loan(
    payload: SimulateLoanRequest, user: CurrentUserDep, session: SessionDep
) -> SimulateLoanResponse:
    """Simulateur — ne crée rien, calcul pur. Le taux dépend de l'emprunteur du token."""
    terms = _loans(session).simuler(
        user_id=user.user_id,
        amount=payload.amount,
        duration_months=payload.duration_months,
    )
    return SimulateLoanResponse(
        principal=terms.principal,
        duration_months=terms.duration_months,
        monthly_installment=terms.monthly_installment,
        total_repayable=terms.total_repayable,
        interest_rate_applied=terms.interest_rate_applied,
    )


@router.post("/loans", response_model=CreateLoanResponse, status_code=201)
def create_loan(
    payload: CreateLoanRequest, user: CurrentUserDep, session: SessionDep
) -> CreateLoanResponse:
    loan = _loans(session).demander(
        campaign_id=payload.campaign_id,
        borrower_user_id=user.user_id,
        amount=payload.amount,
        duration_months=payload.duration_months,
    )
    return CreateLoanResponse(loan_id=loan.id, status=loan.status)


@router.get("/loans/{loan_id}", response_model=LoanDetail)
def get_loan(loan_id: uuid.UUID, user: CurrentUserDep, session: SessionDep) -> LoanDetail:
    loan, prochaine = _loans(session).detail(loan_id, user_id=user.user_id)
    return LoanDetail(
        id=loan.id,
        campaign_id=loan.campaign_id,
        status=loan.status,
        principal_amount=int(loan.principal_amount),
        currency=loan.currency,
        duration_months=loan.duration_months,
        interest_rate_applied=loan.interest_rate_applied,
        total_repayable=int(loan.total_repayable),
        disbursed_at=loan.disbursed_at,
        next_installment=(
            NextInstallment(
                due_date=prochaine.due_date,
                amount_due=int(prochaine.amount_due) - int(prochaine.amount_paid),
                status=prochaine.status,
            )
            if prochaine
            else None
        ),
    )


@router.get("/loans/{loan_id}/schedule", response_model=ScheduleResponse)
def get_schedule(
    loan_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> ScheduleResponse:
    echeances = _loans(session).echeancier(loan_id, user_id=user.user_id)
    return ScheduleResponse(data=[_installment(e) for e in echeances])


@router.post("/loans/{loan_id}/repay", response_model=RepayResponse)
def repay_loan(
    loan_id: uuid.UUID,
    payload: RepayRequest,
    user: CurrentUserDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
) -> RepayResponse:
    loan, echeance = _loans(session).rembourser(
        loan_id=loan_id,
        borrower_user_id=user.user_id,
        amount=payload.amount,
        pin=payload.pin,
        idempotency_key=idempotency_key,
    )
    return RepayResponse(
        loan_id=loan.id, loan_status=loan.status, installment=_installment(echeance)
    )
