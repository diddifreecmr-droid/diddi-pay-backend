"""Bounded, read-only search for missing capture and module notification."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.application.integrity import PaymentIntegrityGap
from payfund_app.modules.payments.infra.models import (
    FinancialJournalRecord,
    PaymentIntentRecord,
    PaymentOutboxRecord,
)


class SqlPaymentIntegrityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_gaps(self, limit: int) -> list[PaymentIntegrityGap]:
        captured = (
            select(FinancialJournalRecord.id)
            .where(
                FinancialJournalRecord.payment_intent_id == PaymentIntentRecord.id,
                FinancialJournalRecord.event_type == "capture",
            )
            .exists()
        )
        notified = (
            select(PaymentOutboxRecord.id)
            .where(
                PaymentOutboxRecord.aggregate_id == PaymentIntentRecord.id,
                PaymentOutboxRecord.event_type == "payment.succeeded",
            )
            .exists()
        )
        rows = self.session.execute(
            select(PaymentIntentRecord.id, (~captured).label("missing_capture"), (~notified).label("missing_callback"))
            .where(
                PaymentIntentRecord.status.in_(("succeeded", "partially_refunded", "refunded")),
                or_(~captured, ~notified),
            )
            .order_by(PaymentIntentRecord.created_at, PaymentIntentRecord.id)
            .limit(limit)
        )
        return [PaymentIntegrityGap(row.id, row.missing_capture, row.missing_callback) for row in rows]
