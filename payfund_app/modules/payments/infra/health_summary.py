"""Persistence adapter for the Pilotage operational health summary."""

from sqlalchemy.orm import Session

from payfund_app.modules.payments.infra.repositories import PaymentOutboxRepository


class SqlPaymentHealthSummaryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def callback_status_counts(self) -> dict[str, int]:
        return PaymentOutboxRepository(self.session).status_counts()
