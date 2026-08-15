"""Modèles SQLAlchemy du schéma `fund` (Architecture §3.2).

Écart assumé : `campaigns.wallet_account_id` est ajouté. Le ledger étant en double entrée (§2),
un investissement doit créditer un compte identifié ; or `fund.campaigns` n'en désignait aucun.
Chaque campagne reçoit donc son propre compte technique à la création, ce qui rend le pool
collecté auditable comme n'importe quel compte.

Écarts sur `loans` par rapport au §3.2, tous rendus nécessaires par le contrat API §2 :
`campaign_id` (DiddiFund est du crowdlending : le pool d'une campagne finance le prêt de son
porteur, et les remboursements y retournent), plus `duration_months`, `interest_rate_applied`,
`total_repayable`, `currency` et `created_at`, que le contrat expose mais que la table ne portait
pas.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from payfund_app.core.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','closed','cancelled')",
            name="ck_campaigns_status",
        ),
        CheckConstraint("goal_amount > 0", name="ck_campaigns_goal_positive"),
        CheckConstraint("raised_amount >= 0", name="ck_campaigns_raised_positive"),
        Index("idx_campaigns_status", "status"),
        {"schema": "fund"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Matérialisé, recalculable depuis `investments` (§3.2).
    raised_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="XOF")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # Compte technique du pool. Référence logique vers wallet.accounts(id) : pas de FK, la
    # règle de frontière du §1 interdit à `fund` de dépendre du schéma `wallet`.
    wallet_account_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Investment(Base):
    __tablename__ = "investments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_investments_amount_positive"),
        Index("idx_investments_campaign", "campaign_id", "created_at"),
        UniqueConstraint(
            "payment_intent_id", name="uq_fund_investment_payment_intent"
        ),
        {"schema": "fund"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("fund.campaigns.id"), nullable=False
    )
    investor_user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Trace vers wallet.transactions, obtenue via WalletServicePort (§3.2).
    wallet_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    payment_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Loan(Base):
    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','disbursed','repaying','closed','defaulted')",
            name="ck_loans_status",
        ),
        CheckConstraint("principal_amount > 0", name="ck_loans_principal_positive"),
        CheckConstraint(
            "duration_months > 0 AND duration_months <= 60", name="ck_loans_duration"
        ),
        Index("idx_loans_borrower", "borrower_user_id"),
        Index("idx_loans_campaign", "campaign_id"),
        {"schema": "fund"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    # Crowdlending : c'est le pool de cette campagne qui finance le prêt et qui reçoit les
    # remboursements.
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("fund.campaigns.id"), nullable=False
    )
    borrower_user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    duration_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    interest_rate_applied: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    total_repayable: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="XOF")
    # Traçabilité de la décision (cf. cahier des charges IA) : `None` tant que le module de
    # scoring n'expose rien.
    diddi_score_at_grant: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wallet_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepaymentSchedule(Base):
    __tablename__ = "repayment_schedule"
    __table_args__ = (
        CheckConstraint(
            "status IN ('due','paid','late','defaulted')", name="ck_schedule_status"
        ),
        CheckConstraint("amount_due > 0", name="ck_schedule_amount_due_positive"),
        CheckConstraint("amount_paid >= 0", name="ck_schedule_amount_paid_positive"),
        Index("uq_schedule_loan_installment", "loan_id", "installment_no", unique=True),
        {"schema": "fund"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("fund.loans.id"), nullable=False
    )
    installment_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="due")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoanStatusHistory(Base):
    """Reprend le principe `ride_status_history` / `user_status_history` de l'écosystème."""

    __tablename__ = "loan_status_history"
    __table_args__ = ({"schema": "fund"},)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("fund.loans.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)


class FundPaymentOrder(Base):
    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('investment','loan_repayment')",
            name="ck_fund_payment_order_operation",
        ),
        CheckConstraint(
            "status IN ('requires_action','processing','succeeded','failed','cancelled','unknown')",
            name="ck_fund_payment_order_status",
        ),
        CheckConstraint("amount > 0", name="ck_fund_payment_order_amount"),
        Index("idx_fund_payment_orders_user", "payer_user_id", "created_at"),
        UniqueConstraint("idempotency_key", name="uq_fund_payment_order_idempotency"),
        UniqueConstraint("payment_intent_id", name="uq_fund_payment_order_intent"),
        {"schema": "fund"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    business_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    payer_user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("fund.campaigns.id"), nullable=True
    )
    loan_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("fund.loans.id"), nullable=True
    )
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="XOF")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FundPaymentEventInbox(Base):
    __tablename__ = "payment_event_inbox"
    __table_args__ = ({"schema": "fund"},)

    event_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
