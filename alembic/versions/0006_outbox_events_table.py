"""Ajout de la table outbox pour les événements internes durables.

Revision ID: 0006_outbox_events_table
Revises: 0005_business_reference_on_transactions
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_outbox_events_table"
down_revision: str | None = "0005_business_reference_on_transactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("event_name", sa.String(80), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('pending','published')", name="ck_outbox_status"),
        schema="wallet",
    )
    op.create_index(
        "idx_outbox_status_created_at",
        "outbox_events",
        ["status", "created_at"],
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_status_created_at", table_name="outbox_events", schema="wallet")
    op.drop_table("outbox_events", schema="wallet")
