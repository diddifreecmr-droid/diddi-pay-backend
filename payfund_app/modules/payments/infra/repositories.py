"""PostgreSQL repositories for the payment domain."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    NextAction,
    NextActionType,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
    Refund,
)
from payfund_app.modules.payments.infra.models import (
    PaymentAttemptRecord,
    PaymentIntentRecord,
    ProviderEventRecord,
    PaymentOutboxRecord,
    RefundRecord,
)


def _action_to_json(action: NextAction | None) -> dict | None:
    if action is None:
        return None
    return {
        "type": str(action.type),
        "url": action.url,
        "instructions": action.instructions,
        "expires_at": action.expires_at.isoformat() if action.expires_at else None,
    }


def _action_from_json(value: dict | None) -> NextAction | None:
    if value is None:
        return None
    return NextAction(
        type=NextActionType(value["type"]),
        url=value.get("url"),
        instructions=value.get("instructions"),
        expires_at=datetime.fromisoformat(value["expires_at"])
        if value.get("expires_at")
        else None,
    )


class PaymentIntentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, intent: PaymentIntent) -> PaymentIntent:
        self.session.add(
            PaymentIntentRecord(
                id=intent.id,
                client_id=intent.client_id,
                business_reference=intent.business_reference,
                payer_user_id=intent.payer_user_id,
                payee_user_id=intent.payee_user_id,
                amount=intent.money.amount,
                currency=intent.money.currency,
                status=str(intent.status),
                idempotency_key=intent.idempotency_key,
                request_fingerprint=intent.request_fingerprint,
                description=intent.description,
                metadata_json=intent.metadata,
                refunded_amount=intent.refunded_amount,
                created_at=intent.created_at,
                updated_at=intent.updated_at,
            )
        )
        self.session.flush()
        return intent

    def get(self, intent_id: uuid.UUID, *, for_update: bool = False) -> PaymentIntent | None:
        query = select(PaymentIntentRecord).where(PaymentIntentRecord.id == intent_id)
        if for_update:
            query = query.with_for_update()
        row = self.session.scalar(query)
        return self._to_domain(row) if row else None

    def get_by_idempotency(self, client_id: str, key: str) -> PaymentIntent | None:
        row = self.session.scalar(
            select(PaymentIntentRecord).where(
                PaymentIntentRecord.client_id == client_id,
                PaymentIntentRecord.idempotency_key == key,
            )
        )
        return self._to_domain(row) if row else None

    def list_for_client(self, client_id: str, *, limit: int = 50) -> list[PaymentIntent]:
        rows = self.session.scalars(
            select(PaymentIntentRecord)
            .where(PaymentIntentRecord.client_id == client_id)
            .order_by(PaymentIntentRecord.created_at.desc())
            .limit(limit)
        )
        return [self._to_domain(row) for row in rows]

    def save(self, intent: PaymentIntent) -> PaymentIntent:
        row = self.session.get(PaymentIntentRecord, intent.id)
        if row is None:
            raise LookupError(f"payment intent {intent.id} does not exist")
        row.status = str(intent.status)
        row.refunded_amount = intent.refunded_amount
        row.updated_at = intent.updated_at
        self.session.flush()
        return intent

    @staticmethod
    def _to_domain(row: PaymentIntentRecord) -> PaymentIntent:
        return PaymentIntent(
            id=row.id,
            client_id=row.client_id,
            business_reference=row.business_reference,
            payer_user_id=row.payer_user_id,
            payee_user_id=row.payee_user_id,
            money=Money(row.amount, row.currency),
            status=PaymentIntentStatus(row.status),
            idempotency_key=row.idempotency_key,
            request_fingerprint=row.request_fingerprint,
            description=row.description,
            metadata=row.metadata_json,
            refunded_amount=row.refunded_amount,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class PaymentAttemptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, attempt: PaymentAttempt) -> PaymentAttempt:
        self.session.add(
            PaymentAttemptRecord(
                id=attempt.id,
                payment_intent_id=attempt.payment_intent_id,
                attempt_number=attempt.attempt_number,
                processor=attempt.processor,
                channel=attempt.channel,
                network=attempt.network,
                amount=attempt.money.amount,
                currency=attempt.money.currency,
                status=str(attempt.status),
                provider_reference=attempt.provider_reference,
                provider_status=attempt.provider_status,
                next_action=_action_to_json(attempt.next_action),
                failure_code=attempt.failure_code,
                failure_message=attempt.failure_message,
                created_at=attempt.created_at,
                updated_at=attempt.updated_at,
            )
        )
        self.session.flush()
        return attempt

    def get(self, attempt_id: uuid.UUID, *, for_update: bool = False) -> PaymentAttempt | None:
        query = select(PaymentAttemptRecord).where(PaymentAttemptRecord.id == attempt_id)
        if for_update:
            query = query.with_for_update()
        row = self.session.scalar(query)
        return self._to_domain(row) if row else None

    def get_by_provider_reference(
        self, processor: str, provider_reference: str, *, for_update: bool = False
    ) -> PaymentAttempt | None:
        query = select(PaymentAttemptRecord).where(
            PaymentAttemptRecord.processor == processor,
            PaymentAttemptRecord.provider_reference == provider_reference,
        )
        if for_update:
            query = query.with_for_update()
        row = self.session.scalar(query)
        return self._to_domain(row) if row else None

    def list_for_intent(self, intent_id: uuid.UUID) -> list[PaymentAttempt]:
        rows = self.session.scalars(
            select(PaymentAttemptRecord)
            .where(PaymentAttemptRecord.payment_intent_id == intent_id)
            .order_by(PaymentAttemptRecord.attempt_number)
        )
        return [self._to_domain(row) for row in rows]

    def next_attempt_number(self, intent_id: uuid.UUID) -> int:
        attempts = self.list_for_intent(intent_id)
        return attempts[-1].attempt_number + 1 if attempts else 1

    def pending_for_reconciliation(
        self, *, older_than: datetime, limit: int = 100
    ) -> list[PaymentAttempt]:
        rows = self.session.scalars(
            select(PaymentAttemptRecord)
            .where(
                PaymentAttemptRecord.status.in_(
                    ["requires_action", "processing", "unknown"]
                ),
                PaymentAttemptRecord.provider_reference.is_not(None),
                PaymentAttemptRecord.updated_at <= older_than,
            )
            .order_by(PaymentAttemptRecord.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [self._to_domain(row) for row in rows]

    def save(self, attempt: PaymentAttempt) -> PaymentAttempt:
        row = self.session.get(PaymentAttemptRecord, attempt.id)
        if row is None:
            raise LookupError(f"payment attempt {attempt.id} does not exist")
        row.status = str(attempt.status)
        row.provider_reference = attempt.provider_reference
        row.provider_status = attempt.provider_status
        row.next_action = _action_to_json(attempt.next_action)
        row.failure_code = attempt.failure_code
        row.failure_message = attempt.failure_message
        row.updated_at = attempt.updated_at
        self.session.flush()
        return attempt

    @staticmethod
    def _to_domain(row: PaymentAttemptRecord) -> PaymentAttempt:
        return PaymentAttempt(
            id=row.id,
            payment_intent_id=row.payment_intent_id,
            processor=row.processor,
            channel=row.channel,
            network=row.network,
            money=Money(row.amount, row.currency),
            attempt_number=row.attempt_number,
            status=AttemptStatus(row.status),
            provider_reference=row.provider_reference,
            provider_status=row.provider_status,
            next_action=_action_from_json(row.next_action),
            failure_code=row.failure_code,
            failure_message=row.failure_message,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ProviderEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, processor: str, event_key: str) -> ProviderEventRecord | None:
        return self.session.scalar(
            select(ProviderEventRecord).where(
                ProviderEventRecord.processor == processor,
                ProviderEventRecord.event_key == event_key,
            )
        )

    def add(
        self,
        *,
        processor: str,
        event_key: str,
        event_type: str,
        payload_hash: str,
        payload: dict,
    ) -> ProviderEventRecord:
        row = ProviderEventRecord(
            processor=processor,
            event_key=event_key,
            event_type=event_type,
            payload_hash=payload_hash,
            payload=payload,
            status="received",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def mark(
        self,
        row: ProviderEventRecord,
        *,
        status: str,
        payment_attempt_id: uuid.UUID | None = None,
        error_message: str | None = None,
    ) -> ProviderEventRecord:
        row.status = status
        row.payment_attempt_id = payment_attempt_id
        row.error_message = error_message
        if status in {"processed", "ignored", "failed"}:
            row.processed_at = datetime.now().astimezone()
        self.session.flush()
        return row


class RefundRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, refund: Refund, *, client_id: str, processor: str) -> Refund:
        self.session.add(
            RefundRecord(
                id=refund.id,
                payment_intent_id=refund.payment_intent_id,
                payment_attempt_id=refund.payment_attempt_id,
                client_id=client_id,
                processor=processor,
                amount=refund.money.amount,
                currency=refund.money.currency,
                status=str(refund.status),
                idempotency_key=refund.idempotency_key,
                request_fingerprint=refund.request_fingerprint,
                reason=refund.reason,
                provider_reference=refund.provider_reference,
                provider_status=refund.provider_status,
                created_at=refund.created_at,
                updated_at=refund.updated_at,
            )
        )
        self.session.flush()
        return refund

    def get_by_idempotency(self, client_id: str, key: str) -> RefundRecord | None:
        return self.session.scalar(
            select(RefundRecord).where(
                RefundRecord.client_id == client_id,
                RefundRecord.idempotency_key == key,
            )
        )

    def total_active_for_intent(self, payment_intent_id: uuid.UUID) -> int:
        value = self.session.scalar(
            select(func.coalesce(func.sum(RefundRecord.amount), 0)).where(
                RefundRecord.payment_intent_id == payment_intent_id,
                RefundRecord.status.in_(("pending", "processing", "succeeded")),
            )
        )
        return int(value or 0)

    def save(self, refund: Refund) -> Refund:
        row = self.session.get(RefundRecord, refund.id)
        if row is None:
            raise LookupError(f"refund not found: {refund.id}")
        row.status = str(refund.status)
        row.provider_reference = refund.provider_reference
        row.provider_status = refund.provider_status
        row.updated_at = refund.updated_at
        self.session.flush()
        return refund


class PaymentOutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(self, *, client_id: str, event_type: str, aggregate_id: uuid.UUID, payload: dict):
        row = PaymentOutboxRecord(
            client_id=client_id,
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload=payload,
            status="pending",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def pending(self, limit: int = 100) -> list[PaymentOutboxRecord]:
        now = datetime.now().astimezone()
        statement = (
            select(PaymentOutboxRecord)
            .where(
                PaymentOutboxRecord.status == "pending",
                PaymentOutboxRecord.next_attempt_at <= now,
            )
            .order_by(PaymentOutboxRecord.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self.session.scalars(statement))

    def delivered(self, row: PaymentOutboxRecord) -> None:
        row.status = "delivered"
        row.delivered_at = datetime.now().astimezone()
        row.last_error = None
        self.session.flush()

    def failed(
        self, row: PaymentOutboxRecord, error: str, *, max_attempts: int = 10
    ) -> None:
        row.attempts += 1
        row.last_error = error[:255]
        if row.attempts >= max_attempts:
            row.status = "dead_letter"
        else:
            delay = min(3600, 2 ** min(row.attempts, 10))
            row.next_attempt_at = datetime.now().astimezone() + timedelta(seconds=delay)
        self.session.flush()
