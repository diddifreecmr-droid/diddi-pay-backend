"""Journal des callbacks provider vus.

Revision ID: 0008_webhook_inbox_events_table
Revises: 0007_reconciliation_logs_table
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_webhook_inbox_events_table"
down_revision: str | None = "0007_reconciliation_logs_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_inbox_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("event_key", sa.String(180), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'received'")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("provider <> ''", name="ck_webhook_inbox_provider"),
        sa.CheckConstraint("event_key <> ''", name="ck_webhook_inbox_event_key"),
        sa.CheckConstraint("status IN ('received','processed')", name="ck_webhook_inbox_status"),
        schema="wallet",
    )
    op.create_index(
        "idx_webhook_inbox_provider_event",
        "webhook_inbox_events",
        ["provider", "event_key"],
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_webhook_inbox_provider_event",
        table_name="webhook_inbox_events",
        schema="wallet",
    )
    op.drop_table("webhook_inbox_events", schema="wallet")
