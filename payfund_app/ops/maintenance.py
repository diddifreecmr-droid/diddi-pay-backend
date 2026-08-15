"""Internal maintenance helpers for wallet provisioning and Paystack reconciliation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from payfund_app.core.security import CurrentUser
from payfund_app.core.config import get_settings
from payfund_app.modules.payments.application.deliveries import (
    DeliverySummary,
    PaymentEventDeliveryUseCases,
)
from payfund_app.modules.payments.application.ports import (
    CallbackTarget,
    PaymentEventSenderPort,
)
from payfund_app.modules.payments.infra.callback_delivery import HttpSignedCallbackSender
from payfund_app.modules.payments.infra.repositories import PaymentOutboxRepository
from payfund_app.modules.payments.application.accounting import PaymentAccountingService
from payfund_app.modules.payments.infra.repositories import (
    FinancialLedgerRepository,
    PaymentAttemptRepository,
    PaymentIntentRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.entities import TransactionStatus, TransactionType
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.modules.wallet.infra.gateways import GatewayStatus, PaystackGateway
from payfund_app.modules.wallet.infra.repositories import OutboxRepository, UserPhoneRepository
from payfund_app.shared_kernel.logging import emit
from payfund_app.shared_kernel.events.types import Event


@dataclass(frozen=True)
class BackfillResult:
    user_id: uuid.UUID
    account_id: uuid.UUID
    phone: str | None
    account_type: str


@dataclass(frozen=True)
class ReconcileResult:
    transaction_id: uuid.UUID
    status: str


def backfill_wallet(
    session: Session,
    *,
    user_id: uuid.UUID,
    phone: str | None = None,
    account_type: str = "user",
) -> BackfillResult:
    emit(
        "info",
        "ops.backfill.start",
        user_id=str(user_id),
        account_type=account_type,
        phone=phone,
    )
    use_cases = WalletUseCases(session)
    if account_type == "merchant":
        account = use_cases.provisionner_compte_marchand(user_id)
    else:
        account = use_cases.provisionner_compte(user_id)
    if phone:
        UserPhoneRepository(session).upsert(user_id, phone)
    session.commit()
    emit(
        "info",
        "ops.backfill.done",
        user_id=str(user_id),
        account_id=str(account.id),
        account_type=account_type,
        phone=phone,
    )
    return BackfillResult(
        user_id=user_id,
        account_id=account.id,
        phone=phone,
        account_type=account_type,
    )


def reconcile_paystack_deposit(session: Session, *, transaction_id: uuid.UUID) -> ReconcileResult:
    emit("info", "ops.reconcile.start", transaction_id=str(transaction_id))
    use_cases = WalletUseCases(session)
    transaction = use_cases.transactions.get(transaction_id)
    if transaction is None:
        raise ValueError(f"Transaction introuvable: {transaction_id}")
    if transaction.provider_reference is None:
        raise ValueError("Transaction sans provider_reference.")

    result = PaystackGateway().verifier_depot(transaction.provider_reference)
    if result.status is GatewayStatus.COMPLETED:
        use_cases.confirmer_operation(transaction.id, provider="paystack")
        session.commit()
        emit(
            "info",
            "ops.reconcile.done",
            transaction_id=str(transaction.id),
            status="completed",
        )
        return ReconcileResult(transaction_id=transaction.id, status="completed")
    if result.status is GatewayStatus.FAILED:
        use_cases.echouer_operation(transaction.id, provider="paystack")
        session.commit()
        emit(
            "warning",
            "ops.reconcile.done",
            transaction_id=str(transaction.id),
            status="failed",
        )
        return ReconcileResult(transaction_id=transaction.id, status="failed")

    session.commit()
    emit(
        "info",
        "ops.reconcile.done",
        transaction_id=str(transaction.id),
        status="pending",
    )
    return ReconcileResult(transaction_id=transaction.id, status="pending")


@dataclass(frozen=True)
class BulkReconcileResult:
    scanned: int
    completed: int
    failed: int
    pending: int


@dataclass(frozen=True)
class RelayResult:
    scanned: int
    published: int


@dataclass(frozen=True)
class HousekeepingResult:
    reconciliation: BulkReconcileResult
    outbox: RelayResult


def reconcile_pending_paystack_deposits(session: Session) -> BulkReconcileResult:
    """Sweep all pending Paystack deposits that still need a final provider verdict."""
    emit("info", "ops.reconcile.sweep.start")
    pending_deposits = list(
        session.scalars(
            select(Transaction).where(
                Transaction.type == str(TransactionType.DEPOSIT),
                Transaction.status == str(TransactionStatus.PENDING),
                Transaction.provider_reference.is_not(None),
                Transaction.origin_module == "wallet",
            )
        )
    )
    completed = failed = pending = 0
    for transaction in pending_deposits:
        result = reconcile_paystack_deposit(session, transaction_id=transaction.id)
        if result.status == "completed":
            completed += 1
        elif result.status == "failed":
            failed += 1
        else:
            pending += 1
    return BulkReconcileResult(
        scanned=len(pending_deposits),
        completed=completed,
        failed=failed,
        pending=pending,
    )


def relay_outbox_events(session: Session, bus) -> RelayResult:
    """Publie les événements durables non encore relayés vers le bus temps réel."""
    emit("info", "ops.outbox.relay.start")
    repo = OutboxRepository(session)
    try:
        pending_events = repo.pending()
    except ProgrammingError:
        session.rollback()
        return RelayResult(scanned=0, published=0)
    published = 0
    for row in pending_events:
        bus.publish(Event(row.event_name, row.payload))
        repo.mark_published(row)
        published += 1
    session.commit()
    emit(
        "info",
        "ops.outbox.relay.done",
        scanned=len(pending_events),
        published=published,
    )
    return RelayResult(scanned=len(pending_events), published=published)


def deliver_payment_events(
    session: Session,
    *,
    limit: int = 100,
    sender: PaymentEventSenderPort | None = None,
    targets: dict[str, CallbackTarget] | None = None,
) -> DeliverySummary:
    """Deliver provider-neutral payment events to their owning modules."""
    if targets is None:
        targets = {
            client_id: CallbackTarget(str(config.url), config.secret)
            for client_id, config in get_settings().payment_callback_targets.items()
        }
    emit(
        "info",
        "ops.payment_events.delivery.start",
        limit=limit,
        configured_clients=sorted(targets),
    )
    result = PaymentEventDeliveryUseCases(
        PaymentOutboxRepository(session),
        SqlAlchemyUnitOfWork(session),
        sender or HttpSignedCallbackSender(),
        targets,
    ).run(limit=limit)
    emit(
        "info",
        "ops.payment_events.delivery.done",
        scanned=result.scanned,
        delivered=result.delivered,
        retried=result.retried,
        unavailable=result.unavailable,
    )
    return result


def record_payment_settlement(
    session: Session,
    *,
    payment_intent_id: uuid.UUID,
    amount: int,
    settlement_reference: str,
) -> dict[str, int]:
    intents = PaymentIntentRepository(session)
    intent = intents.get(payment_intent_id, for_update=True)
    if intent is None:
        raise ValueError("PAYMENT_INTENT_NOT_FOUND")
    attempts = PaymentAttemptRepository(session).list_for_intent(payment_intent_id)
    successful = next((row for row in reversed(attempts) if str(row.status) == "succeeded"), None)
    if successful is None:
        raise ValueError("SUCCESSFUL_PAYMENT_ATTEMPT_NOT_FOUND")
    ledger = FinancialLedgerRepository(session)
    PaymentAccountingService(ledger).record_settlement(
        intent,
        processor=successful.processor,
        amount=amount,
        settlement_reference=settlement_reference,
    )
    session.commit()
    summary = ledger.summary(payment_intent_id)
    emit(
        "info",
        "ops.payment_settlement.recorded",
        payment_intent_id=str(payment_intent_id),
        amount=amount,
        settlement_reference=settlement_reference,
        outstanding=summary["outstanding"],
    )
    return summary


def run_housekeeping(session: Session, bus) -> HousekeepingResult:
    """Exécute le cycle standard d'entretien: réconciliation Paystack puis relay outbox.

    Ce point d'entrée est pensé pour un cron interne, un job planifié ou un hook de maintenance.
    """
    emit("info", "ops.housekeeping.start")
    reconciliation = reconcile_pending_paystack_deposits(session)
    outbox = relay_outbox_events(session, bus)
    emit(
        "info",
        "ops.housekeeping.done",
        reconciliation_scanned=reconciliation.scanned,
        reconciliation_completed=reconciliation.completed,
        reconciliation_failed=reconciliation.failed,
        reconciliation_pending=reconciliation.pending,
        outbox_scanned=outbox.scanned,
        outbox_published=outbox.published,
    )
    return HousekeepingResult(reconciliation=reconciliation, outbox=outbox)


def require_admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise PermissionError("Admin role required.")
