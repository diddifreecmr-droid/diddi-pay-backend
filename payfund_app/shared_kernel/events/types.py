"""Noms d'événements du bus interne.

Entrants : publiés par DiddiFreeID (DiddiFreeID_Contrat_API.md §4).
Sortants : publiés par ce service (Architecture §1, `shared_kernel/events`).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# --- Entrants (DiddiFreeID) ---
USER_REGISTERED = "user.registered"
USER_UPDATED = "user.updated"
USER_ROLE_CHANGED = "user.role_changed"
USER_SUSPENDED = "user.suspended"

# --- Sortants (payfund) ---
PAYMENT_COMPLETED = "payment.completed"
LOAN_DISBURSED = "loan.disbursed"
CAMPAIGN_CLOSED = "campaign.closed"


@dataclass(frozen=True)
class Event:
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    telemetry_context: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        # Format du §4 : le nom de l'événement et `at` au premier niveau, à plat avec le payload.
        from payfund_app.core.observability.async_context import capture_context

        return {
            "telemetry_context": self.telemetry_context
            if self.telemetry_context is not None
            else capture_context(),
            "event": self.event,
            **self.payload,
            "at": self.at.isoformat(),
        }
