"""Read-only operational views owned by DiddiPay."""

from dataclasses import dataclass
from datetime import datetime
import uuid


@dataclass(frozen=True, slots=True)
class BackofficeAttempt:
    id: uuid.UUID
    processor: str
    status: str
    provider_reference: str | None
    provider_status: str | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BackofficePayment:
    id: uuid.UUID
    client_id: str
    business_reference: str
    amount: int
    currency: str
    status: str
    refunded_amount: int
    created_at: datetime
    updated_at: datetime
    attempts: tuple[BackofficeAttempt, ...] = ()
    financial_summary: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class BackofficePaymentPage:
    items: tuple[BackofficePayment, ...]
    page: int
    page_size: int
    total: int


class BackofficePaymentQueries:
    def __init__(self, repository) -> None:
        self.repository = repository

    def list(self, *, page: int, page_size: int, status: str | None) -> BackofficePaymentPage:
        items, total = self.repository.list(page=page, page_size=page_size, status=status)
        return BackofficePaymentPage(tuple(items), page, page_size, total)

    def get(self, payment_intent_id: uuid.UUID) -> BackofficePayment | None:
        return self.repository.get(payment_intent_id)
