"""Read-only aggregate over immutable financial journal events."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.infra.models import FinancialJournalRecord


class SqlPaymentDailySummaryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def totals(self, start: datetime, end: datetime) -> dict[str, tuple[int, int]]:
        rows = self.session.execute(
            select(
                FinancialJournalRecord.event_type,
                func.count(FinancialJournalRecord.id),
                func.coalesce(func.sum(FinancialJournalRecord.amount), 0),
            )
            .where(
                FinancialJournalRecord.created_at >= start,
                FinancialJournalRecord.created_at < end,
                FinancialJournalRecord.currency == "XOF",
                FinancialJournalRecord.event_type.in_(("capture", "refund")),
            )
            .group_by(FinancialJournalRecord.event_type)
        )
        return {kind: (int(count), int(amount)) for kind, count, amount in rows}
