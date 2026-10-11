"""add payments.payouts.destination

Revision ID: b2d4f6a81c93
Revises: a1c7f0e9d3b2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2d4f6a81c93"
down_revision: str | None = "a1c7f0e9d3b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, no backfill: existing rows were all created before any real payout
    # processor existed (SandboxPaymentProcessor only), so none of them has a real
    # destination to record. {"type": "mobile_money", "phone": ..., "network": ...} today;
    # generic by design so a future destination type needs no migration of old rows.
    op.add_column(
        "payouts",
        sa.Column("destination", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="payments",
    )


def downgrade() -> None:
    op.drop_column("payouts", "destination", schema="payments")
