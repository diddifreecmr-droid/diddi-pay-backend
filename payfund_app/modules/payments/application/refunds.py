"""Idempotent provider-neutral refund orchestration."""

import uuid
from dataclasses import dataclass

from payfund_app.modules.payments.application.errors import (
    IdempotencyConflict,
    PaymentNotFound,
    PaymentOperationConflict,
    ProcessorCallUncertain,
)
from payfund_app.modules.payments.application.fingerprints import request_fingerprint
from payfund_app.modules.payments.application.ports import RefundRequest
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentIntentStatus,
    Refund,
    RefundStatus,
)


@dataclass(frozen=True, slots=True)
class CreateRefundCommand:
    client_id: str
    payment_intent_id: uuid.UUID
    amount: int
    idempotency_key: str
    reason: str | None = None

    def fingerprint(self) -> str:
        return request_fingerprint(
            {
                "client_id": self.client_id,
                "payment_intent_id": self.payment_intent_id,
                "amount": self.amount,
                "reason": self.reason,
            }
        )


class RefundUseCases:
    def __init__(self, intents, attempts, refunds, processors, uow) -> None:
        self.intents = intents
        self.attempts = attempts
        self.refunds = refunds
        self.processors = processors
        self.uow = uow

    def create(self, command: CreateRefundCommand):
        fingerprint = command.fingerprint()
        existing = self.refunds.get_by_idempotency(
            command.client_id, command.idempotency_key
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflict()
            return existing

        intent = self.intents.get(command.payment_intent_id, for_update=True)
        if intent is None or intent.client_id != command.client_id:
            raise PaymentNotFound()
        if intent.status not in {
            PaymentIntentStatus.SUCCEEDED,
            PaymentIntentStatus.PARTIALLY_REFUNDED,
        }:
            raise PaymentOperationConflict("only a succeeded payment can be refunded")
        attempts = self.attempts.list_for_intent(intent.id)
        attempt = next(
            (item for item in reversed(attempts) if item.status == AttemptStatus.SUCCEEDED),
            None,
        )
        if attempt is None or not attempt.provider_reference:
            raise PaymentOperationConflict("successful provider attempt not found")
        active_total = self.refunds.total_active_for_intent(intent.id)
        if command.amount <= 0 or active_total + command.amount > intent.money.amount:
            raise PaymentOperationConflict("refund exceeds the captured amount")

        refund = Refund(
            payment_intent_id=intent.id,
            payment_attempt_id=attempt.id,
            money=Money(command.amount, intent.money.currency),
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            reason=command.reason,
        )
        self.refunds.add(refund, client_id=command.client_id, processor=attempt.processor)
        self.uow.commit()

        processor = self.processors.get(attempt.processor)
        try:
            result = processor.refund_payment(
                RefundRequest(
                    refund_id=refund.id,
                    provider_reference=attempt.provider_reference,
                    money=refund.money,
                    reason=refund.reason,
                )
            )
        except ProcessorCallUncertain:
            refund.transition_to(RefundStatus.PROCESSING)
            refund.provider_status = "unknown"
        else:
            refund.provider_reference = result.provider_reference
            refund.provider_status = result.provider_status
            refund.transition_to(result.status)
        if refund.status == RefundStatus.SUCCEEDED:
            intent.apply_refund(refund.money.amount)
            self.intents.save(intent)
        self.refunds.save(refund)
        self.uow.commit()
        return refund
