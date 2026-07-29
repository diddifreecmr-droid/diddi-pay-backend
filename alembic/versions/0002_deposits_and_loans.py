"""En-tête de transaction enrichi (dépôt/retrait) et tables de prêt.

Revision ID: 0002_deposits_and_loans
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_deposits_and_loans"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- wallet.transactions ------------------------------------------------
    # Un dépôt en attente de l'opérateur n'a encore aucune écriture : sans ces colonnes, ni son
    # montant ni son compte n'existent nulle part et il serait invisible dans l'historique.
    op.add_column(
        "transactions",
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet.accounts.id"),
            nullable=True,
        ),
        schema="wallet",
    )
    op.add_column(
        "transactions", sa.Column("amount", sa.Numeric(14, 2), nullable=True), schema="wallet"
    )
    op.add_column(
        "transactions", sa.Column("currency", sa.CHAR(3), nullable=True), schema="wallet"
    )
    op.add_column(
        "transactions",
        sa.Column("provider_reference", sa.String(100), nullable=True),
        schema="wallet",
    )
    op.add_column(
        "transactions",
        sa.Column("reverses_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="wallet",
    )
    op.create_index(
        "idx_transactions_account", "transactions", ["account_id"], schema="wallet"
    )

    # --- fund.loans ---------------------------------------------------------
    op.create_table(
        "loans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fund.campaigns.id"),
            nullable=False,
        ),
        sa.Column("borrower_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("duration_months", sa.SmallInteger(), nullable=False),
        sa.Column("interest_rate_applied", sa.Numeric(5, 2), nullable=False),
        sa.Column("total_repayable", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default="XOF"),
        sa.Column("diddi_score_at_grant", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("disbursed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wallet_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending','disbursed','repaying','closed','defaulted')",
            name="ck_loans_status",
        ),
        sa.CheckConstraint("principal_amount > 0", name="ck_loans_principal_positive"),
        sa.CheckConstraint(
            "duration_months > 0 AND duration_months <= 60", name="ck_loans_duration"
        ),
        schema="fund",
    )
    op.create_index("idx_loans_borrower", "loans", ["borrower_user_id"], schema="fund")
    op.create_index("idx_loans_campaign", "loans", ["campaign_id"], schema="fund")

    op.create_table(
        "repayment_schedule",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fund.loans.id"),
            nullable=False,
        ),
        sa.Column("installment_no", sa.SmallInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount_due", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "amount_paid", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="due"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('due','paid','late','defaulted')", name="ck_schedule_status"
        ),
        sa.CheckConstraint("amount_due > 0", name="ck_schedule_amount_due_positive"),
        sa.CheckConstraint("amount_paid >= 0", name="ck_schedule_amount_paid_positive"),
        schema="fund",
    )
    op.create_index(
        "uq_schedule_loan_installment",
        "repayment_schedule",
        ["loan_id", "installment_no"],
        unique=True,
        schema="fund",
    )

    op.create_table(
        "loan_status_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fund.loans.id"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        schema="fund",
    )


def downgrade() -> None:
    op.drop_table("loan_status_history", schema="fund")
    op.drop_table("repayment_schedule", schema="fund")
    op.drop_table("loans", schema="fund")
    op.drop_index("idx_transactions_account", table_name="transactions", schema="wallet")
    for column in (
        "reverses_transaction_id",
        "provider_reference",
        "currency",
        "amount",
        "account_id",
    ):
        op.drop_column("transactions", column, schema="wallet")
