"""Persiste le lien de checkout Paystack sur les transactions wallet.

`authorization_url` et `access_code` n'existaient qu'en mémoire, retournés une seule fois dans la
réponse `202` de `POST /wallet/deposit`. Un rejeu de la même Idempotency-Key, ou une consultation
ultérieure via `GET /wallet/transactions/{id}`, ne pouvaient jamais les retrouver — l'utilisateur
restait bloqué sans lien de paiement à ouvrir.

Revision ID: 7f3a1c9e2b6d
Revises: 22eb39b4d210
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "7f3a1c9e2b6d"
down_revision: str | None = "22eb39b4d210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("authorization_url", sa.String(500), nullable=True),
        schema="wallet",
    )
    op.add_column(
        "transactions",
        sa.Column("access_code", sa.String(100), nullable=True),
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_column("transactions", "access_code", schema="wallet")
    op.drop_column("transactions", "authorization_url", schema="wallet")
