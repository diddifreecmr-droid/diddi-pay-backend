"""store whether a refund must be taken back from the payee (ticket 121)"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261128_refund_recover_from_payee"
down_revision: str | None = "20261121_cinetpay_webhook_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Default false = the human's rule: a refund does not take money back from the payee
    # (e.g. the driver) unless an admin explicitly asks for it. Existing rows get false.
    op.add_column(
        "refunds",
        sa.Column(
            "recover_from_payee",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="payments",
    )


def downgrade() -> None:
    op.drop_column("refunds", "recover_from_payee", schema="payments")
