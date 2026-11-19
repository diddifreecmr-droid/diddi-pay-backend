"""allow provider event inbox rows to own refund callbacks"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261119_pawapay_callbacks"
down_revision: str | None = "20261119_payout_webhook_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provider_events",
        sa.Column("refund_id", sa.UUID(), nullable=True),
        schema="payments",
    )
    op.create_foreign_key(
        "fk_provider_event_refund",
        "provider_events",
        "refunds",
        ["refund_id"],
        ["id"],
        source_schema="payments",
        referent_schema="payments",
    )
    op.drop_constraint(
        "ck_provider_events_single_owner",
        "provider_events",
        schema="payments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_events_single_owner",
        "provider_events",
        "num_nonnulls(payment_attempt_id, payout_id, refund_id) <= 1",
        schema="payments",
    )
    op.create_index(
        "idx_provider_events_refund",
        "provider_events",
        ["refund_id", "created_at"],
        schema="payments",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_provider_events_refund",
        table_name="provider_events",
        schema="payments",
    )
    op.drop_constraint(
        "ck_provider_events_single_owner",
        "provider_events",
        schema="payments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_events_single_owner",
        "provider_events",
        "num_nonnulls(payment_attempt_id, payout_id) <= 1",
        schema="payments",
    )
    op.drop_constraint(
        "fk_provider_event_refund",
        "provider_events",
        schema="payments",
        type_="foreignkey",
    )
    op.drop_column("provider_events", "refund_id", schema="payments")
