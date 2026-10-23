"""Privacy-preserving payment context for DiddiSupport."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SupportAttemptContext:
    status: str
    provider_status: str | None
    failure_code: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SupportPaymentContext:
    payment_intent_id: uuid.UUID
    business_reference: str
    payment_status: str
    amount: int
    currency: str
    refunded_amount: int
    refund_status: str
    latest_attempt: SupportAttemptContext | None
    updated_at: datetime


class SupportPaymentContextQueries:
    def __init__(self, repository) -> None:
        self.repository = repository

    def get(self, payment_intent_id: uuid.UUID) -> SupportPaymentContext | None:
        return self.repository.get(payment_intent_id)
