"""Journal des r\xE9conciliations provider.

Revision ID: 0007_reconciliation_logs_table
Revises: 0006_outbox_events_table
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_reconciliation_logs_table"
down_revision: str | None = "0006_outbox_events_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_reference", sa.String(100), nullable=True),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("provider <> ''", name="ck_reconciliation_logs_provider"),
        sa.CheckConstraint("event <> ''", name="ck_reconciliation_logs_event"),
        sa.CheckConstraint("outcome <> ''", name="ck_reconciliation_logs_outcome"),
        schema="wallet",
    )
    op.create_index(
        "idx_reconciliation_logs_transaction",
        "reconciliation_logs",
        ["transaction_id", "created_at"],
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_reconciliation_logs_transaction",
        table_name="reconciliation_logs",
        schema="wallet",
    )
    op.drop_table("reconciliation_logs", schema="wallet")
