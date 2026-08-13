"""Internal maintenance helpers for wallet provisioning and Paystack reconciliation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from payfund_app.core.security import CurrentUser
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.entities import TransactionStatus, TransactionType
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.modules.wallet.infra.gateways import GatewayStatus, PaystackGateway
from payfund_app.modules.wallet.infra.repositories import UserPhoneRepository


@dataclass(frozen=True)
class BackfillResult:
    user_id: uuid.UUID
    account_id: uuid.UUID
    phone: str | None


@dataclass(frozen=True)
class ReconcileResult:
    transaction_id: uuid.UUID
    status: str


def backfill_wallet(session: Session, *, user_id: uuid.UUID, phone: str | None = None) -> BackfillResult:
    account = WalletUseCases(session).provisionner_compte(user_id)
    if phone:
        UserPhoneRepository(session).upsert(user_id, phone)
    session.commit()
    return BackfillResult(user_id=user_id, account_id=account.id, phone=phone)


def reconcile_paystack_deposit(session: Session, *, transaction_id: uuid.UUID) -> ReconcileResult:
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
        return ReconcileResult(transaction_id=transaction.id, status="completed")
    if result.status is GatewayStatus.FAILED:
        use_cases.echouer_operation(transaction.id, provider="paystack")
        session.commit()
        return ReconcileResult(transaction_id=transaction.id, status="failed")

    session.commit()
    return ReconcileResult(transaction_id=transaction.id, status="pending")


@dataclass(frozen=True)
class BulkReconcileResult:
    scanned: int
    completed: int
    failed: int
    pending: int


def reconcile_pending_paystack_deposits(session: Session) -> BulkReconcileResult:
    """Sweep all pending Paystack deposits that still need a final provider verdict."""
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


def require_admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise PermissionError("Admin role required.")
