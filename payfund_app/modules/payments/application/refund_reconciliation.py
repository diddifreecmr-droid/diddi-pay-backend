"""Finish refunds whose provider callback never arrives (ticket 023).

A refund accepted by the provider stays `processing` until a callback says how it ended.
Paystack refund callbacks are not consumed, so Paystack refunds never finished. The worker
now reads such refunds back from any processor that declares `refund_verification` and
applies a final answer through the same inbox path as a callback (amount check, state
transition, accounting, module event, exactly once).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from payfund_app.modules.payments.application.errors import ProcessorCallUncertain
from payfund_app.modules.payments.application.ports import RefundProviderEvent
from payfund_app.modules.payments.application.processor_router import ProcessorRoutingError
from payfund_app.modules.payments.application.refund_webhooks import RefundWebhookUseCases
from payfund_app.modules.payments.domain import RefundStatus


@dataclass(frozen=True, slots=True)
class RefundReconciliationSummary:
    scanned: int = 0
    succeeded: int = 0
    failed: int = 0
    pending: int = 0
    unverifiable: int = 0


class RefundReconciliationUseCases:
    def __init__(self, refunds, processors, webhook_use_cases: RefundWebhookUseCases, uow) -> None:
        self.refunds = refunds
        self.processors = processors
        self.webhooks = webhook_use_cases
        self.uow = uow

    def reconcile(
        self, *, minimum_age_seconds: int = 300, limit: int = 100
    ) -> RefundReconciliationSummary:
        older_than = datetime.now(UTC) - timedelta(seconds=minimum_age_seconds)
        # Only processors that can read a refund back: the others (PawaPay) finish refunds
        # by callback, and their pending rows must not fill every batch.
        verifiable = [
            processor.name
            for processor in self.processors.ordered()
            if processor.capabilities.refund_verification
        ]
        rows = self.refunds.pending_for_reconciliation(
            older_than=older_than, processors=verifiable, limit=limit
        )
        # Copy what we need: process_event commits, which expires the loaded rows.
        work = [(row.processor, row.provider_reference) for row in rows]
        counts = {"succeeded": 0, "failed": 0, "pending": 0, "unverifiable": 0}
        for processor_name, provider_reference in work:
            outcome = self._reconcile_one(processor_name, provider_reference)
            counts[outcome] += 1
        self.uow.commit()
        return RefundReconciliationSummary(scanned=len(work), **counts)

    def _reconcile_one(self, processor_name: str, provider_reference: str) -> str:
        try:
            processor = self.processors.get(processor_name)
        except ProcessorRoutingError:
            return "unverifiable"
        if not processor.capabilities.refund_verification:
            return "unverifiable"
        try:
            result = processor.verify_refund(provider_reference)
        except ProcessorCallUncertain:
            return "pending"
        if result.status not in {RefundStatus.SUCCEEDED, RefundStatus.FAILED}:
            return "pending"

        provider_status = result.provider_status or str(result.status)
        payload = {
            "source": "reconciliation",
            "provider_reference": provider_reference,
            "status": provider_status,
            "amount": result.amount,
            "currency": result.currency,
        }
        raw = json.dumps(payload, sort_keys=True).encode()
        event = RefundProviderEvent(
            # Stable per final answer: a second cycle (or a late callback with the same key)
            # is a duplicate in the inbox, never a second accounting entry.
            event_key=f"refund-reconcile:{provider_reference}:{provider_status}",
            event_type="refund.reconciled",
            provider_reference=provider_reference,
            status=result.status,
            provider_status=provider_status,
            amount=result.amount,
            currency=result.currency,
            failure_code=result.failure_code,
            failure_message=result.failure_message,
            sanitized_payload={**payload, "payload_sha256": hashlib.sha256(raw).hexdigest()},
        )
        outcome = self.webhooks.process_event(processor_name, raw, event)
        if outcome.status in {"processed", "duplicate"}:
            return "succeeded" if result.status == RefundStatus.SUCCEEDED else "failed"
        return "pending"
