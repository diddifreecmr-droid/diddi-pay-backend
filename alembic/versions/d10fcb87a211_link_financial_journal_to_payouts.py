"""link financial journals to payouts

Revision ID: d10fcb87a211
Revises: 9c41a72d6e10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d10fcb87a211"
down_revision: str | None = "9c41a72d6e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "financial_journals",
        "payment_intent_id",
        existing_type=sa.UUID(),
        nullable=True,
        schema="payments",
    )
    op.add_column(
        "financial_journals",
        sa.Column("payout_id", sa.UUID(), nullable=True),
        schema="payments",
    )
    op.create_foreign_key(
        "fk_financial_journal_payout",
        "financial_journals",
        "payouts",
        ["payout_id"],
        ["id"],
        source_schema="payments",
        referent_schema="payments",
    )
    op.create_check_constraint(
        "ck_financial_journal_single_owner",
        "financial_journals",
        "num_nonnulls(payment_intent_id, payout_id) = 1",
        schema="payments",
    )
    op.create_index(
        "idx_financial_journal_payout",
        "financial_journals",
        ["payout_id", "created_at"],
        schema="payments",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_financial_journal_payout",
        table_name="financial_journals",
        schema="payments",
    )
    op.drop_constraint(
        "ck_financial_journal_single_owner",
        "financial_journals",
        schema="payments",
        type_="check",
    )
    op.drop_constraint(
        "fk_financial_journal_payout",
        "financial_journals",
        schema="payments",
        type_="foreignkey",
    )
    op.drop_column("financial_journals", "payout_id", schema="payments")
    op.alter_column(
        "financial_journals",
        "payment_intent_id",
        existing_type=sa.UUID(),
        nullable=False,
        schema="payments",
    )
