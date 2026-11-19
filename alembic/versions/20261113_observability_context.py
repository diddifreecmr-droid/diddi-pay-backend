"""Store trace metadata separately from signed business payloads."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261113_observability_context"
down_revision = "20261020_support_refund_actions"
branch_labels = None
depends_on = None


def upgrade():
    for schema in ("payments", "wallet"):
        op.add_column("outbox_events", sa.Column("telemetry_context", JSONB(), nullable=True), schema=schema)


def downgrade():
    for schema in ("payments", "wallet"):
        op.drop_column("outbox_events", "telemetry_context", schema=schema)
