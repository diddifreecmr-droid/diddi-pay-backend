"""Fondations multi-devises : position de change, cotations, conversions.

Aucune migration de **données** n'est nécessaire. Les montants sont désormais exprimés en unité
mineure, or l'exposant du XOF est 0 : les lignes existantes valent déjà ce qu'il faut.

Revision ID: 0003_multi_currency
Revises: 0002_deposits_and_loans
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_multi_currency"
down_revision: str | None = "0002_deposits_and_loans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Un compte de contrepartie (suspense d'opérateur, position de change) est négatif par
    # construction pendant l'opération ; un compte client ou un pool de campagne, jamais.
    op.add_column(
        "accounts",
        sa.Column(
            "allows_negative_balance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="wallet",
    )
    # Les comptes suspense déjà créés relèvent de cette catégorie.
    op.execute(
        """
        UPDATE wallet.accounts SET allows_negative_balance = true
        WHERE id IN (SELECT account_id FROM wallet.gateway_accounts)
        """
    )

    # Un utilisateur = un compte **par devise** (un seul tant qu'on n'opère qu'en XOF).
    op.drop_index("uq_accounts_user_id", table_name="accounts", schema="wallet")
    op.create_index(
        "uq_accounts_user_currency",
        "accounts",
        ["user_id", "currency"],
        unique=True,
        schema="wallet",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    # Les montants sont des entiers d'unités mineures : aucune valeur fractionnaire ne doit
    # pouvoir entrer en base, même par une écriture manuelle.
    op.create_check_constraint(
        "ck_ledger_amount_integral",
        "ledger_entries",
        "amount = trunc(amount)",
        schema="wallet",
    )

    op.create_table(
        "exchange_rates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("base_currency", sa.CHAR(3), nullable=False),
        sa.Column("quote_currency", sa.CHAR(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("rate > 0", name="ck_rates_positive"),
        sa.CheckConstraint("base_currency <> quote_currency", name="ck_rates_distinct"),
        schema="wallet",
    )
    op.create_index(
        "idx_rates_pair",
        "exchange_rates",
        ["base_currency", "quote_currency", "valid_from"],
        schema="wallet",
    )

    op.create_table(
        "currency_conversions",
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
        sa.Column("source_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source_currency", sa.CHAR(3), nullable=False),
        sa.Column("target_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_currency", sa.CHAR(3), nullable=False),
        # Taux figé au moment de la conversion : rejouer l'historique doit redonner le même
        # résultat, quelles que soient les cotations ultérieures.
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_table("currency_conversions", schema="wallet")
    op.drop_table("exchange_rates", schema="wallet")
    op.drop_constraint(
        "ck_ledger_amount_integral", "ledger_entries", schema="wallet", type_="check"
    )
    op.drop_index("uq_accounts_user_currency", table_name="accounts", schema="wallet")
    op.create_index(
        "uq_accounts_user_id",
        "accounts",
        ["user_id"],
        unique=True,
        schema="wallet",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.drop_column("accounts", "allows_negative_balance", schema="wallet")
