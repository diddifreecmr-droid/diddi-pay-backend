"""SQL read model for the DiddiPay Backoffice surface."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.application.backoffice import (
    BackofficeAttempt,
    BackofficePayment,
)
from payfund_app.modules.payments.infra.models import (
    PaymentAttemptRecord,
    PaymentIntentRecord,
)
from payfund_app.modules.payments.infra.repositories import FinancialLedgerRepository


class SqlBackofficePaymentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, *, page: int, page_size: int, status: str | None):
        filters = []
        if status:
            filters.append(PaymentIntentRecord.status == status)
        total = self.session.scalar(
            select(func.count(PaymentIntentRecord.id)).where(*filters)
        ) or 0
        rows = self.session.scalars(
            select(PaymentIntentRecord)
            .where(*filters)
            .order_by(PaymentIntentRecord.updated_at.desc(), PaymentIntentRecord.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return [self._payment(row, include_detail=False) for row in rows], int(total)

    def get(self, payment_intent_id: uuid.UUID) -> BackofficePayment | None:
        row = self.session.get(PaymentIntentRecord, payment_intent_id)
        return self._payment(row, include_detail=True) if row else None

    def _payment(self, row: PaymentIntentRecord, *, include_detail: bool) -> BackofficePayment:
        attempts: tuple[BackofficeAttempt, ...] = ()
        financial_summary = None
        if include_detail:
            attempt_rows = self.session.scalars(
                select(PaymentAttemptRecord)
                .where(PaymentAttemptRecord.payment_intent_id == row.id)
                .order_by(PaymentAttemptRecord.attempt_number)
            )
            attempts = tuple(
                BackofficeAttempt(
                    id=attempt.id,
                    processor=attempt.processor,
                    status=attempt.status,
                    provider_reference=attempt.provider_reference,
                    provider_status=attempt.provider_status,
                    failure_code=attempt.failure_code,
                    created_at=attempt.created_at,
                    updated_at=attempt.updated_at,
                )
                for attempt in attempt_rows
            )
            financial_summary = FinancialLedgerRepository(self.session).summary(row.id)
        return BackofficePayment(
            id=row.id,
            client_id=row.client_id,
            business_reference=row.business_reference,
            amount=row.amount,
            currency=row.currency,
            status=row.status,
            refunded_amount=row.refunded_amount,
            created_at=row.created_at,
            updated_at=row.updated_at,
            attempts=attempts,
            financial_summary=financial_summary,
        )
