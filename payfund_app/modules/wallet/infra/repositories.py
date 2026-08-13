"""Accès données du module `wallet`."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session

from payfund_app.modules.wallet.domain.entities import (
    AccountStatus,
    AccountType,
    Direction,
)
from payfund_app.modules.wallet.domain.money import Balance, Money
from payfund_app.modules.wallet.infra.models import (
    Account,
    GatewayAccount,
    LedgerEntry,
    Transaction,
    UserPhone,
)


class AccountRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, account_id: uuid.UUID) -> Account | None:
        return self.session.get(Account, account_id)

    def get_by_user(
        self,
        user_id: uuid.UUID,
        *,
        currency: str = "XOF",
        account_type: AccountType = AccountType.USER,
    ) -> Account | None:
        """Le wallet personnel d'un utilisateur — jamais son éventuel compte marchand.

        Un utilisateur peut posséder un compte `user` et un compte `merchant` dans la même
        devise (§ commentaire sur `uq_accounts_user_currency_type`) : sans filtrer sur
        `account_type`, cette méthode serait ambiguë dès qu'un commerçant est aussi client.
        """
        return self.session.scalar(
            select(Account).where(
                Account.user_id == user_id,
                Account.currency == currency,
                Account.account_type == str(account_type),
            )
        )

    def get_for_update(self, account_id: uuid.UUID) -> Account | None:
        """Verrou de ligne — sérialise les débits concurrents sur un même compte.

        Le solde n'étant pas stocké (Architecture §3.1), rien n'empêcherait sans ce verrou deux
        retraits simultanés de passer chacun le contrôle de solde avant que l'autre n'écrive.
        """
        return self.session.scalar(
            select(Account).where(Account.id == account_id).with_for_update()
        )

    def get_by_reference(self, reference: str) -> Account | None:
        return self.session.scalar(select(Account).where(Account.reference == reference))

    def create(
        self,
        *,
        user_id: uuid.UUID | None,
        account_type: AccountType = AccountType.USER,
        currency: str = "XOF",
        reference: str | None = None,
        allows_negative_balance: bool = False,
    ) -> Account:
        account = Account(
            user_id=user_id,
            account_type=str(account_type),
            currency=currency,
            status=str(AccountStatus.ACTIVE),
            reference=reference,
            allows_negative_balance=allows_negative_balance,
        )
        self.session.add(account)
        self.session.flush()
        return account

    def set_status(self, account: Account, status: AccountStatus) -> None:
        account.status = str(status)
        self.session.flush()

    def balance(self, account_id: uuid.UUID) -> Balance:
        """Solde = somme des crédits − somme des débits sur toutes les écritures du compte.

        Aucune colonne de solde n'est maintenue (§3.1 : « Le solde n'est PAS stocké
        directement »). La colonne de cache décrite comme optionnelle n'est pas activée : elle
        est conditionnée dans le document à un besoin « mesuré », qui ne l'est pas encore.
        """
        signed = case(
            (LedgerEntry.direction == str(Direction.CREDIT), LedgerEntry.amount),
            else_=-LedgerEntry.amount,
        )
        total: Decimal | None = self.session.scalar(
            select(func.coalesce(func.sum(signed), 0)).where(
                LedgerEntry.account_id == account_id
            )
        )
        account = self.get(account_id)
        currency = account.currency if account else "XOF"
        return Balance.from_db(total or Decimal(0), currency)


class LedgerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_entry(
        self,
        *,
        account_id: uuid.UUID,
        transaction_id: uuid.UUID,
        direction: Direction,
        money: Money,
        reference: str | None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            account_id=account_id,
            transaction_id=transaction_id,
            direction=str(direction),
            amount=money.to_db(),
            currency=money.currency,
            reference=reference,
        )
        self.session.add(entry)
        return entry

    def entries_of(self, transaction_id: uuid.UUID) -> list[LedgerEntry]:
        return list(
            self.session.scalars(
                select(LedgerEntry).where(LedgerEntry.transaction_id == transaction_id)
            )
        )

    def entry_for_account(
        self, transaction_id: uuid.UUID, account_id: uuid.UUID
    ) -> LedgerEntry | None:
        return self.session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.transaction_id == transaction_id,
                LedgerEntry.account_id == account_id,
            )
        )


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, transaction_id: uuid.UUID) -> Transaction | None:
        return self.session.get(Transaction, transaction_id)

    def get_by_idempotency_key(self, key: str) -> Transaction | None:
        return self.session.scalar(
            select(Transaction).where(Transaction.idempotency_key == key)
        )

    def get_by_provider_reference(self, reference: str) -> Transaction | None:
        return self.session.scalar(
            select(Transaction).where(Transaction.provider_reference == reference)
        )

    def marquer(self, transaction: Transaction, status: str) -> Transaction:
        """Change le statut de l'en-tête. Ne touche jamais aux écritures, qui sont immuables."""
        transaction.status = str(status)
        if str(status) == "completed":
            transaction.completed_at = datetime.now(timezone.utc)
        self.session.flush()
        return transaction

    def create(
        self,
        *,
        type_: str,
        status: str,
        origin_module: str | None,
        business_reference: str | None = None,
        idempotency_key: str | None,
        completed_at: datetime | None = None,
        account_id: uuid.UUID | None = None,
        money: Money | None = None,
        provider_reference: str | None = None,
        reverses_transaction_id: uuid.UUID | None = None,
    ) -> Transaction:
        transaction = Transaction(
            type=type_,
            status=status,
            origin_module=origin_module,
            business_reference=business_reference,
            idempotency_key=idempotency_key,
            completed_at=completed_at,
            account_id=account_id,
            amount=money.to_db() if money else None,
            currency=money.currency if money else None,
            provider_reference=provider_reference,
            reverses_transaction_id=reverses_transaction_id,
        )
        self.session.add(transaction)
        self.session.flush()
        return transaction

    def _history_query(
        self,
        account_id: uuid.UUID,
        *,
        origin_module: str | None,
        type_: str | None,
        from_date: date | None,
        to_date: date | None,
    ) -> Select:
        # Jointure externe : une transaction apparaît soit parce qu'elle porte une écriture sur
        # ce compte, soit — dépôt encore en attente de l'opérateur — parce qu'elle en est à
        # l'origine sans avoir encore produit d'écriture.
        conditions = [
            or_(LedgerEntry.id.is_not(None), Transaction.account_id == account_id)
        ]
        if origin_module:
            conditions.append(Transaction.origin_module == origin_module)
        if type_:
            conditions.append(Transaction.type == type_)
        if from_date:
            conditions.append(func.date(Transaction.created_at) >= from_date)
        if to_date:
            conditions.append(func.date(Transaction.created_at) <= to_date)
        return (
            select(Transaction, LedgerEntry)
            .outerjoin(
                LedgerEntry,
                and_(
                    LedgerEntry.transaction_id == Transaction.id,
                    LedgerEntry.account_id == account_id,
                ),
            )
            .where(and_(*conditions))
        )

    def history(
        self,
        account_id: uuid.UUID,
        *,
        origin_module: str | None = None,
        type_: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[tuple[Transaction, LedgerEntry | None]], int]:
        """Historique du compte.

        L'écriture renvoyée avec la transaction donne le montant *vu par ce compte* — c'est là
        que vit le montant réel (§3.1). Elle est `None` pour une opération encore en attente de
        l'opérateur, où seul l'en-tête de transaction existe.
        """
        query = self._history_query(
            account_id,
            origin_module=origin_module,
            type_=type_,
            from_date=from_date,
            to_date=to_date,
        )
        total = self.session.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0
        rows = self.session.execute(
            query.order_by(Transaction.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return [(row[0], row[1]) for row in rows], total


class GatewayAccountRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def account_id_for(self, provider: str) -> uuid.UUID | None:
        return self.session.scalar(
            select(GatewayAccount.account_id).where(GatewayAccount.provider == provider)
        )

    def register(self, provider: str, account_id: uuid.UUID) -> GatewayAccount:
        gateway = GatewayAccount(provider=provider, account_id=account_id)
        self.session.add(gateway)
        self.session.flush()
        return gateway


class UserPhoneRepository:
    """Index local téléphone → user_id (voir en-tête de `models.py`)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def user_id_for(self, phone: str) -> uuid.UUID | None:
        return self.session.scalar(select(UserPhone.user_id).where(UserPhone.phone == phone))

    def upsert(self, user_id: uuid.UUID, phone: str) -> None:
        existing = self.session.get(UserPhone, user_id)
        if existing is None:
            self.session.add(UserPhone(user_id=user_id, phone=phone))
        else:
            existing.phone = phone
            existing.updated_at = datetime.now()
        self.session.flush()
