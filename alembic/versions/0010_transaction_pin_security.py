"""Transaction PIN security.

Revision ID: 0010_transaction_pin_security
Revises: 0009_kyc_documents_table
Create Date: 2026-08-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_transaction_pin_security"
down_revision = "0009_kyc_documents_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transaction_pins",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pin_hash", sa.String(length=128), nullable=False),
        sa.Column("pin_salt", sa.String(length=64), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("failed_attempts >= 0", name="ck_pin_failed_attempts"),
        sa.ForeignKeyConstraint(["account_id"], ["wallet.accounts.id"]),
        sa.PrimaryKeyConstraint("account_id"),
        schema="wallet",
    )
    op.create_table(
        "transaction_pin_recovery_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["wallet.accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
        schema="wallet",
    )
    op.create_index(
        "idx_pin_recovery_codes_account",
        "transaction_pin_recovery_codes",
        ["account_id", "created_at"],
        unique=False,
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_pin_recovery_codes_account",
        table_name="transaction_pin_recovery_codes",
        schema="wallet",
    )
    op.drop_table("transaction_pin_recovery_codes", schema="wallet")
    op.drop_table("transaction_pins", schema="wallet")
