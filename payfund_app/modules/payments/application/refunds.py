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
from payfund_app.modules.payments.application.ports import PaymentDirection, RefundRequest
from payfund_app.modules.payments.application.processor_router import ProcessorRoutingError
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentIntentStatus,
    Refund,
    RefundStatus,
)


def _enqueue_refund_event(
    outbox,
    *,
    owner_client_id: str,
    intent,
    refund,
    status: str,
    processor: str | None,
) -> None:
    """Publish one provider-neutral refund result for the owning module.

    The outbox event is deliberately emitted by the application layer after the
    refund transition has been applied. Provider adapters only normalize their
    own responses; modules consume this stable contract.
    """
    if outbox is None:
        return
    outbox.enqueue(
        client_id=owner_client_id,
        event_type=f"diddipay.refund.{status}.v1",
        aggregate_id=refund.id,
        payload={
            "event_version": "1",
            "refund_id": str(refund.id),
            "payment_intent_id": str(refund.payment_intent_id),
            "owner_client_id": owner_client_id,
            "business_reference": intent.business_reference,
            "amount": refund.money.amount,
            "currency": refund.money.currency,
            "status": status,
            "provider_status": refund.provider_status,
            "provider_reference": refund.provider_reference,
            "processor": processor,
            "reason": refund.reason,
            "occurred_at": refund.updated_at.isoformat(),
        },
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
    def __init__(
        self,
        intents,
        attempts,
        refunds,
        processors,
        uow,
        accounting=None,
        outbox=None,
    ) -> None:
        self.intents = intents
        self.attempts = attempts
        self.refunds = refunds
        self.processors = processors
        self.uow = uow
        self.accounting = accounting
        self.outbox = outbox

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

        # Resolve the processor BEFORE anything is persisted: a processor that is not
        # registered or cannot refund must be refused cleanly, not leave a pending refund row.
        try:
            processor = self.processors.get_capable(attempt.processor, PaymentDirection.REFUND)
        except ProcessorRoutingError as exc:
            raise PaymentOperationConflict(
                f"refunds are not available for processor {attempt.processor!r}"
            ) from exc

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
            _enqueue_refund_event(
                self.outbox,
                owner_client_id=intent.client_id,
                intent=intent,
                refund=refund,
                status="unknown",
                processor=attempt.processor,
            )
        else:
            refund.provider_reference = result.provider_reference
            refund.provider_status = result.provider_status
            refund.transition_to(result.status)
        if refund.status == RefundStatus.SUCCEEDED:
            intent.apply_refund(refund.money.amount)
            self.intents.save(intent)
            if self.accounting is not None:
                self.accounting.record_refund(intent, attempt, refund)
            _enqueue_refund_event(
                self.outbox,
                owner_client_id=intent.client_id,
                intent=intent,
                refund=refund,
                status="succeeded",
                processor=attempt.processor,
            )
        elif refund.status == RefundStatus.FAILED:
            _enqueue_refund_event(
                self.outbox,
                owner_client_id=intent.client_id,
                intent=intent,
                refund=refund,
                status="failed",
                processor=attempt.processor,
            )
        self.refunds.save(refund)
        self.uow.commit()
        return refund
