"""Accès données du module `wallet`."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
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
    KycDocument,
    PinRecoveryAudit,
    TransactionPin,
    TransactionPinRecoveryCode,
    TransferOtpChallenge,
    OutboxEvent,
    ReconciliationLog,
    WebhookInboxEvent,
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
        """Verrou de ligne — sérialise les débits concurrents sur un même compte."""
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
        conditions = [or_(LedgerEntry.id.is_not(None), Transaction.account_id == account_id)]
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
        query = self._history_query(
            account_id,
            origin_module=origin_module,
            type_=type_,
            from_date=from_date,
            to_date=to_date,
        )
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.session.execute(
            query.order_by(Transaction.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return [(row[0], row[1]) for row in rows], total


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(self, *, event_name: str, payload: dict) -> OutboxEvent:
        event = OutboxEvent(event_name=event_name, payload=payload, status="pending")
        self.session.add(event)
        self.session.flush()
        return event

    def pending(self, limit: int = 100) -> list[OutboxEvent]:
        return list(
            self.session.scalars(
                select(OutboxEvent).where(OutboxEvent.status == "pending").limit(limit)
            )
        )

    def mark_published(self, event: OutboxEvent) -> None:
        event.status = "published"
        event.published_at = datetime.now(timezone.utc)
        self.session.flush()


class WebhookInboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def seen(self, event_key: str) -> WebhookInboxEvent | None:
        return self.session.scalar(
            select(WebhookInboxEvent).where(WebhookInboxEvent.event_key == event_key)
        )

    def record(
        self,
        *,
        provider: str,
        event_key: str,
        payload: dict,
        status: str = "received",
    ) -> WebhookInboxEvent:
        row = WebhookInboxEvent(
            provider=provider,
            event_key=event_key,
            payload=payload,
            status=status,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def mark_processed(self, row: WebhookInboxEvent) -> None:
        row.status = "processed"
        row.processed_at = datetime.now(timezone.utc)
        self.session.flush()


class ReconciliationLogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append(
        self,
        *,
        transaction_id: uuid.UUID,
        provider: str,
        provider_reference: str | None,
        event: str,
        outcome: str,
        reason: str | None = None,
    ) -> ReconciliationLog:
        row = ReconciliationLog(
            transaction_id=transaction_id,
            provider=provider,
            provider_reference=provider_reference,
            event=event,
            outcome=outcome,
            reason=reason,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def latest_for_transaction(
        self, transaction_id: uuid.UUID, limit: int = 20
    ) -> list[ReconciliationLog]:
        return list(
            self.session.scalars(
                select(ReconciliationLog)
                .where(ReconciliationLog.transaction_id == transaction_id)
                .order_by(ReconciliationLog.created_at.desc())
                .limit(limit)
            )
        )


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


class KycDocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        user_id: uuid.UUID,
        file_id: str,
        document_type: str,
        status: str = "pending",
        source_module: str | None = None,
    ) -> KycDocument:
        row = KycDocument(
            user_id=user_id,
            file_id=file_id,
            document_type=document_type,
            status=status,
            source_module=source_module,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_user(self, user_id: uuid.UUID) -> list[KycDocument]:
        return list(
            self.session.scalars(
                select(KycDocument)
                .where(KycDocument.user_id == user_id)
                .order_by(KycDocument.created_at.desc())
            )
        )


class TransactionPinRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, account_id: uuid.UUID) -> TransactionPin | None:
        return self.session.get(TransactionPin, account_id)

    def set_pin(
        self,
        *,
        account_id: uuid.UUID,
        pin_hash: str,
        pin_salt: str,
    ) -> TransactionPin:
        row = self.get(account_id)
        if row is None:
            row = TransactionPin(
                account_id=account_id,
                pin_hash=pin_hash,
                pin_salt=pin_salt,
                failed_attempts=0,
            )
            self.session.add(row)
        else:
            row.pin_hash = pin_hash
            row.pin_salt = pin_salt
            row.failed_attempts = 0
            row.locked_until = None
            row.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return row

    def register_failure(self, row: TransactionPin, *, lock_minutes: int = 15) -> int:
        row.failed_attempts += 1
        if row.failed_attempts >= 5:
            row.locked_until = datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)
        row.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return row.failed_attempts

    def clear_lock(self, row: TransactionPin) -> None:
        row.failed_attempts = 0
        row.locked_until = None
        row.updated_at = datetime.now(timezone.utc)
        self.session.flush()


class TransactionPinRecoveryCodeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, *, account_id: uuid.UUID, code_hash: str) -> TransactionPinRecoveryCode:
        row = TransactionPinRecoveryCode(account_id=account_id, code_hash=code_hash)
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_account(self, account_id: uuid.UUID) -> list[TransactionPinRecoveryCode]:
        return list(
            self.session.scalars(
                select(TransactionPinRecoveryCode)
                .where(TransactionPinRecoveryCode.account_id == account_id)
                .order_by(TransactionPinRecoveryCode.created_at.desc())
            )
        )

    def get_by_hash(self, code_hash: str) -> TransactionPinRecoveryCode | None:
        return self.session.scalar(
            select(TransactionPinRecoveryCode).where(
                TransactionPinRecoveryCode.code_hash == code_hash
            )
        )


class TransferOtpChallengeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        account_id: uuid.UUID,
        recipient_phone: str,
        amount: Decimal,
        code_hash: str,
        expires_at: datetime,
    ) -> TransferOtpChallenge:
        row = TransferOtpChallenge(
            account_id=account_id,
            recipient_phone=recipient_phone,
            amount=amount,
            code_hash=code_hash,
            expires_at=expires_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, challenge_id: uuid.UUID) -> TransferOtpChallenge | None:
        return self.session.get(TransferOtpChallenge, challenge_id)

    def latest_for_account(self, account_id: uuid.UUID) -> TransferOtpChallenge | None:
        return self.session.scalar(
            select(TransferOtpChallenge)
            .where(TransferOtpChallenge.account_id == account_id)
            .order_by(TransferOtpChallenge.created_at.desc())
        )


class PinRecoveryAuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        *,
        account_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        action: str,
        reason: str,
        metadata: dict | None = None,
    ) -> PinRecoveryAudit:
        row = PinRecoveryAudit(
            account_id=account_id,
            actor_user_id=actor_user_id,
            action=action,
            reason=reason,
            metadata_json=metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row
