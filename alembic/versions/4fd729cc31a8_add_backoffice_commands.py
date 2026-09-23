"""add audited backoffice commands

Revision ID: 4fd729cc31a8
Revises: d10fcb87a211
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4fd729cc31a8"
down_revision: str | None = "d10fcb87a211"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backoffice_commands",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('processing','completed','failed')",
            name="ck_backoffice_command_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "command_id", name="uq_backoffice_command_id"),
        sa.UniqueConstraint(
            "client_id", "idempotency_key", name="uq_backoffice_command_idempotency"
        ),
        schema="payments",
    )
    op.create_index(
        "idx_backoffice_command_target",
        "backoffice_commands",
        ["target_type", "target_id", "created_at"],
        schema="payments",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_backoffice_command_target",
        table_name="backoffice_commands",
        schema="payments",
    )
    op.drop_table("backoffice_commands", schema="payments")
