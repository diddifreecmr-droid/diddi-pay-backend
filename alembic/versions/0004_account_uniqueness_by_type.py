"""Unicité de compte : (user_id, currency) devient (user_id, currency, account_type).

Un même utilisateur doit pouvoir posséder à la fois un compte `user` (son wallet personnel) et un
compte `merchant` dans la même devise — le cas d'un commerçant qui est aussi client. C'est devenu
nécessaire avec le QR code de paiement : le propriétaire d'un compte marchand qui génère son
propre QR a, par construction, déjà un wallet personnel dans la même devise.

Revision ID: 0004_account_uniqueness_by_type
Revises: 0003_multi_currency
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_account_uniqueness_by_type"
down_revision: str | None = "0003_multi_currency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_accounts_user_currency", table_name="accounts", schema="wallet")
    op.create_index(
        "uq_accounts_user_currency_type",
        "accounts",
        ["user_id", "currency", "account_type"],
        unique=True,
        schema="wallet",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_accounts_user_currency_type", table_name="accounts", schema="wallet")
    op.create_index(
        "uq_accounts_user_currency",
        "accounts",
        ["user_id", "currency"],
        unique=True,
        schema="wallet",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
