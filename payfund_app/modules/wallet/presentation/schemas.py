"""Schémas d'entrée/sortie du module `wallet` — miroir exact du contrat API §1."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

# Liste des opérateurs du §3.1 (`wallet.gateway_accounts.provider`).
Provider = Literal["paystack", "orange_money", "mtn_momo", "wave", "moov", "card_gateway"]


class Pagination(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class Page(BaseModel, Generic[T]):
    data: list[T]
    pagination: Pagination


class BalanceResponse(BaseModel):
    account_id: uuid.UUID
    balance: int
    currency: str
    status: str


class DepositRequest(BaseModel):
    # `Literal` plutôt qu'un contrôle manuel : un opérateur inconnu ressort en 422 par le
    # gestionnaire de validation standard, sans inventer de code d'erreur absent du contrat.
    provider: Provider
    amount: int = Field(gt=0)
    phone: str = Field(min_length=4, max_length=20)
    email: str | None = Field(default=None, max_length=254)


class WithdrawRequest(DepositRequest):
    pass


class PendingOperationResponse(BaseModel):
    """Réponse `202` du dépôt et du retrait (Contrat §1)."""

    transaction_id: uuid.UUID
    status: str


class DepositResponse(PendingOperationResponse):
    provider_reference: str | None = None
    authorization_url: str | None = None
    access_code: str | None = None


class TransferRequest(BaseModel):
    recipient_phone: str = Field(min_length=4, max_length=20)
    amount: int = Field(gt=0)


class MerchantPaymentRequest(BaseModel):
    merchant_account_id: uuid.UUID
    amount: int = Field(gt=0)
    origin_module: str | None = Field(default=None, max_length=30)
    business_reference: str | None = Field(default=None, max_length=100)


class TransferResponse(BaseModel):
    transaction_id: uuid.UUID
    status: str
    amount: int
    currency: str


class TransactionItem(BaseModel):
    id: uuid.UUID
    type: str
    amount: int
    currency: str
    status: str
    origin_module: str | None
    business_reference: str | None
    created_at: datetime


class TransactionDetail(TransactionItem):
    # Sens du mouvement pour le compte de l'appelant : le contrat expose un montant unique,
    # `direction` lève l'ambiguïté entre un envoi et une réception. `None` tant qu'une opération
    # n'a pas produit d'écriture (dépôt en attente de l'opérateur).
    direction: str | None
    completed_at: datetime | None


# --- QR code de paiement marchand (Contrat §1, format fixé en §3) -----------


class GenerateQrRequest(BaseModel):
    merchant_account_id: uuid.UUID
    # Absent → QR statique, montant saisi par le payeur (cas décrit au contrat). Présent → QR à
    # montant fixe (facture), extension au-delà du contrat.
    amount: int | None = Field(default=None, gt=0)
    currency: str = Field(default="XOF", min_length=3, max_length=3)
    origin_module: str | None = Field(default=None, max_length=30)
    # Uniquement valide avec un montant fixe. Plafonné côté service à 24h.
    expires_in_seconds: int | None = Field(default=None, gt=0, le=86_400)


class GenerateQrResponse(BaseModel):
    payload: str
    type: Literal["static", "dynamic"]
    merchant_account_id: uuid.UUID
    amount: int | None
    currency: str
    origin_module: str | None
    expires_at: datetime | None


class VerifyQrRequest(BaseModel):
    payload: str = Field(min_length=1)


class VerifyQrResponse(BaseModel):
    merchant_account_id: uuid.UUID
    amount: int | None
    currency: str
    origin_module: str | None
    expires_at: datetime | None


class OpsBackfillRequest(BaseModel):
    user_id: uuid.UUID
    phone: str | None = Field(default=None, max_length=20)
