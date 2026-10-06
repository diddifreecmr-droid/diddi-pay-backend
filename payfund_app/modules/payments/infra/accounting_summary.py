"""SQL projection for the Pilotage accounting contract."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.application.accounting_summary import AccountingSourceRow
from payfund_app.modules.payments.infra.models import (
    FinancialJournalRecord,
    PaymentAttemptRecord,
    PaymentIntentRecord,
    PayoutRecord,
)


class SqlPaymentAccountingSummaryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def rows(self, start: datetime, end: datetime) -> list[AccountingSourceRow]:
        successful_processor = (
            select(PaymentAttemptRecord.processor)
            .where(
                PaymentAttemptRecord.payment_intent_id
                == FinancialJournalRecord.payment_intent_id,
                PaymentAttemptRecord.status == "succeeded",
            )
            .limit(1)
            .scalar_subquery()
        )
        payment_rows = self.session.execute(
            select(
                PaymentIntentRecord.client_id,
                func.coalesce(successful_processor, "unknown"),
                PaymentIntentRecord.business_reference,
                PaymentIntentRecord.metadata_json,
                FinancialJournalRecord.event_type,
                func.count(FinancialJournalRecord.id),
                func.sum(FinancialJournalRecord.amount),
            )
            .join(
                PaymentIntentRecord,
                PaymentIntentRecord.id == FinancialJournalRecord.payment_intent_id,
            )
            .where(
                FinancialJournalRecord.created_at >= start,
                FinancialJournalRecord.created_at < end,
                FinancialJournalRecord.currency == "XOF",
                FinancialJournalRecord.event_type.in_(
                    ("capture", "refund", "processor_fee", "settlement")
                ),
            )
            .group_by(
                PaymentIntentRecord.client_id,
                PaymentIntentRecord.business_reference,
                PaymentIntentRecord.metadata_json,
                FinancialJournalRecord.payment_intent_id,
                FinancialJournalRecord.event_type,
            )
        )
        payout_rows = self.session.execute(
            select(
                PayoutRecord.client_id,
                PayoutRecord.processor,
                PayoutRecord.business_reference,
                PayoutRecord.metadata_json,
                FinancialJournalRecord.event_type,
                func.count(FinancialJournalRecord.id),
                func.sum(FinancialJournalRecord.amount),
            )
            .join(PayoutRecord, PayoutRecord.id == FinancialJournalRecord.payout_id)
            .where(
                FinancialJournalRecord.created_at >= start,
                FinancialJournalRecord.created_at < end,
                FinancialJournalRecord.currency == "XOF",
                FinancialJournalRecord.event_type == "payout",
            )
            .group_by(
                PayoutRecord.client_id,
                PayoutRecord.processor,
                PayoutRecord.business_reference,
                PayoutRecord.metadata_json,
                FinancialJournalRecord.event_type,
            )
        )
        return [self._row(row) for row in [*payment_rows, *payout_rows]]

    @staticmethod
    def _row(row) -> AccountingSourceRow:
        return AccountingSourceRow(
            client_id=row[0],
            processor=row[1],
            business_reference=row[2],
            metadata=row[3] or {},
            event_type=row[4],
            transactions_count=int(row[5]),
            amount_xof=int(row[6]),
        )
