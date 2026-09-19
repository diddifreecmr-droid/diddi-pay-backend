"""Read-only checks for missing effects of confirmed PaymentIntents."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class PaymentIntegrityGap:
    payment_intent_id: UUID
    missing_capture: bool
    missing_callback: bool


@dataclass(frozen=True)
class PaymentIntegrityReport:
    gaps: tuple[PaymentIntegrityGap, ...]
    has_more: bool

    @property
    def has_gaps(self) -> bool:
        return bool(self.gaps)


class PaymentIntegrityUseCases:
    def __init__(self, repository) -> None:
        self.repository = repository

    def audit(self, *, limit: int = 100) -> PaymentIntegrityReport:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.repository.find_gaps(limit + 1)
        return PaymentIntegrityReport(tuple(rows[:limit]), len(rows) > limit)
