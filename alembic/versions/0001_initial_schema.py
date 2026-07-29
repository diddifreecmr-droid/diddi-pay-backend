"""Schémas wallet et fund — état initial.

Revision ID: 0001_initial
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS wallet")
    op.execute("CREATE SCHEMA IF NOT EXISTS fund")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # --- wallet ------------------------------------------------------------
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("account_type", sa.String(20), nullable=False, server_default="user"),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default="XOF"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "account_type IN ('user','merchant','technical')", name="ck_accounts_type"
        ),
        sa.CheckConstraint(
            "status IN ('active','frozen','closed')", name="ck_accounts_status"
        ),
        sa.CheckConstraint(
            "(account_type = 'technical') OR (user_id IS NOT NULL)",
            name="ck_accounts_owner_required",
        ),
        schema="wallet",
    )
    # Un utilisateur = un compte, mais les comptes sans propriétaire échappent à la contrainte.
    op.create_index(
        "uq_accounts_user_id",
        "accounts",
        ["user_id"],
        unique=True,
        schema="wallet",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    op.create_table(
        "ledger_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet.accounts.id"),
            nullable=False,
        ),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(6), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("direction IN ('debit','credit')", name="ck_ledger_direction"),
        sa.CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
        schema="wallet",
    )
    op.create_index(
        "idx_ledger_account", "ledger_entries", ["account_id", "created_at"], schema="wallet"
    )
    op.create_index(
        "idx_ledger_transaction", "ledger_entries", ["transaction_id"], schema="wallet"
    )

    op.create_table(
        "transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("origin_module", sa.String(30), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=True, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','completed','failed','reversed')",
            name="ck_transactions_status",
        ),
        schema="wallet",
    )

    op.create_table(
        "gateway_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(30), nullable=False, unique=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet.accounts.id"),
            nullable=False,
        ),
        schema="wallet",
    )

    op.create_table(
        "user_phones",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False, unique=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="wallet",
    )
    op.create_index("ix_wallet_user_phones_phone", "user_phones", ["phone"], schema="wallet")

    # --- fund --------------------------------------------------------------
    op.create_table(
        "campaigns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "raised_amount", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default="XOF"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("wallet_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','active','closed','cancelled')", name="ck_campaigns_status"
        ),
        sa.CheckConstraint("goal_amount > 0", name="ck_campaigns_goal_positive"),
        sa.CheckConstraint("raised_amount >= 0", name="ck_campaigns_raised_positive"),
        schema="fund",
    )
    op.create_index("idx_campaigns_status", "campaigns", ["status"], schema="fund")

    op.create_table(
        "investments",
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
        sa.Column("investor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("wallet_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("amount > 0", name="ck_investments_amount_positive"),
        schema="fund",
    )
    op.create_index(
        "idx_investments_campaign", "investments", ["campaign_id", "created_at"], schema="fund"
    )


def downgrade() -> None:
    op.drop_table("investments", schema="fund")
    op.drop_table("campaigns", schema="fund")
    op.drop_table("user_phones", schema="wallet")
    op.drop_table("gateway_accounts", schema="wallet")
    op.drop_table("transactions", schema="wallet")
    op.drop_table("ledger_entries", schema="wallet")
    op.drop_table("accounts", schema="wallet")
    op.execute("DROP SCHEMA IF EXISTS fund CASCADE")
    op.execute("DROP SCHEMA IF EXISTS wallet CASCADE")
