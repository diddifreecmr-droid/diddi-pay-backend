"""Accès données du module `fund`."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from payfund_app.modules.fund.domain.entities import InstallmentStatus, LoanStatus
from payfund_app.modules.fund.domain.loan import Installment
from payfund_app.modules.fund.infra.models import (
    Campaign,
    FundPaymentEventInbox,
    FundPaymentOrder,
    Investment,
    Loan,
    LoanStatusHistory,
    RepaymentSchedule,
)


class CampaignRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, campaign_id: uuid.UUID) -> Campaign | None:
        return self.session.get(Campaign, campaign_id)

    def get_for_update(self, campaign_id: uuid.UUID) -> Campaign | None:
        """Verrou de ligne : sérialise les investissements concurrents sur une même campagne,
        pour que le contrôle « objectif déjà atteint » et l'incrément de `raised_amount` ne
        puissent pas s'entrelacer."""
        return self.session.scalar(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        )

    def create(
        self,
        *,
        owner_user_id: uuid.UUID,
        title: str,
        goal_amount: int,
        currency: str,
        wallet_account_id: uuid.UUID,
        status: str,
    ) -> Campaign:
        campaign = Campaign(
            owner_user_id=owner_user_id,
            title=title,
            goal_amount=Decimal(goal_amount),
            currency=currency,
            status=status,
            wallet_account_id=wallet_account_id,
        )
        self.session.add(campaign)
        self.session.flush()
        return campaign

    def list_by_status(
        self, status: str | None, page: int, page_size: int
    ) -> tuple[list[Campaign], int]:
        query = select(Campaign)
        if status:
            query = query.where(Campaign.status == status)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            self.session.scalars(
                query.order_by(Campaign.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total


class InvestmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign_id: uuid.UUID,
        investor_user_id: uuid.UUID,
        amount: int,
        wallet_transaction_id: uuid.UUID | None = None,
        payment_intent_id: uuid.UUID | None = None,
    ) -> Investment:
        investment = Investment(
            campaign_id=campaign_id,
            investor_user_id=investor_user_id,
            amount=Decimal(amount),
            wallet_transaction_id=wallet_transaction_id,
            payment_intent_id=payment_intent_id,
        )
        self.session.add(investment)
        self.session.flush()
        return investment

    def by_wallet_transaction(self, wallet_transaction_id: uuid.UUID) -> Investment | None:
        return self.session.scalar(
            select(Investment).where(
                Investment.wallet_transaction_id == wallet_transaction_id
            )
        )

    def latest_for_campaign(self, campaign_id: uuid.UUID, limit: int = 10) -> list[Investment]:
        return list(
            self.session.scalars(
                select(Investment)
                .where(Investment.campaign_id == campaign_id)
                .order_by(Investment.created_at.desc())
                .limit(limit)
            )
        )


class FundPaymentOrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, order_id: uuid.UUID, *, for_update: bool = False) -> FundPaymentOrder | None:
        if not for_update:
            return self.session.get(FundPaymentOrder, order_id)
        return self.session.scalar(
            select(FundPaymentOrder)
            .where(FundPaymentOrder.id == order_id)
            .with_for_update()
        )

    def by_idempotency(self, key: str) -> FundPaymentOrder | None:
        return self.session.scalar(
            select(FundPaymentOrder).where(FundPaymentOrder.idempotency_key == key)
        )

    def by_payment_intent(
        self, payment_intent_id: uuid.UUID, *, for_update: bool = False
    ) -> FundPaymentOrder | None:
        query = select(FundPaymentOrder).where(
            FundPaymentOrder.payment_intent_id == payment_intent_id
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def create(self, **values) -> FundPaymentOrder:
        order = FundPaymentOrder(**values)
        self.session.add(order)
        self.session.flush()
        return order


class FundPaymentEventInboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_once(self, *, event_id: uuid.UUID, event_type: str, payload: dict) -> bool:
        statement = (
            insert(FundPaymentEventInbox)
            .values(event_id=event_id, event_type=event_type, payload=payload)
            .on_conflict_do_nothing(index_elements=[FundPaymentEventInbox.event_id])
            .returning(FundPaymentEventInbox.event_id)
        )
        return self.session.scalar(statement) is not None


class LoanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, loan_id: uuid.UUID) -> Loan | None:
        return self.session.get(Loan, loan_id)

    def get_for_update(self, loan_id: uuid.UUID) -> Loan | None:
        return self.session.scalar(select(Loan).where(Loan.id == loan_id).with_for_update())

    def create(
        self,
        *,
        campaign_id: uuid.UUID,
        borrower_user_id: uuid.UUID,
        principal_amount: int,
        duration_months: int,
        interest_rate_applied: Decimal,
        total_repayable: int,
        currency: str,
        diddi_score_at_grant: int | None,
    ) -> Loan:
        loan = Loan(
            campaign_id=campaign_id,
            borrower_user_id=borrower_user_id,
            principal_amount=Decimal(principal_amount),
            duration_months=duration_months,
            interest_rate_applied=interest_rate_applied,
            total_repayable=Decimal(total_repayable),
            currency=currency,
            diddi_score_at_grant=diddi_score_at_grant,
            status=str(LoanStatus.PENDING),
        )
        self.session.add(loan)
        self.session.flush()
        self.tracer_statut(loan, None, str(LoanStatus.PENDING))
        return loan

    def tracer_statut(
        self,
        loan: Loan,
        from_status: str | None,
        to_status: str,
        metadata: dict | None = None,
    ) -> None:
        self.session.add(
            LoanStatusHistory(
                loan_id=loan.id,
                from_status=from_status,
                to_status=to_status,
                metadata_=metadata,
            )
        )
        self.session.flush()

    def changer_statut(self, loan: Loan, to_status: str, metadata: dict | None = None) -> None:
        ancien = loan.status
        if ancien == to_status:
            return
        loan.status = to_status
        self.tracer_statut(loan, ancien, to_status, metadata)

    def ajouter_echeances(self, loan: Loan, echeances: list[Installment]) -> None:
        for echeance in echeances:
            self.session.add(
                RepaymentSchedule(
                    loan_id=loan.id,
                    installment_no=echeance.installment_no,
                    due_date=echeance.due_date,
                    amount_due=Decimal(echeance.amount_due),
                )
            )
        self.session.flush()

    def echeancier(self, loan_id: uuid.UUID) -> list[RepaymentSchedule]:
        return list(
            self.session.scalars(
                select(RepaymentSchedule)
                .where(RepaymentSchedule.loan_id == loan_id)
                .order_by(RepaymentSchedule.installment_no)
            )
        )

    def prochaine_echeance(self, loan_id: uuid.UUID) -> RepaymentSchedule | None:
        """Première échéance non soldée, dans l'ordre du calendrier."""
        return self.session.scalar(
            select(RepaymentSchedule)
            .where(
                RepaymentSchedule.loan_id == loan_id,
                RepaymentSchedule.status != str(InstallmentStatus.PAID),
            )
            .order_by(RepaymentSchedule.installment_no)
            .limit(1)
            .with_for_update()
        )
