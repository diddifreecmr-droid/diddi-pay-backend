"""Routes du module `wallet` (Contrat API §1)."""

from __future__ import annotations

import math
import hmac
import hashlib
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select

from payfund_app.core.errors import Forbidden
from payfund_app.modules.wallet.application.qr_service import QrService
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.money import Money
from payfund_app.modules.wallet.presentation.deps import (
    CurrentUserDep,
    IdempotencyKeyDep,
    SessionDep,
)
from payfund_app.modules.wallet.presentation.schemas import (
    DepositResponse,
    BalanceResponse,
    DepositRequest,
    GenerateQrRequest,
    GenerateQrResponse,
    MerchantPaymentRequest,
    OpsBackfillRequest,
    Page,
    Pagination,
    PendingOperationResponse,
    TransactionDetail,
    TransactionItem,
    TransferRequest,
    TransferResponse,
    VerifyQrRequest,
    VerifyQrResponse,
    WithdrawRequest,
)
from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.infra.gateways import GatewayStatus, PaystackGateway
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.modules.wallet.infra.repositories import OutboxRepository, ReconciliationLogRepository
from payfund_app.shared_kernel.events.bus import get_bus

router = APIRouter(prefix="/wallet", tags=["wallet"])


def _montant(transaction, entry) -> int:
    """Montant vu par l'appelant : celui de son écriture, ou celui annoncé si l'opération
    n'a pas encore produit d'écriture (dépôt en attente de l'opérateur)."""
    if entry is not None:
        return Money.from_db(entry.amount, entry.currency).amount
    if transaction.amount is None:
        return 0
    return Money.from_db(transaction.amount, transaction.currency or "XOF").amount


@router.get("/balance", response_model=BalanceResponse)
def get_balance(user: CurrentUserDep, session: SessionDep) -> BalanceResponse:
    account, balance = WalletUseCases(session).consulter_solde(user.user_id)
    return BalanceResponse(
        account_id=account.id,
        balance=balance.amount,
        currency=balance.currency,
        status=account.status,
    )


@router.post("/deposit", response_model=DepositResponse, status_code=202)
def deposit(
    payload: DepositRequest,
    user: CurrentUserDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
) -> DepositResponse:
    """Traitement asynchrone côté opérateur : le front interroge ensuite
    `GET /wallet/transactions/{id}` pour connaître l'issue."""
    result = WalletUseCases(session, bus=get_bus()).deposer(
        user_id=user.user_id,
        provider=payload.provider,
        amount=payload.amount,
        phone=payload.phone,
        email=payload.email,
        idempotency_key=idempotency_key,
    )
    return DepositResponse(
        transaction_id=result.transaction.id,
        status=result.transaction.status,
        provider_reference=result.transaction.provider_reference,
        authorization_url=result.authorization_url,
        access_code=result.access_code,
    )


@router.post("/withdraw", response_model=PendingOperationResponse, status_code=202)
def withdraw(
    payload: WithdrawRequest,
    user: CurrentUserDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
) -> PendingOperationResponse:
    transaction = WalletUseCases(session, bus=get_bus()).retirer(
        user_id=user.user_id,
        provider=payload.provider,
        amount=payload.amount,
        phone=payload.phone,
        idempotency_key=idempotency_key,
    )
    return PendingOperationResponse(
        transaction_id=transaction.id, status=transaction.status
    )


@router.post("/transfer", response_model=TransferResponse, status_code=201)
def transfer(
    payload: TransferRequest,
    user: CurrentUserDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
) -> TransferResponse:
    result = WalletUseCases(session, bus=get_bus()).transferer_p2p(
        user_id=user.user_id,
        recipient_phone=payload.recipient_phone,
        amount=payload.amount,
        idempotency_key=idempotency_key,
    )
    return TransferResponse(
        transaction_id=result.transaction.id,
        status=result.transaction.status,
        amount=result.money.amount,
        currency=result.money.currency,
    )


@router.post("/pay/merchant", response_model=TransferResponse, status_code=201)
def pay_merchant(
    payload: MerchantPaymentRequest,
    user: CurrentUserDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
) -> TransferResponse:
    result = WalletUseCases(session, bus=get_bus()).payer_marchand(
        user_id=user.user_id,
        merchant_account_id=payload.merchant_account_id,
        amount=payload.amount,
        origin_module=payload.origin_module,
        business_reference=payload.business_reference,
        idempotency_key=idempotency_key,
    )
    return TransferResponse(
        transaction_id=result.transaction.id,
        status=result.transaction.status,
        amount=result.money.amount,
        currency=result.money.currency,
    )


@router.post("/qr/generate", response_model=GenerateQrResponse, status_code=201)
def generate_qr(
    payload: GenerateQrRequest, user: CurrentUserDep, session: SessionDep
) -> GenerateQrResponse:
    """Génère le QR d'un compte marchand — réservé à son propriétaire.

    Ne déplace aucun fonds : pas d'`Idempotency-Key` requis (§0). Le paiement effectif reste
    `POST /wallet/pay/merchant`, appelé après que le frontend a décodé le QR via
    `POST /wallet/qr/verify`.
    """
    token, decoded = QrService(session).generer(
        requester_user_id=user.user_id,
        merchant_account_id=payload.merchant_account_id,
        amount=payload.amount,
        currency=payload.currency,
        origin_module=payload.origin_module,
        expires_in_seconds=payload.expires_in_seconds,
    )
    return GenerateQrResponse(
        payload=token,
        type="dynamic" if decoded.amount is not None else "static",
        merchant_account_id=decoded.merchant_account_id,
        amount=decoded.amount,
        currency=decoded.currency,
        origin_module=decoded.origin_module,
        expires_at=decoded.expires_at,
    )


@router.post("/qr/verify", response_model=VerifyQrResponse)
def verify_qr(
    payload: VerifyQrRequest, user: CurrentUserDep, session: SessionDep
) -> VerifyQrResponse:
    """Décode un QR scanné, avant l'appel à `POST /wallet/pay/merchant`."""
    decoded, _merchant = QrService(session).verifier(payload.payload)
    return VerifyQrResponse(
        merchant_account_id=decoded.merchant_account_id,
        amount=decoded.amount,
        currency=decoded.currency,
        origin_module=decoded.origin_module,
        expires_at=decoded.expires_at,
    )


@router.get("/transactions", response_model=Page[TransactionItem])
def list_transactions(
    user: CurrentUserDep,
    session: SessionDep,
    origin_module: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,
    from_date: Annotated[date | None, Query()] = None,
    to_date: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[TransactionItem]:
    rows, total = WalletUseCases(session).consulter_historique(
        user.user_id,
        origin_module=origin_module,
        type_=type,
        from_date=from_date,
        to_date=to_date,
        page=page,
        page_size=page_size,
    )
    return Page[TransactionItem](
        data=[
            TransactionItem(
                id=transaction.id,
                type=transaction.type,
                amount=_montant(transaction, entry),
                currency=(entry.currency if entry else transaction.currency) or "XOF",
                status=transaction.status,
                origin_module=transaction.origin_module,
                business_reference=transaction.business_reference,
                created_at=transaction.created_at,
            )
            for transaction, entry in rows
        ],
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=math.ceil(total / page_size) if total else 0,
        ),
    )


@router.get("/transactions/{transaction_id}", response_model=TransactionDetail)
def get_transaction(
    transaction_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> TransactionDetail:
    transaction, entry = WalletUseCases(session).consulter_transaction(
        user.user_id, transaction_id
    )
    return TransactionDetail(
        id=transaction.id,
        type=transaction.type,
        amount=_montant(transaction, entry),
        currency=(entry.currency if entry else transaction.currency) or "XOF",
        status=transaction.status,
        origin_module=transaction.origin_module,
        business_reference=transaction.business_reference,
        created_at=transaction.created_at,
        completed_at=transaction.completed_at,
        direction=entry.direction if entry else None,
    )


def _require_admin(user: CurrentUserDep) -> None:
    if user.role != "admin":
        raise Forbidden("Accès administrateur requis.", code="ADMIN_REQUIRED")


@router.post("/ops/backfill")
def backfill_wallet(
    payload: OpsBackfillRequest,
    user: CurrentUserDep,
    session: SessionDep,
):
    """Backfill interne: crée le wallet personnel si le provisioning événementiel a été manqué."""
    _require_admin(user)
    account = WalletUseCases(session).provisionner_compte(payload.user_id)
    phone = payload.phone
    if phone:
        from payfund_app.modules.wallet.infra.repositories import UserPhoneRepository

        UserPhoneRepository(session).upsert(payload.user_id, phone)
    return {"status": "ok", "account_id": str(account.id), "user_id": str(payload.user_id)}


@router.get("/ops/outbox")
def list_outbox_events(user: CurrentUserDep, session: SessionDep):
    """Vue interne des événements durables en attente de relay."""
    _require_admin(user)
    rows = OutboxRepository(session).pending(limit=100)
    return {
        "data": [
            {
                "id": str(row.id),
                "event_name": row.event_name,
                "status": row.status,
                "created_at": row.created_at,
                "published_at": row.published_at,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@router.post("/ops/outbox/relay")
def relay_outbox(user: CurrentUserDep, session: SessionDep):
    """Relaye manuellement les événements durables en attente."""
    _require_admin(user)
    from payfund_app.ops.maintenance import relay_outbox_events

    result = relay_outbox_events(session, get_bus())
    return {"scanned": result.scanned, "published": result.published}


@router.post("/ops/paystack/reconcile/{transaction_id}")
def reconcile_paystack_deposit(
    transaction_id: uuid.UUID,
    user: CurrentUserDep,
    session: SessionDep,
):
    """Reconciliation interne pour dépôts Paystack restés en `pending` après une webhook manquée."""
    _require_admin(user)
    use_cases = WalletUseCases(session, bus=get_bus())
    transaction = use_cases.transactions.get(transaction_id)
    if transaction is None:
        return {"status": "not_found"}
    if transaction.provider_reference is None:
        return {"status": "missing_provider_reference"}

    gateway = PaystackGateway()
    result = gateway.verifier_depot(transaction.provider_reference)
    if result.status is GatewayStatus.COMPLETED:
        use_cases.confirmer_operation(transaction.id, provider="paystack")
        ReconciliationLogRepository(session).append(
            transaction_id=transaction.id,
            provider="paystack",
            provider_reference=transaction.provider_reference,
            event="manual_reconcile",
            outcome="completed",
            reason="provider_completed",
        )
        return {"status": "completed", "transaction_id": str(transaction.id)}
    if result.status is GatewayStatus.FAILED:
        use_cases.echouer_operation(transaction.id, provider="paystack")
        ReconciliationLogRepository(session).append(
            transaction_id=transaction.id,
            provider="paystack",
            provider_reference=transaction.provider_reference,
            event="manual_reconcile",
            outcome="failed",
            reason="provider_failed",
        )
        return {"status": "failed", "transaction_id": str(transaction.id)}
    ReconciliationLogRepository(session).append(
        transaction_id=transaction.id,
        provider="paystack",
        provider_reference=transaction.provider_reference,
        event="manual_reconcile",
        outcome="pending",
        reason="provider_pending",
    )
    return {"status": "pending", "transaction_id": str(transaction.id)}


@router.get("/ops/paystack/{transaction_id}")
def inspect_paystack_transaction(
    transaction_id: uuid.UUID,
    user: CurrentUserDep,
    session: SessionDep,
):
    """Vue interne d'audit pour un dépôt Paystack."""
    _require_admin(user)
    transaction = WalletUseCases(session).transactions.get(transaction_id)
    if transaction is None:
        return {"status": "not_found"}
    return {
        "transaction_id": str(transaction.id),
        "provider_reference": transaction.provider_reference,
        "status": transaction.status,
        "amount": int(transaction.amount) if transaction.amount is not None else None,
        "currency": transaction.currency,
        "completed_at": transaction.completed_at,
        "origin_module": transaction.origin_module,
    }


@router.get("/ops/paystack/{transaction_id}/reconciliations")
def list_paystack_reconciliations(
    transaction_id: uuid.UUID,
    user: CurrentUserDep,
    session: SessionDep,
):
    """Historique des décisions de réconciliation pour une transaction Paystack."""
    _require_admin(user)
    rows = ReconciliationLogRepository(session).latest_for_transaction(transaction_id)
    return {
        "data": [
            {
                "id": str(row.id),
                "transaction_id": str(row.transaction_id),
                "provider": row.provider,
                "provider_reference": row.provider_reference,
                "event": row.event,
                "outcome": row.outcome,
                "reason": row.reason,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@router.get("/ops/paystack/pending")
def list_pending_paystack_transactions(user: CurrentUserDep, session: SessionDep):
    """Vue d'ensemble des dépôts Paystack qui attendent encore une décision finale."""
    _require_admin(user)
    rows = list(
        session.scalars(
            select(Transaction).where(
                Transaction.type == "deposit",
                Transaction.status == "pending",
                Transaction.provider_reference.is_not(None),
                Transaction.origin_module == "wallet",
            )
        )
    )
    return {
        "data": [
            {
                "transaction_id": str(row.id),
                "provider_reference": row.provider_reference,
                "amount": int(row.amount) if row.amount is not None else None,
                "currency": row.currency,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@router.get("/ops/paystack/summary")
def paystack_reconciliation_summary(user: CurrentUserDep, session: SessionDep):
    """Résumé de réconciliation Paystack pour support et ops."""
    _require_admin(user)
    base = select(Transaction).where(
        Transaction.type == "deposit",
        Transaction.origin_module == "wallet",
        Transaction.provider_reference.is_not(None),
    )
    rows = list(session.scalars(base))
    summary = {"pending": 0, "completed": 0, "failed": 0, "missing_reference": 0}
    for row in rows:
        if row.provider_reference is None:
            summary["missing_reference"] += 1
        elif row.status == "pending":
            summary["pending"] += 1
        elif row.status == "completed":
            summary["completed"] += 1
        elif row.status in {"failed", "reversed"}:
            summary["failed"] += 1
    summary["total"] = len(rows)
    return summary


@router.post("/webhooks/paystack")
async def paystack_webhook(
    request: Request,
    session: SessionDep,
    x_paystack_signature: Annotated[str | None, Header(alias="x-paystack-signature")] = None,
):
    raw_body = await request.body()
    secret = get_settings().paystack_webhook_secret or get_settings().paystack_secret_key
    if not secret:
        return {"status": "ignored", "reason": "missing_secret"}

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    if not x_paystack_signature or not hmac.compare_digest(expected, x_paystack_signature):
        return {"status": "invalid_signature", "reason": "signature_mismatch"}

    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return {"status": "ignored", "reason": "missing_reference"}

    transaction = WalletUseCases(session, bus=get_bus()).transactions.get_by_provider_reference(
        reference
    )
    if transaction is None:
        return {"status": "unknown_reference", "reason": "no_local_transaction"}

    use_cases = WalletUseCases(session, bus=get_bus())
    if event == "charge.success" or data.get("status") == "success":
        use_cases.confirmer_operation(transaction.id, provider="paystack")
        ReconciliationLogRepository(session).append(
            transaction_id=transaction.id,
            provider="paystack",
            provider_reference=transaction.provider_reference,
            event=str(event or "webhook"),
            outcome="completed",
            reason="charge_success",
        )
        return {
            "status": "processed",
            "transaction_status": "completed",
            "reason": "charge_success",
        }

    if data.get("status") in {"failed", "abandoned", "reversed"}:
        use_cases.echouer_operation(transaction.id, provider="paystack")
        ReconciliationLogRepository(session).append(
            transaction_id=transaction.id,
            provider="paystack",
            provider_reference=transaction.provider_reference,
            event=str(event or "webhook"),
            outcome="failed",
            reason=str(data.get("status")),
        )
        return {
            "status": "processed",
            "transaction_status": "failed",
            "reason": str(data.get("status")),
        }

    ReconciliationLogRepository(session).append(
        transaction_id=transaction.id,
        provider="paystack",
        provider_reference=transaction.provider_reference,
        event=str(event or "webhook"),
        outcome="ignored",
        reason="unhandled_event",
    )
    return {"status": "ignored", "reason": "unhandled_event"}
