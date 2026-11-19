"""Durable, verified and idempotent processing of payout provider callbacks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from payfund_app.modules.payments.application.payouts import PayoutUseCases
from payfund_app.modules.payments.application.ports import (
    PayoutProviderEvent,
    PayoutRepositoryPort,
    PayoutResult,
    ProviderEventRepositoryPort,
    UnitOfWorkPort,
)
from payfund_app.modules.payments.domain.errors import InvalidStateTransition


@dataclass(frozen=True, slots=True)
class PayoutWebhookOutcome:
    status: str
    event_key: str
    payout_id: str | None = None


class PayoutWebhookUseCases:
    """Apply a provider callback only after the provider confirms its own status."""

    def __init__(
        self,
        payouts: PayoutRepositoryPort,
        events: ProviderEventRepositoryPort,
        uow: UnitOfWorkPort,
        payout_use_cases: PayoutUseCases,
    ) -> None:
        self.payouts = payouts
        self.events = events
        self.uow = uow
        self.payout_use_cases = payout_use_cases

    def process(
        self,
        processor,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        callback_url: str | None = None,
    ) -> PayoutWebhookOutcome:
        event: PayoutProviderEvent = processor.parse_payout_webhook(
            raw_body, headers, callback_url=callback_url
        )
        row = self.events.get(processor.name, event.event_key)
        if row is not None and row.status in {"processed", "ignored"}:
            return PayoutWebhookOutcome("duplicate", event.event_key, _row_payout_id(row))
        if row is None:
            try:
                row = self.events.add(
                    processor=processor.name,
                    event_key=event.event_key,
                    event_type=event.event_type,
                    payload_hash=hashlib.sha256(raw_body).hexdigest(),
                    payload=dict(event.sanitized_payload),
                )
                # The callback may be retried after a timeout. The inbox is the durable barrier.
                self.uow.commit()
            except IntegrityError:
                # A concurrent callback won the unique inbox key. Roll back the failed flush,
                # then continue from the durable row instead of returning a provider retryable
                # 500.
                self.uow.rollback()
                row = self.events.get(processor.name, event.event_key)
                if row is None:
                    raise
                if row.status in {"processed", "ignored"}:
                    return PayoutWebhookOutcome(
                        "duplicate", event.event_key, _row_payout_id(row)
                    )

        if not event.provider_reference:
            self.events.mark(row, status="ignored", error_message="missing provider reference")
            self.uow.commit()
            return PayoutWebhookOutcome("ignored", event.event_key)

        payout = self.payouts.get_by_provider_reference(
            processor.name, event.provider_reference, for_update=True
        )
        if payout is None:
            self.events.mark(row, status="ignored", error_message="unknown provider reference")
            self.uow.commit()
            return PayoutWebhookOutcome("ignored", event.event_key)

        if event.amount is not None and event.amount != payout.money.amount:
            self.events.mark(
                row,
                status="failed",
                payout_id=payout.id,
                error_message="provider amount mismatch",
            )
            self.uow.commit()
            return PayoutWebhookOutcome("failed", event.event_key, str(payout.id))
        if event.currency is not None and event.currency != payout.money.currency:
            self.events.mark(
                row,
                status="failed",
                payout_id=payout.id,
                error_message="provider currency mismatch",
            )
            self.uow.commit()
            return PayoutWebhookOutcome("failed", event.event_key, str(payout.id))

        # A signed callback proves origin, not the business result. Confirm the final
        # provider state with the authenticated API before posting the payout ledger.
        # Transport uncertainty propagates to the route as 503, leaving the inbox row received
        # so a PawaPay retry or the worker can finish it.
        result: PayoutResult = processor.verify_payout(event.provider_reference)

        previous = payout.status
        try:
            self.payout_use_cases.apply_provider_result(payout, result)
        except InvalidStateTransition as exc:
            self.events.mark(
                row,
                status="failed",
                payout_id=payout.id,
                error_message=str(exc)[:255],
            )
            self.uow.commit()
            return PayoutWebhookOutcome("failed", event.event_key, str(payout.id))

        self.events.mark(row, status="processed", payout_id=payout.id)
        self.uow.commit()
        status = "processed" if payout.status != previous else "duplicate"
        return PayoutWebhookOutcome(status, event.event_key, str(payout.id))


def _row_payout_id(row) -> str | None:
    payout_id = getattr(row, "payout_id", None)
    return str(payout_id) if payout_id else None
