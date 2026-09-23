"""PostgreSQL adapters for audited Backoffice commands."""

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from payfund_app.core.errors import Conflict, NotFound, UnprocessableEntity
from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.application.backoffice_commands import (
    BackofficeCommand,
)
from payfund_app.modules.payments.infra.models import (
    BackofficeCommandRecord,
    PaymentOutboxRecord,
)
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentAttemptRepository,
    PaymentIntentRepository,
)


def _to_command(row: BackofficeCommandRecord) -> BackofficeCommand:
    return BackofficeCommand(
        id=row.id,
        client_id=row.client_id,
        command_id=row.command_id,
        actor_user_id=row.actor_user_id,
        idempotency_key=row.idempotency_key,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        reason=row.reason,
        request_fingerprint=row.request_fingerprint,
        status=row.status,
        result=row.result,
    )


class SqlBackofficeCommandRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_replay(self, *, client_id: str, command_id: str, idempotency_key: str):
        row = self.session.scalar(
            select(BackofficeCommandRecord).where(
                BackofficeCommandRecord.client_id == client_id,
                or_(
                    BackofficeCommandRecord.command_id == command_id,
                    BackofficeCommandRecord.idempotency_key == idempotency_key,
                ),
            )
        )
        return _to_command(row) if row else None

    def create(self, **values) -> BackofficeCommand:
        row = BackofficeCommandRecord(status="processing", result={}, **values)
        self.session.add(row)
        self.session.flush()
        return _to_command(row)

    def complete(self, command: BackofficeCommand, result: dict) -> BackofficeCommand:
        row = self.session.get(BackofficeCommandRecord, command.id)
        row.status = "completed"
        row.result = result
        row.updated_at = datetime.now().astimezone()
        self.session.flush()
        return _to_command(row)

    def get(self, *, client_id: str, command_id: str):
        row = self.session.scalar(
            select(BackofficeCommandRecord).where(
                BackofficeCommandRecord.client_id == client_id,
                BackofficeCommandRecord.command_id == command_id,
            )
        )
        return _to_command(row) if row else None


class SqlBackofficePaymentOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def retry_callback(self, *, payment_intent_id: uuid.UUID, event_id: uuid.UUID) -> dict:
        row = self.session.scalar(
            select(PaymentOutboxRecord)
            .where(PaymentOutboxRecord.id == event_id)
            .with_for_update()
        )
        if row is None or row.aggregate_id != payment_intent_id:
            raise NotFound("Callback de paiement introuvable.", code="CALLBACK_NOT_FOUND")
        if row.status != "dead_letter":
            raise Conflict(
                "Seul un callback en dead letter peut être remis en file.",
                code="CALLBACK_NOT_RETRYABLE",
            )
        row.status = "pending"
        row.next_attempt_at = datetime.now().astimezone()
        row.locked_at = None
        row.last_error = None
        self.session.flush()
        return {"event_id": str(row.id), "delivery_status": "pending"}

    def record_settlement(
        self,
        *,
        payment_intent_id: uuid.UUID,
        amount: int,
        settlement_reference: str,
    ) -> dict:
        intent = PaymentIntentRepository(self.session).get(payment_intent_id, for_update=True)
        if intent is None:
            raise NotFound("PaymentIntent introuvable.", code="PAYMENT_INTENT_NOT_FOUND")
        attempts = PaymentAttemptRepository(self.session).list_for_intent(payment_intent_id)
        if not attempts:
            raise Conflict(
                "Aucune tentative provider associée au paiement.",
                code="SETTLEMENT_ATTEMPT_MISSING",
            )
        processor = attempts[-1].processor
        try:
            PaymentAccountingService(FinancialLedgerRepository(self.session)).record_settlement(
                intent,
                processor=processor,
                amount=amount,
                settlement_reference=settlement_reference,
            )
        except ValueError as exc:
            raise UnprocessableEntity(
                str(exc), code="SETTLEMENT_AMOUNT_INVALID"
            ) from exc
        return {
            "payment_intent_id": str(payment_intent_id),
            "amount": amount,
            "currency": intent.money.currency,
            "processor": processor,
            "settlement_reference": settlement_reference,
        }
