"""add wallet.transactions.gateway_mode

Revision ID: a1c7f0e9d3b2
Revises: 4fd729cc31a8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c7f0e9d3b2"
down_revision: str | None = "4fd729cc31a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, pas de backfill : les lignes historiques n'ont jamais enregistré quel
    # adaptateur les a traitées. `WalletUseCases.reconcile_transaction` traite NULL comme
    # "paystack" (seul mode utilisé en production à ce jour) — voir son commentaire.
    op.add_column(
        "transactions",
        sa.Column("gateway_mode", sa.String(length=30), nullable=True),
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_column("transactions", "gateway_mode", schema="wallet")
