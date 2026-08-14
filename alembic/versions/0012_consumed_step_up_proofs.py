"""One-time DiddiFreeID step-up proof consumption.

Revision ID: 0012_consumed_step_up_proofs
Revises: 0011_step_up_otp_and_pin_audit
Create Date: 2026-08-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_consumed_step_up_proofs"
down_revision = "0011_step_up_otp_and_pin_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consumed_step_up_proofs",
        sa.Column("jti", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("purpose <> ''", name="ck_step_up_proof_purpose"),
        sa.PrimaryKeyConstraint("jti"),
        schema="wallet",
    )
    op.create_index(
        "idx_step_up_proofs_expiry",
        "consumed_step_up_proofs",
        ["expires_at"],
        unique=False,
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_step_up_proofs_expiry",
        table_name="consumed_step_up_proofs",
        schema="wallet",
    )
    op.drop_table("consumed_step_up_proofs", schema="wallet")
