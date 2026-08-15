"""Provider verification fallback for payment attempts missing a final webhook."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from payfund_app.modules.payments.application.errors import ProcessorCallUncertain
from payfund_app.modules.payments.application.webhooks import PaymentWebhookUseCases
from payfund_app.modules.payments.domain import AttemptStatus, PaymentIntentStatus


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    scanned: int
    succeeded: int
    failed: int
    pending: int
    mismatched: int


class PaymentReconciliationUseCases:
    def __init__(self, intents, attempts, events, processors, uow) -> None:
        self.intents = intents
        self.attempts = attempts
        self.events = events
        self.processors = processors
        self.uow = uow

    def run(self, *, minimum_age_seconds: int = 300, limit: int = 100) -> ReconciliationSummary:
        cutoff = datetime.now(UTC) - timedelta(seconds=minimum_age_seconds)
        candidates = self.attempts.pending_for_reconciliation(
            older_than=cutoff, limit=limit
        )
        succeeded = failed = pending = mismatched = 0
        for attempt in candidates:
            processor = self.processors.get(attempt.processor)
            try:
                result = processor.verify_payment(attempt.provider_reference)
            except ProcessorCallUncertain as exc:
                self._log(attempt, "failed", {"reason": str(exc), "outcome": "unknown"})
                pending += 1
                self.uow.commit()
                continue
            intent = self.intents.get(attempt.payment_intent_id, for_update=True)
            if intent is None:
                self._log(attempt, "failed", {"reason": "intent_missing"})
                failed += 1
                self.uow.commit()
                continue
            if result.status == AttemptStatus.SUCCEEDED and (
                result.amount != attempt.money.amount or result.currency != attempt.money.currency
            ):
                self._log(attempt, "failed", {"reason": "amount_or_currency_mismatch"})
                mismatched += 1
                self.uow.commit()
                continue
            updater = PaymentWebhookUseCases(
                self.intents, self.attempts, self.events, self.uow
            )
            updater._apply_status(intent, attempt, result.status)
            attempt.provider_status = result.provider_status
            self.attempts.save(attempt)
            self.intents.save(intent)
            self._log(
                attempt,
                "processed",
                {"provider_status": result.provider_status, "outcome": str(result.status)},
            )
            if result.status == AttemptStatus.SUCCEEDED:
                succeeded += 1
            elif result.status == AttemptStatus.FAILED:
                failed += 1
            else:
                pending += 1
            self.uow.commit()
        return ReconciliationSummary(len(candidates), succeeded, failed, pending, mismatched)

    def _log(self, attempt, status: str, payload: dict) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode()
        row = self.events.add(
            processor=attempt.processor,
            event_key=f"reconciliation:{attempt.id}:{datetime.now(UTC).isoformat()}",
            event_type="reconciliation.verify",
            payload_hash=hashlib.sha256(encoded).hexdigest(),
            payload=payload,
        )
        self.events.mark(
            row,
            status=status,
            payment_attempt_id=attempt.id,
            error_message=payload.get("reason"),
        )
