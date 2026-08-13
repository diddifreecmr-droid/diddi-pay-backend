"""Routes du module `wallet` (Contrat API §1)."""

from __future__ import annotations

import math
import hmac
import hashlib
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

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
        created_at=transaction.created_at,
        completed_at=transaction.completed_at,
        direction=entry.direction if entry else None,
    )


@router.post("/webhooks/paystack")
async def paystack_webhook(
    request: Request,
    session: SessionDep,
    x_paystack_signature: Annotated[str | None, Header(alias="x-paystack-signature")] = None,
):
    raw_body = await request.body()
    secret = get_settings().paystack_webhook_secret or get_settings().paystack_secret_key
    if not secret:
        return {"status": "ignored"}

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    if not x_paystack_signature or not hmac.compare_digest(expected, x_paystack_signature):
        return {"status": "invalid_signature"}

    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return {"status": "ignored"}

    transaction = WalletUseCases(session, bus=get_bus()).transactions.get_by_provider_reference(
        reference
    )
    if transaction is None:
        return {"status": "unknown_reference"}

    use_cases = WalletUseCases(session, bus=get_bus())
    if event == "charge.success" or data.get("status") == "success":
        use_cases.confirmer_operation(transaction.id, provider="paystack")
        return {"status": "processed", "transaction_status": "completed"}

    if data.get("status") in {"failed", "abandoned", "reversed"}:
        use_cases.echouer_operation(transaction.id, provider="paystack")
        return {"status": "processed", "transaction_status": "failed"}

    return {"status": "ignored"}
