"""SQL projection for the limited DiddiSupport payment context."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.application.support_context import (
    SupportAttemptContext,
    SupportPaymentContext,
)
from payfund_app.modules.payments.infra.models import (
    PaymentAttemptRecord,
    PaymentIntentRecord,
)


class SqlSupportPaymentContextRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, payment_intent_id: uuid.UUID) -> SupportPaymentContext | None:
        intent = self.session.get(PaymentIntentRecord, payment_intent_id)
        if intent is None:
            return None

        attempt = self.session.scalar(
            select(PaymentAttemptRecord)
            .where(PaymentAttemptRecord.payment_intent_id == payment_intent_id)
            .order_by(PaymentAttemptRecord.updated_at.desc(), PaymentAttemptRecord.id.desc())
            .limit(1)
        )
        latest_attempt = (
            SupportAttemptContext(
                status=attempt.status,
                provider_status=attempt.provider_status,
                failure_code=attempt.failure_code,
                occurred_at=attempt.updated_at,
            )
            if attempt is not None
            else None
        )
        if intent.refunded_amount == 0:
            refund_status = "none"
        elif intent.refunded_amount < intent.amount:
            refund_status = "partially_refunded"
        else:
            refund_status = "refunded"
        return SupportPaymentContext(
            payment_intent_id=intent.id,
            business_reference=intent.business_reference,
            payment_status=intent.status,
            amount=intent.amount,
            currency=intent.currency,
            refunded_amount=intent.refunded_amount,
            refund_status=refund_status,
            latest_attempt=latest_attempt,
            updated_at=intent.updated_at,
        )
