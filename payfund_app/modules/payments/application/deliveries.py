"""At-least-once delivery policy for payment outbox events."""

from dataclasses import dataclass

from payfund_app.modules.payments.application.errors import CallbackDeliveryFailed
from payfund_app.modules.payments.application.ports import (
    CallbackTarget,
    PaymentEventSenderPort,
    PaymentOutboxRepositoryPort,
    UnitOfWorkPort,
)


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    scanned: int
    delivered: int
    retried: int
    unavailable: int


class PaymentEventDeliveryUseCases:
    def __init__(
        self,
        outbox: PaymentOutboxRepositoryPort,
        uow: UnitOfWorkPort,
        sender: PaymentEventSenderPort,
        targets: dict[str, CallbackTarget],
    ) -> None:
        self.outbox = outbox
        self.uow = uow
        self.sender = sender
        self.targets = targets

    def run(self, *, limit: int = 100) -> DeliverySummary:
        rows = self.outbox.pending(limit)
        delivered = retried = unavailable = 0
        for row in rows:
            target = self.targets.get(row.client_id)
            if target is None:
                self.outbox.failed(row, "callback target not configured")
                unavailable += 1
                self.uow.commit()
                continue
            try:
                self.sender.send(target, event_id=row.id, payload=row.payload)
            except CallbackDeliveryFailed as exc:
                self.outbox.failed(row, str(exc))
                retried += 1
            else:
                self.outbox.delivered(row)
                delivered += 1
            self.uow.commit()
        return DeliverySummary(len(rows), delivered, retried, unavailable)
