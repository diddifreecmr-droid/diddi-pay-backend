"""add provider-neutral payouts

Revision ID: 9c41a72d6e10
Revises: 7f3a1c9e2b6d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9c41a72d6e10"
down_revision: str | None = "7f3a1c9e2b6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payouts",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("business_reference", sa.String(length=128), nullable=False),
        sa.Column("beneficiary_reference", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("processor", sa.String(length=64), nullable=False),
        sa.Column("provider_reference", sa.String(length=160), nullable=True),
        sa.Column("provider_status", sa.String(length=80), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_message", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_payout_amount_positive"),
        sa.CheckConstraint(
            "status IN ('pending','processing','succeeded','failed','disputed')",
            name="ck_payout_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_id", "idempotency_key", name="uq_payout_client_idempotency"
        ),
        schema="payments",
    )
    op.create_index(
        "idx_payout_business",
        "payouts",
        ["client_id", "business_reference"],
        schema="payments",
    )
    op.create_index(
        "idx_payout_status_updated",
        "payouts",
        ["status", "updated_at"],
        schema="payments",
    )
    op.create_index(
        "uq_payout_provider_reference",
        "payouts",
        ["processor", "provider_reference"],
        unique=True,
        schema="payments",
        postgresql_where=sa.text("provider_reference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_payout_provider_reference", table_name="payouts", schema="payments"
    )
    op.drop_index("idx_payout_status_updated", table_name="payouts", schema="payments")
    op.drop_index("idx_payout_business", table_name="payouts", schema="payments")
    op.drop_table("payouts", schema="payments")
