"""Références KYC vers les fichiers externes.

Revision ID: 0009_kyc_documents_table
Revises: 0008_webhook_inbox_events_table
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_kyc_documents_table"
down_revision: str | None = "0008_webhook_inbox_events_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kyc_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", sa.String(100), nullable=False, unique=True),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("source_module", sa.String(30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("user_id <> '00000000-0000-0000-0000-000000000000'", name="ck_kyc_user"),
        sa.CheckConstraint("file_id <> ''", name="ck_kyc_file_id"),
        sa.CheckConstraint(
            "status IN ('pending','verified','rejected')", name="ck_kyc_status"
        ),
        schema="wallet",
    )
    op.create_index(
        "idx_kyc_documents_user",
        "kyc_documents",
        ["user_id", "created_at"],
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index("idx_kyc_documents_user", table_name="kyc_documents", schema="wallet")
    op.drop_table("kyc_documents", schema="wallet")
