"""Durable and idempotent processing of provider refund callbacks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from payfund_app.modules.payments.application.ports import (
    ProviderEventRepositoryPort,
    RefundProviderEvent,
    RefundRepositoryPort,
    UnitOfWorkPort,
)
from payfund_app.modules.payments.domain import Money, Refund, RefundStatus
from payfund_app.modules.payments.domain.errors import InvalidStateTransition


@dataclass(frozen=True, slots=True)
class RefundWebhookOutcome:
    status: str
    event_key: str
    refund_id: str | None = None


class RefundWebhookUseCases:
    """Apply a signed refund callback exactly once and post its accounting effect."""

    def __init__(
        self,
        refunds: RefundRepositoryPort,
        intents,
        attempts,
        events: ProviderEventRepositoryPort,
        uow: UnitOfWorkPort,
        accounting=None,
    ) -> None:
        self.refunds = refunds
        self.intents = intents
        self.attempts = attempts
        self.events = events
        self.uow = uow
        self.accounting = accounting

    def process(
        self,
        processor,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        callback_url: str | None = None,
    ) -> RefundWebhookOutcome:
        event: RefundProviderEvent = processor.parse_refund_webhook(
            raw_body, headers, callback_url=callback_url
        )
        row = self.events.get(processor.name, event.event_key)
        if row is not None and row.status in {"processed", "ignored"}:
            return RefundWebhookOutcome("duplicate", event.event_key, _row_refund_id(row))

        if row is None:
            try:
                row = self.events.add(
                    processor=processor.name,
                    event_key=event.event_key,
                    event_type=event.event_type,
                    payload_hash=hashlib.sha256(raw_body).hexdigest(),
                    payload=dict(event.sanitized_payload),
                )
                self.uow.commit()
            except IntegrityError:
                self.uow.rollback()
                row = self.events.get(processor.name, event.event_key)
                if row is None:
                    raise
                if row.status in {"processed", "ignored"}:
                    return RefundWebhookOutcome(
                        "duplicate", event.event_key, _row_refund_id(row)
                    )

        if not event.provider_reference:
            self.events.mark(row, status="ignored", error_message="missing provider reference")
            self.uow.commit()
            return RefundWebhookOutcome("ignored", event.event_key)

        refund_row = self.refunds.get_by_provider_reference(
            processor.name, event.provider_reference, for_update=True
        )
        if refund_row is None:
            self.events.mark(row, status="ignored", error_message="unknown provider reference")
            self.uow.commit()
            return RefundWebhookOutcome("ignored", event.event_key)

        refund_id = str(refund_row.id)
        if event.amount is not None and event.amount != refund_row.amount:
            self.events.mark(
                row,
                status="failed",
                refund_id=refund_row.id,
                error_message="provider amount mismatch",
            )
            self.uow.commit()
            return RefundWebhookOutcome("failed", event.event_key, refund_id)
        if event.currency is not None and event.currency != refund_row.currency:
            self.events.mark(
                row,
                status="failed",
                refund_id=refund_row.id,
                error_message="provider currency mismatch",
            )
            self.uow.commit()
            return RefundWebhookOutcome("failed", event.event_key, refund_id)

        refund = _to_domain(refund_row)
        if event.status is None:
            self.events.mark(
                row,
                status="failed",
                refund_id=refund_row.id,
                error_message="missing refund status",
            )
            self.uow.commit()
            return RefundWebhookOutcome("failed", event.event_key, refund_id)

        try:
            if event.status == RefundStatus.PROCESSING:
                refund.transition_to(RefundStatus.PROCESSING)
            elif event.status == RefundStatus.FAILED:
                if refund.status == RefundStatus.SUCCEEDED:
                    raise InvalidStateTransition(
                        "refund", refund.status, RefundStatus.FAILED
                    )
                refund.transition_to(RefundStatus.FAILED)
            elif event.status == RefundStatus.SUCCEEDED:
                if refund.status != RefundStatus.SUCCEEDED:
                    intent = self.intents.get(refund.payment_intent_id, for_update=True)
                    attempt = self.attempts.get(refund.payment_attempt_id, for_update=True)
                    if intent is None or attempt is None:
                        raise LookupError("refund payment records are missing")
                    refund.transition_to(RefundStatus.SUCCEEDED)
                    intent.apply_refund(refund.money.amount)
                    self.intents.save(intent)
                    if self.accounting is not None:
                        self.accounting.record_refund(intent, attempt, refund)
            else:
                raise InvalidStateTransition("refund", refund.status, event.status)
        except (InvalidStateTransition, LookupError, ValueError) as exc:
            self.events.mark(
                row,
                status="failed",
                refund_id=refund_row.id,
                error_message=str(exc)[:255],
            )
            self.uow.commit()
            return RefundWebhookOutcome("failed", event.event_key, refund_id)

        refund.provider_status = event.provider_status
        self.refunds.save(refund)
        self.events.mark(row, status="processed", refund_id=refund_row.id)
        self.uow.commit()
        return RefundWebhookOutcome("processed", event.event_key, refund_id)


def _to_domain(row) -> Refund:
    return Refund(
        id=row.id,
        payment_intent_id=row.payment_intent_id,
        payment_attempt_id=row.payment_attempt_id,
        money=Money(row.amount, row.currency),
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        reason=row.reason,
        status=RefundStatus(row.status),
        provider_reference=row.provider_reference,
        provider_status=row.provider_status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_refund_id(row) -> str | None:
    return str(row.refund_id) if getattr(row, "refund_id", None) else None
