"""persist provider callback metadata for CinetPay notifications"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "20261121_cinetpay_webhook_metadata"
down_revision: str | None = "20261119_pawapay_callbacks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_attempts",
        sa.Column(
            "provider_metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="payments",
    )


def downgrade() -> None:
    op.drop_column("payment_attempts", "provider_metadata", schema="payments")
