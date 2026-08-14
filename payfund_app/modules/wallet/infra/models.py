"""Modèles SQLAlchemy du schéma `wallet` (Architecture §3.1).

Écarts assumés par rapport au SQL du document, tous décidés explicitement :

* `accounts.user_id` passe de `NOT NULL UNIQUE` à `NULL` + index unique partiel. Le document
  déclarait les deux à la fois : `user_id NOT NULL UNIQUE` et `account_type = 'technical'`
  (comptes suspense). Un compte suspense n'a pas de propriétaire — l'insertion était impossible.
* `user_phones` : table ajoutée pour résoudre `recipient_phone` → `user_id` sur le transfert P2P,
  alimentée par l'événement `user.registered` (dont le payload porte déjà le téléphone,
  DiddiFreeID §4). DiddiFreeID n'expose aucune recherche par téléphone.
* L'unicité (`user_id`, `currency`) posée pour le multi-devises inclut aussi `account_type` : un
  même utilisateur peut posséder un compte `user` (son wallet personnel) et un compte `merchant`
  dans la même devise — le cas d'un commerçant qui est aussi client. C'est devenu nécessaire dès
  le QR code de paiement : le propriétaire d'un compte marchand, qui génère son propre QR, a par
  construction déjà un wallet personnel.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from payfund_app.core.database import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        # Un utilisateur n'a qu'un seul compte **par devise et par type** — un seul compte tout
        # court tant qu'on n'opère qu'en XOF avec un seul type. Un même utilisateur peut donc
        # posséder à la fois un compte `user` et un compte `merchant` en XOF (un commerçant qui
        # est aussi client), mais pas deux comptes `user` en XOF. Les comptes sans propriétaire
        # (technical, pool de campagne, position de change) échappent à la contrainte.
        Index(
            "uq_accounts_user_currency_type",
            "user_id",
            "currency",
            "account_type",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        CheckConstraint(
            "account_type IN ('user','merchant','technical')", name="ck_accounts_type"
        ),
        CheckConstraint(
            "status IN ('active','frozen','closed')", name="ck_accounts_status"
        ),
        CheckConstraint(
            "(account_type = 'technical') OR (user_id IS NOT NULL)",
            name="ck_accounts_owner_required",
        ),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Référence *logique* vers identity.users(id) (DiddiFreeID) — pas de FK inter-service.
    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="XOF")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # Sur un compte technique : à quoi il sert (ex. "fund:campaign:<id>"). Null sur compte user.
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Un compte de contrepartie (suspense de passerelle, position de change) est négatif par
    # construction pendant l'opération. Un compte client ou un pool de campagne, jamais.
    allows_negative_balance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LedgerEntry(Base):
    """Écriture immuable. Aucun UPDATE/DELETE n'est jamais émis sur cette table (§2)."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("direction IN ('debit','credit')", name="ck_ledger_direction"),
        CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
        Index("idx_ledger_account", "account_id", "created_at"),
        Index("idx_ledger_transaction", "transaction_id"),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("wallet.accounts.id"), nullable=False
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Transaction(Base):
    """En-tête d'une opération. Les mouvements réels vivent dans `ledger_entries`.

    `account_id`, `amount` et `currency` ne figuraient pas au §3.1, où le montant vivait
    uniquement sur les écritures. Ils deviennent nécessaires avec le passage des écritures **à la
    confirmation** pour les dépôts : entre l'initiation et la réponse de l'opérateur, une
    transaction n'a aucune écriture, donc ni montant ni compte rattaché — elle serait invisible
    dans l'historique et `GET /wallet/transactions/{id}` ne pourrait rien en dire.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','completed','failed','reversed')",
            name="ck_transactions_status",
        ),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    origin_module: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Compte à l'origine de l'opération — sert à retrouver une transaction encore sans écriture.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("wallet.accounts.id"), nullable=True
    )
    # Référence métier fournie par le module appelant (course, commande, facture, etc.).
    business_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    # Identifiant de l'opération chez l'opérateur, pour le support et la réconciliation.
    provider_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Transaction d'origine, sur une contre-passation.
    reverses_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # Unicité globale : c'est elle qui rend le rejeu impossible (Architecture §4).
    idempotency_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GatewayAccount(Base):
    """Un compte suspense par opérateur (§2 : « Mobile Money suspense »)."""

    __tablename__ = "gateway_accounts"
    __table_args__ = ({"schema": "wallet"},)

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("wallet.accounts.id"), nullable=False
    )


class ExchangeRate(Base):
    """Taux de change daté, alimenté par le back-office ou un fournisseur de cotations.

    Aucun taux n'est codé en dur : sans ligne ici, une conversion est refusée plutôt que
    devinée. Le taux retenu est **figé** sur la conversion (`CurrencyConversion.rate`), pour
    qu'un historique rejoué six mois plus tard donne le même résultat.
    """

    __tablename__ = "exchange_rates"
    __table_args__ = (
        CheckConstraint("rate > 0", name="ck_rates_positive"),
        CheckConstraint("base_currency <> quote_currency", name="ck_rates_distinct"),
        Index("idx_rates_pair", "base_currency", "quote_currency", "valid_from"),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    # 1 unité usuelle de `base` vaut `rate` unités usuelles de `quote`.
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CurrencyConversion(Base):
    """Trace d'une conversion : les deux transactions ledger et le taux appliqué.

    Une conversion ne peut pas être une transaction unique — ses deux écritures seraient dans des
    devises différentes et ne sommeraient pas à zéro. Ce sont donc deux transactions équilibrées,
    chacune dans sa devise, reliées ici.
    """

    __tablename__ = "currency_conversions"
    __table_args__ = ({"schema": "wallet"},)

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("wallet.accounts.id"), nullable=False
    )
    source_transaction_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    target_transaction_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    source_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    target_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserPhone(Base):
    """Index téléphone → user_id, alimenté par `user.registered` / `user.updated`."""

    __tablename__ = "user_phones"
    __table_args__ = ({"schema": "wallet"},)

    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KycDocument(Base):
    """Référence KYC vers un document stocké ailleurs, par exemple diddifiles."""

    __tablename__ = "kyc_documents"
    __table_args__ = (
        Index("idx_kyc_documents_user", "user_id", "created_at"),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    file_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    source_module: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxEvent(Base):
    """Événement durable à relayer vers le bus interne.

    Le bus Redis reste le transport d'exécution, mais cette table permet de rejouer un publish
    raté après crash du process ou indisponibilité temporaire du consumer.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("idx_outbox_status_created_at", "status", "created_at"),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_name: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WebhookInboxEvent(Base):
    """Journal durable des callbacks provider déjà vus."""

    __tablename__ = "webhook_inbox_events"
    __table_args__ = (
        Index("idx_webhook_inbox_provider_event", "provider", "event_key"),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    event_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReconciliationLog(Base):
    """Journal durable des décisions de réconciliation provider."""

    __tablename__ = "reconciliation_logs"
    __table_args__ = (
        Index("idx_reconciliation_logs_transaction", "transaction_id", "created_at"),
        {"schema": "wallet"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
