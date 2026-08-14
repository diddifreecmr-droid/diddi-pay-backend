"""Ajout d'une référence métier sur les transactions wallet.

Permet aux modules appelants de relier un paiement à une course, une commande, une facture ou tout
autre objet métier, sans polluer la logique comptable du wallet.

Revision ID: 0005_business_ref_txn
Revises: 0004_account_uniqueness_by_type
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_business_ref_txn"
down_revision: str | None = "0004_account_uniqueness_by_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("business_reference", sa.String(100), nullable=True),
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_column("transactions", "business_reference", schema="wallet")
