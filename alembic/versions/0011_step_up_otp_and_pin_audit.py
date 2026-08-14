"""Step-up OTP and PIN recovery audit.

Revision ID: 0011_step_up_otp_and_pin_audit
Revises: 0010_transaction_pin_security
Create Date: 2026-08-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_step_up_otp_and_pin_audit"
down_revision = "0010_transaction_pin_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfer_otp_challenges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_phone", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["wallet.accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="wallet",
    )
    op.create_index(
        "idx_transfer_otp_account_created",
        "transfer_otp_challenges",
        ["account_id", "created_at"],
        unique=False,
        schema="wallet",
    )

    op.create_table(
        "pin_recovery_audits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["wallet.accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="wallet",
    )
    op.create_index(
        "idx_pin_recovery_audits_account",
        "pin_recovery_audits",
        ["account_id", "created_at"],
        unique=False,
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_pin_recovery_audits_account",
        table_name="pin_recovery_audits",
        schema="wallet",
    )
    op.drop_table("pin_recovery_audits", schema="wallet")
    op.drop_index(
        "idx_transfer_otp_account_created",
        table_name="transfer_otp_challenges",
        schema="wallet",
    )
    op.drop_table("transfer_otp_challenges", schema="wallet")
