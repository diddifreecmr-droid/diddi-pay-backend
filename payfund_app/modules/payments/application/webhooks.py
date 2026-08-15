"""Durable and idempotent processing of normalized provider webhooks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

from payfund_app.modules.payments.application.ports import (
    PaymentAttemptRepositoryPort,
    PaymentIntentRepositoryPort,
    PaymentProcessorPort,
    ProviderEventRepositoryPort,
    UnitOfWorkPort,
)
from payfund_app.modules.payments.domain import AttemptStatus, PaymentIntentStatus
from payfund_app.modules.payments.domain.errors import InvalidStateTransition


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    status: str
    event_key: str
    payment_intent_id: str | None = None


class PaymentWebhookUseCases:
    def __init__(
        self,
        intents: PaymentIntentRepositoryPort,
        attempts: PaymentAttemptRepositoryPort,
        events: ProviderEventRepositoryPort,
        uow: UnitOfWorkPort,
        outbox=None,
        accounting=None,
    ) -> None:
        self.intents = intents
        self.attempts = attempts
        self.events = events
        self.uow = uow
        self.outbox = outbox
        self.accounting = accounting

    def process(
        self,
        processor: PaymentProcessorPort,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> WebhookOutcome:
        event = processor.parse_webhook(raw_body, headers)
        row = self.events.get(processor.name, event.event_key)
        if row is not None and row.status in {"processed", "ignored"}:
            return WebhookOutcome("duplicate", event.event_key)
        if row is None:
            row = self.events.add(
                processor=processor.name,
                event_key=event.event_key,
                event_type=event.event_type,
                payload_hash=hashlib.sha256(raw_body).hexdigest(),
                payload=dict(event.sanitized_payload),
            )
            # Persist the inbox record before changing financial state.
            self.uow.commit()

        if not event.provider_reference:
            self.events.mark(row, status="ignored", error_message="missing provider reference")
            self.uow.commit()
            return WebhookOutcome("ignored", event.event_key)

        attempt = self.attempts.get_by_provider_reference(
            processor.name, event.provider_reference, for_update=True
        )
        if attempt is None:
            self.events.mark(row, status="ignored", error_message="unknown provider reference")
            self.uow.commit()
            return WebhookOutcome("ignored", event.event_key)
        intent = self.intents.get(attempt.payment_intent_id, for_update=True)
        if intent is None:
            self.events.mark(
                row,
                status="failed",
                payment_attempt_id=attempt.id,
                error_message="payment intent missing",
            )
            self.uow.commit()
            return WebhookOutcome("failed", event.event_key)

        if event.status == AttemptStatus.SUCCEEDED and (
            event.amount != attempt.money.amount or event.currency != attempt.money.currency
        ):
            self.events.mark(
                row,
                status="failed",
                payment_attempt_id=attempt.id,
                error_message="provider amount or currency mismatch",
            )
            self.uow.commit()
            return WebhookOutcome("failed", event.event_key, str(intent.id))

        was_succeeded = intent.status == PaymentIntentStatus.SUCCEEDED
        try:
            self._apply_status(intent, attempt, event.status)
        except InvalidStateTransition as exc:
            self.events.mark(
                row,
                status="failed",
                payment_attempt_id=attempt.id,
                error_message=str(exc)[:255],
            )
            self.uow.commit()
            return WebhookOutcome("failed", event.event_key, str(intent.id))

        attempt.provider_status = event.sanitized_payload.get("status")
        self.attempts.save(attempt)
        self.intents.save(intent)
        self.events.mark(row, status="processed", payment_attempt_id=attempt.id)
        if (
            self.outbox is not None
            and event.status == AttemptStatus.SUCCEEDED
            and not was_succeeded
        ):
            self.outbox.enqueue(
                client_id=intent.client_id,
                event_type="payment.succeeded",
                aggregate_id=intent.id,
                payload={
                    "event_id": event.event_key,
                    "payment_intent_id": str(intent.id),
                    "business_reference": intent.business_reference,
                    "amount": intent.money.amount,
                    "currency": intent.money.currency,
                    "status": str(intent.status),
                },
            )
        if (
            self.accounting is not None
            and event.status == AttemptStatus.SUCCEEDED
            and not was_succeeded
        ):
            self.accounting.record_capture(
                intent,
                attempt,
                event_reference=event.event_key,
                fee=event.fee or 0,
            )
        self.uow.commit()
        return WebhookOutcome("processed", event.event_key, str(intent.id))

    def _apply_status(self, intent, attempt, status: AttemptStatus | None) -> None:
        if status is None or status == attempt.status:
            return
        if status == AttemptStatus.SUCCEEDED:
            attempt.transition_to(AttemptStatus.SUCCEEDED)
            if intent.status == PaymentIntentStatus.FAILED:
                intent.transition_to(PaymentIntentStatus.PROCESSING)
            if intent.status != PaymentIntentStatus.SUCCEEDED:
                intent.transition_to(PaymentIntentStatus.SUCCEEDED)
            return
        if status == AttemptStatus.FAILED:
            if attempt.status not in {
                AttemptStatus.SUCCEEDED,
                AttemptStatus.FAILED,
                AttemptStatus.CANCELLED,
            }:
                attempt.transition_to(AttemptStatus.FAILED)
                latest = self.attempts.list_for_intent(intent.id)[-1]
                if latest.id == attempt.id and intent.status in {
                    PaymentIntentStatus.PROCESSING,
                    PaymentIntentStatus.REQUIRES_ACTION,
                }:
                    intent.transition_to(PaymentIntentStatus.FAILED)
            return
        if status == AttemptStatus.PROCESSING and attempt.status in {
            AttemptStatus.PENDING,
            AttemptStatus.REQUIRES_ACTION,
            AttemptStatus.UNKNOWN,
        }:
            attempt.transition_to(AttemptStatus.PROCESSING)
            if intent.status == PaymentIntentStatus.REQUIRES_ACTION:
                intent.transition_to(PaymentIntentStatus.PROCESSING)
