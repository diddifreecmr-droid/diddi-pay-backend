from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.application.accounting import PayoutAccountingService
from payfund_app.modules.payments.application.payouts import (
    CreatePayoutCommand,
    PayoutUseCases,
)
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.infra.models import PayoutRecord
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentOutboxRepository,
    PayoutRepository,
)
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork


def test_concurrent_same_key_creates_one_payout(engine):
    barrier = Barrier(2)
    processors = ProcessorRegistry()
    processors.register(SandboxPaymentProcessor())
    command = CreatePayoutCommand(
        client_id="diddifood",
        business_reference="diddifood:order:concurrent:restaurant-payout:v1",
        beneficiary_reference="restaurant:rest-7",
        amount=7_500,
        currency="XOF",
        idempotency_key="diddisend:delivery:concurrent:picked_up:v1",
        metadata={"delivery_id": "concurrent", "breakdown_hash": "sha256:same"},
    )

    class SynchronizedPayoutRepository(PayoutRepository):
        def get_by_idempotency(self, client_id, key):
            existing = super().get_by_idempotency(client_id, key)
            if existing is None:
                barrier.wait(timeout=5)
            return existing

    def create_once():
        with Session(engine) as session:
            view = PayoutUseCases(
                SynchronizedPayoutRepository(session),
                PaymentOutboxRepository(session),
                processors,
                SqlAlchemyUnitOfWork(session),
                PayoutAccountingService(FinancialLedgerRepository(session)),
            ).create(command)
            return view.payout.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        identifiers = list(executor.map(lambda _: create_once(), range(2)))

    with Session(engine) as session:
        count = session.scalar(
            select(func.count()).select_from(PayoutRecord).where(
                PayoutRecord.client_id == "diddifood",
                PayoutRecord.idempotency_key == command.idempotency_key,
            )
        )
    assert identifiers[0] == identifiers[1]
    assert count == 1
