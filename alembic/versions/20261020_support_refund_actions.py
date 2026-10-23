"""add delegated Support refund action audit records"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261020_support_refund_actions"
down_revision: str | None = "b2d4f6a81c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_refund_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), primary_key=True),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("support_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_user_id", sa.String(length=128), nullable=False),
        sa.Column("delegation_id", sa.String(length=128), nullable=False),
        sa.Column("payment_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refund_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('processing','succeeded','failed','unknown')",
            name="ck_support_refund_action_status",
        ),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_support_refund_action_idempotency"),
        sa.UniqueConstraint("client_id", "command_id", name="uq_support_refund_action_command"),
        schema="payments",
    )
    op.create_index(
        "idx_support_refund_action_case",
        "support_refund_actions",
        ["support_case_id", "created_at"],
        schema="payments",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_support_refund_action_case",
        table_name="support_refund_actions",
        schema="payments",
    )
    op.drop_table("support_refund_actions", schema="payments")
