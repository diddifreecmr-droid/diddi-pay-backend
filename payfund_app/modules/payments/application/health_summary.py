"""Small, non-sensitive operational health summary for Pilotage."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class PaymentHealthSummary:
    status: Literal["healthy", "degraded", "unavailable"]
    pending_callbacks_count: int | None
    dead_letter_callbacks_count: int | None
    calculated_at: datetime


class PaymentHealthSummaryUseCases:
    def __init__(self, repository) -> None:
        self.repository = repository

    def get(self) -> PaymentHealthSummary:
        try:
            counts = self.repository.callback_status_counts()
        except Exception:  # noqa: BLE001 - adapter failures are mapped to contract freshness
            # The contract must distinguish an unavailable source from a real zero.
            return PaymentHealthSummary("unavailable", None, None, datetime.now(UTC))
        dead_letters = counts.get("dead_letter", 0)
        return PaymentHealthSummary(
            status="degraded" if dead_letters else "healthy",
            pending_callbacks_count=counts.get("pending", 0),
            dead_letter_callbacks_count=dead_letters,
            calculated_at=datetime.now(UTC),
        )
