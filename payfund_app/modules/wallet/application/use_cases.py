"""Use cases du module `wallet`.

**Instant de passage des écritures** — décision produit, les documents ne la tranchaient pas :

* **Dépôt** : aucune écriture à l'initiation. L'argent n'existe pas tant que l'opérateur n'a pas
  confirmé ; l'écrire plus tôt gonflerait un solde dépensable sur la foi d'une opération qui peut
  encore échouer. Échec → transaction `failed`, jamais aucune écriture.
* **Retrait** : écritures dès l'initiation (débit du client, crédit du compte suspense). Les fonds
  sont ainsi réservés — sans cela, dix retraits simultanés passeraient tous le contrôle de solde.
  Échec → contre-passation (§2 : « une correction se fait par une écriture inverse »), transaction
  d'origine marquée `reversed`.

Ce que cela donne des quatre statuts du §3.1 : `failed` = opération qui n'a jamais produit
d'écriture ; `reversed` = opération écrite puis annulée par contre-passation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from payfund_app.modules.wallet.application.ledger import LedgerService
from payfund_app.modules.wallet.domain.entities import (
    AccountType,
    Direction,
    PostingLine,
    TransactionStatus,
    TransactionType,
)
from payfund_app.modules.wallet.domain.errors import (
    AccountNotFound,
    CannotTransferToSelf,
    GatewayUnavailable,
    InvalidAmountError,
    InvalidPin,
    InvalidStepUpOtp,
    MerchantNotFound,
    PinLocked,
    PinRequired,
    RecipientNotFound,
    RecoveryCodeInvalid,
    StepUpOtpExpired,
    StepUpOtpRequired,
    TransactionNotFound,
)
from payfund_app.modules.wallet.domain.money import Balance, InvalidAmount, Money
from payfund_app.modules.wallet.infra.gateways import (
    GatewayStatus,
    PaymentGatewayPort,
    get_gateway,
)
from payfund_app.modules.wallet.infra.models import Account, LedgerEntry, Transaction
from payfund_app.modules.wallet.infra.repositories import (
    AccountRepository,
    GatewayAccountRepository,
    LedgerRepository,
    PinRecoveryAuditRepository,
    OutboxRepository,
    TransactionPinRecoveryCodeRepository,
    TransactionPinRepository,
    TransferOtpChallengeRepository,
    TransactionRepository,
    UserPhoneRepository,
)
from payfund_app.shared_kernel.events.bus import EventBusPort
from payfund_app.shared_kernel.events.types import PAYMENT_COMPLETED, Event


def to_money(amount: int, currency: str = "XOF") -> Money:
    """Convertit une entrée utilisateur en `Money`, en traduisant l'erreur en 422."""
    try:
        return Money(amount, currency)
    except InvalidAmount as exc:
        raise InvalidAmountError(str(exc)) from exc


@dataclass
class TransferResult:
    transaction: Transaction
    money: Money
    replayed: bool


@dataclass
class DepositResult:
    transaction: Transaction
    authorization_url: str | None
    access_code: str | None


class WalletUseCases:
    def __init__(
        self,
        session: Session,
        bus: EventBusPort | None = None,
        gateway: PaymentGatewayPort | None = None,
    ) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.transactions = TransactionRepository(session)
        self.ledger_repo = LedgerRepository(session)
        self.phones = UserPhoneRepository(session)
        self.pins = TransactionPinRepository(session)
        self.pin_recovery_codes = TransactionPinRecoveryCodeRepository(session)
        self.transfer_otp = TransferOtpChallengeRepository(session)
        self.pin_recovery_audit = PinRecoveryAuditRepository(session)
        self.gateways = GatewayAccountRepository(session)
        self.outbox = OutboxRepository(session)
        self.ledger = LedgerService(session)
        self.bus = bus
        self._gateway = gateway

    @property
    def gateway(self) -> PaymentGatewayPort:
        if self._gateway is None:
            self._gateway = get_gateway()
        return self._gateway

    # --- Provisionnement -----------------------------------------------------

    def provisionner_compte(self, user_id: uuid.UUID) -> Account:
        """Crée le compte wallet d'un utilisateur, à la réception de `user.registered`.

        DiddiFreeID §4 / Architecture §5 : « l'utilisateur n'a jamais besoin d'un appel explicite
        "créer mon wallet" ». Idempotent : un événement redélivré ne crée pas un second compte.
        """
        existing = self.accounts.get_by_user(user_id)
        if existing is not None:
            return existing
        return self.accounts.create(user_id=user_id, account_type=AccountType.USER)

    def provisionner_compte_marchand(self, user_id: uuid.UUID) -> Account:
        """Crée le compte marchand d'un utilisateur, de manière idempotente.

        Utile pour les modules qui veulent encaisser via QR sans que l'opérateur support doive
        bricoler une ligne SQL. Le compte marchand reste distinct du wallet personnel.
        """
        existing = self.accounts.get_by_user(user_id, account_type=AccountType.MERCHANT)
        if existing is not None:
            return existing
        return self.accounts.create(user_id=user_id, account_type=AccountType.MERCHANT)

    def compte_de(self, user_id: uuid.UUID) -> Account:
        account = self.accounts.get_by_user(user_id)
        if account is None:
            # Self-heal: if the `user.registered` event was missed or replay hasn't happened yet,
            # create the personal wallet lazily the first time the user actually touches Payfund.
            account = self.provisionner_compte(user_id)
        return account

    def has_pin(self, user_id: uuid.UUID) -> bool:
        return self.pins.get(self.compte_de(user_id).id) is not None

    def pin_status(self, user_id: uuid.UUID) -> tuple[Account, object | None]:
        account = self.compte_de(user_id)
        return account, self.pins.get(account.id)

    def _pin_salt(self) -> str:
        return secrets.token_urlsafe(12)

    def _hash_pin(self, pin: str, salt: str) -> str:
        digest = hashlib.sha256(f"{salt}:{pin}".encode()).hexdigest()
        return digest

    def _hash_recovery_code(self, code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def _hash_otp_code(self, code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def _verify_pin(self, account_id: uuid.UUID, pin: str) -> None:
        row = self.pins.get(account_id)
        if row is None:
            raise PinRequired()
        now = datetime.now(timezone.utc)
        if row.locked_until is not None and row.locked_until > now:
            raise PinLocked(details={"locked_until": row.locked_until})
        expected = self._hash_pin(pin, row.pin_salt)
        if not hmac.compare_digest(expected, row.pin_hash):
            attempts = self.pins.register_failure(row)
            raise InvalidPin(details={"attempts": attempts, "remaining": max(0, 5 - attempts)})
        self.pins.clear_lock(row)

    def _ensure_password_policy(self, pin: str, confirm_pin: str) -> None:
        if pin != confirm_pin:
            raise InvalidPin("Les deux PIN ne correspondent pas.")

    def define_pin(self, *, user_id: uuid.UUID, pin: str, confirm_pin: str) -> dict:
        account = self.compte_de(user_id)
        self._ensure_password_policy(pin, confirm_pin)
        salt = self._pin_salt()
        row = self.pins.set_pin(
            account_id=account.id,
            pin_hash=self._hash_pin(pin, salt),
            pin_salt=salt,
        )
        recovery_codes = []
        for _ in range(6):
            code = secrets.token_urlsafe(8)
            self.pin_recovery_codes.add(
                account_id=account.id, code_hash=self._hash_recovery_code(code)
            )
            recovery_codes.append(code)
        return {"account": account, "pin": row, "recovery_codes": recovery_codes}

    def change_pin(
        self, *, user_id: uuid.UUID, current_pin: str, new_pin: str, confirm_new_pin: str
    ) -> dict:
        account = self.compte_de(user_id)
        self._ensure_password_policy(new_pin, confirm_new_pin)
        self._verify_pin(account.id, current_pin)
        salt = self._pin_salt()
        row = self.pins.set_pin(
            account_id=account.id,
            pin_hash=self._hash_pin(new_pin, salt),
            pin_salt=salt,
        )
        return {"account": account, "pin": row}

    def lookup_recipient_display_name(self, phone: str) -> str | None:
        user_id = self.phones.user_id_for(phone)
        if user_id is None:
            return None
        visible = phone[-4:] if len(phone) >= 4 else phone
        return f"Compte se terminant par {visible}"

    def reset_pin_with_recovery(
        self, *, user_id: uuid.UUID, recovery_code: str, new_pin: str, confirm_new_pin: str
    ) -> dict:
        account = self.compte_de(user_id)
        self._ensure_password_policy(new_pin, confirm_new_pin)
        code_hash = self._hash_recovery_code(recovery_code)
        rows = self.pin_recovery_codes.list_for_account(account.id)
        match = None
        for row in rows:
            if row.consumed_at is None and hmac.compare_digest(row.code_hash, code_hash):
                match = row
                break
        if match is None:
            raise RecoveryCodeInvalid()
        match.consumed_at = datetime.now(timezone.utc)
        salt = self._pin_salt()
        self.pins.set_pin(
            account_id=account.id,
            pin_hash=self._hash_pin(new_pin, salt),
            pin_salt=salt,
        )
        return {"account": account}

    def admin_reset_pin(
        self,
        *,
        admin_user_id: uuid.UUID | None,
        user_id: uuid.UUID,
        new_pin: str,
        confirm_new_pin: str,
        reason: str,
    ) -> dict:
        account = self.compte_de(user_id)
        self._ensure_password_policy(new_pin, confirm_new_pin)
        salt = self._pin_salt()
        row = self.pins.set_pin(
            account_id=account.id,
            pin_hash=self._hash_pin(new_pin, salt),
            pin_salt=salt,
        )
        recovery_codes = []
        for _ in range(6):
            code = secrets.token_urlsafe(8)
            self.pin_recovery_codes.add(
                account_id=account.id, code_hash=self._hash_recovery_code(code)
            )
            recovery_codes.append(code)
        self.pin_recovery_audit.add(
            account_id=account.id,
            actor_user_id=admin_user_id,
            action="admin_reset_pin",
            reason=reason,
            metadata={"recovery_codes_issued": len(recovery_codes)},
        )
        return {"account": account, "pin": row, "recovery_codes": recovery_codes}

    def request_step_up_otp(
        self, *, user_id: uuid.UUID, recipient_phone: str, amount: int
    ) -> dict:
        account = self.compte_de(user_id)
        montant = to_money(amount, account.currency)
        challenge_code = "".join(secrets.choice("0123456789") for _ in range(6))
        challenge = self.transfer_otp.add(
            account_id=account.id,
            recipient_phone=recipient_phone,
            amount=montant.to_db(),
            code_hash=self._hash_otp_code(challenge_code),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        return {
            "challenge": challenge,
            "code": challenge_code,
            "masked_recipient": self._mask_phone(recipient_phone),
        }

    def _mask_phone(self, phone: str) -> str:
        if len(phone) <= 4:
            return phone
        return f"{phone[:3]}***{phone[-2:]}"

    def _verify_step_up_otp(
        self,
        *,
        account_id: uuid.UUID,
        recipient_phone: str,
        amount: int,
        otp_code: str | None,
    ) -> None:
        if amount < 50000:
            return
        latest = self.transfer_otp.latest_for_account(account_id)
        if latest is None:
            raise StepUpOtpRequired()
        now = datetime.now(timezone.utc)
        if latest.expires_at < now:
            raise StepUpOtpExpired()
        if latest.used_at is not None:
            raise StepUpOtpRequired()
        if latest.recipient_phone != recipient_phone or int(latest.amount) != amount:
            raise StepUpOtpRequired()
        if not otp_code:
            raise StepUpOtpRequired()
        if not hmac.compare_digest(self._hash_otp_code(otp_code), latest.code_hash):
            raise InvalidStepUpOtp()
        latest.used_at = now
        self.session.flush()

    # --- Lecture -------------------------------------------------------------

    def consulter_solde(self, user_id: uuid.UUID) -> tuple[Account, Balance]:
        account = self.compte_de(user_id)
        return account, self.accounts.balance(account.id)

    def consulter_historique(
        self,
        user_id: uuid.UUID,
        *,
        origin_module: str | None = None,
        type_: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[tuple[Transaction, LedgerEntry]], int]:
        account = self.compte_de(user_id)
        return self.transactions.history(
            account.id,
            origin_module=origin_module,
            type_=type_,
            from_date=from_date,
            to_date=to_date,
            page=page,
            page_size=page_size,
        )

    def consulter_transaction(
        self, user_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> tuple[Transaction, LedgerEntry | None]:
        account = self.compte_de(user_id)
        transaction = self.transactions.get(transaction_id)
        if transaction is None:
            raise TransactionNotFound()
        entry = self.ledger_repo.entry_for_account(transaction_id, account.id)
        if entry is None and transaction.account_id != account.id:
            # Ni écriture sur le compte de l'appelant, ni opération initiée par lui : on ne
            # révèle pas l'existence de la transaction d'un tiers.
            raise TransactionNotFound()
        return transaction, entry

    # --- Écriture ------------------------------------------------------------

    def transferer_p2p(
        self,
        *,
        user_id: uuid.UUID,
        recipient_phone: str,
        amount: int,
        pin: str,
        otp_code: str | None = None,
        idempotency_key: str,
    ) -> TransferResult:
        """Transfert P2P interne — synchrone, les deux écritures dans la même transaction DB
        (Contrat API §1)."""
        source = self.compte_de(user_id)
        self._verify_pin(source.id, pin)
        self._verify_step_up_otp(
            account_id=source.id,
            recipient_phone=recipient_phone,
            amount=amount,
            otp_code=otp_code,
        )
        montant = to_money(amount, source.currency)
        if not montant.is_positive():
            raise InvalidAmountError("Le montant doit être strictement positif.")

        recipient_user_id = self.phones.user_id_for(recipient_phone)
        if recipient_user_id is None:
            raise RecipientNotFound()
        if recipient_user_id == user_id:
            raise CannotTransferToSelf()

        destination = self.accounts.get_by_user(recipient_user_id)
        if destination is None:
            raise RecipientNotFound()

        transaction, replayed = self.ledger.transfer(
            source_account_id=source.id,
            destination_account_id=destination.id,
            montant=montant,
            type_=str(TransactionType.P2P_TRANSFER),
            reference=f"wallet:p2p:{user_id}",
            idempotency_key=idempotency_key,
            origin_module="wallet",
        )
        if not replayed:
            self._publish_completed(transaction, montant)
        return TransferResult(transaction, montant, replayed)

    def payer_marchand(
        self,
        *,
        user_id: uuid.UUID,
        merchant_account_id: uuid.UUID,
        amount: int,
        origin_module: str | None,
        business_reference: str | None,
        idempotency_key: str,
    ) -> TransferResult:
        """Paiement marchand. Le `merchant_account_id` est fourni par le QR scanné (§1)."""
        source = self.compte_de(user_id)
        montant = to_money(amount, source.currency)
        if not montant.is_positive():
            raise InvalidAmountError("Le montant doit être strictement positif.")

        merchant = self.accounts.get(merchant_account_id)
        if merchant is None or merchant.account_type != str(AccountType.MERCHANT):
            raise MerchantNotFound()
        if merchant.id == source.id:
            raise CannotTransferToSelf()

        transaction, replayed = self.ledger.transfer(
            source_account_id=source.id,
            destination_account_id=merchant.id,
            montant=montant,
            type_=str(TransactionType.MERCHANT_PAYMENT),
            reference=f"wallet:merchant:{merchant.id}",
            idempotency_key=idempotency_key,
            origin_module=origin_module,
        )
        if business_reference is not None:
            transaction.business_reference = business_reference
            self.session.flush()
        if not replayed:
            self._publish_completed(transaction, montant)
        return TransferResult(transaction, montant, replayed)

    # --- Interne -------------------------------------------------------------

    def _publish_completed(self, transaction: Transaction, montant: Money) -> None:
        if self.bus is None:
            self.outbox.enqueue(
                event_name=PAYMENT_COMPLETED,
                payload={
                    "transaction_id": str(transaction.id),
                    "type": transaction.type,
                    "amount": montant.amount,
                    "currency": montant.currency,
                    "origin_module": transaction.origin_module,
                },
            )
            return
        event = Event(
            PAYMENT_COMPLETED,
            {
                "transaction_id": str(transaction.id),
                "type": transaction.type,
                "amount": montant.amount,
                "currency": montant.currency,
                "origin_module": transaction.origin_module,
            },
        )
        self.outbox.enqueue(event_name=event.event, payload=event.payload)
        self.bus.publish(event)

    def montant_vu_par(self, entry: LedgerEntry) -> tuple[Money, str]:
        """Montant et sens d'une transaction du point de vue du compte de l'appelant."""
        return Money.from_db(entry.amount, entry.currency), entry.direction

    # --- Dépôt et retrait ----------------------------------------------------

    def _compte_suspense(self, provider: str, currency: str = "XOF") -> uuid.UUID:
        """Compte technique de l'opérateur, créé à la première utilisation.

        C'est le « compte technique "Mobile Money suspense" représentant les fonds encaissés côté
        opérateur mais pas encore réconciliés » du §2.
        """
        existing = self.gateways.account_id_for(provider)
        if existing is not None:
            return existing
        account = self.accounts.create(
            user_id=None,
            account_type=AccountType.TECHNICAL,
            currency=currency,
            reference=f"gateway:{provider}",
            allows_negative_balance=True,
        )
        self.gateways.register(provider, account.id)
        return account.id

    def deposer(
        self,
        *,
        user_id: uuid.UUID,
        provider: str,
        amount: int,
        phone: str,
        email: str | None,
        idempotency_key: str,
    ) -> DepositResult:
        """Initie un dépôt. **Aucune écriture** tant que l'opérateur n'a pas confirmé."""
        rejeu = self.transactions.get_by_idempotency_key(idempotency_key)
        if rejeu is not None:
            return DepositResult(rejeu, None, None)

        compte = self.compte_de(user_id)
        # Le montant reçu est exprimé dans l'unité mineure de la devise du compte.
        montant = to_money(amount, compte.currency)
        if not montant.is_positive():
            raise InvalidAmountError("Le montant doit être strictement positif.")
        self._compte_suspense(provider, compte.currency)
        if provider == "paystack" and not email:
            raise InvalidAmountError("L'adresse e-mail est requise pour Paystack.")

        transaction = self.transactions.create(
            type_=str(TransactionType.DEPOSIT),
            status=str(TransactionStatus.PENDING),
            origin_module="wallet",
            idempotency_key=idempotency_key,
            account_id=compte.id,
            money=montant,
        )

        try:
            operation = self.gateway.initier_depot(
                provider=provider,
                phone=phone,
                email=email,
                montant=montant.amount,
                reference=str(transaction.id),
            )
        except Exception as exc:
            raise GatewayUnavailable() from exc

        transaction.provider_reference = operation.provider_reference
        self.session.flush()

        if operation.status is GatewayStatus.COMPLETED:
            self.confirmer_operation(transaction.id, provider=provider)
        elif operation.status is GatewayStatus.FAILED:
            self.echouer_operation(transaction.id, provider=provider)

        return DepositResult(transaction, operation.authorization_url, operation.access_code)

    def retirer(
        self,
        *,
        user_id: uuid.UUID,
        provider: str,
        amount: int,
        phone: str,
        idempotency_key: str,
    ) -> Transaction:
        """Initie un retrait. Les écritures sont passées **tout de suite** : les fonds quittent
        le compte du client vers le compte suspense, donc ils sont réservés."""
        rejeu = self.transactions.get_by_idempotency_key(idempotency_key)
        if rejeu is not None:
            return rejeu

        compte = self.compte_de(user_id)
        montant = to_money(amount, compte.currency)
        if not montant.is_positive():
            raise InvalidAmountError("Le montant doit être strictement positif.")
        suspense_id = self._compte_suspense(provider, compte.currency)

        transaction, _ = self.ledger.transfer(
            source_account_id=compte.id,
            destination_account_id=suspense_id,
            montant=montant,
            type_=str(TransactionType.WITHDRAWAL),
            reference=f"wallet:withdrawal:{provider}",
            idempotency_key=idempotency_key,
            origin_module="wallet",
            status=TransactionStatus.PENDING,
        )
        transaction.account_id = compte.id
        transaction.amount = montant.to_db()
        transaction.currency = montant.currency
        self.session.flush()

        try:
            operation = self.gateway.initier_retrait(
                provider=provider,
                phone=phone,
                montant=montant.amount,
                reference=str(transaction.id),
            )
        except Exception as exc:
            raise GatewayUnavailable() from exc

        transaction.provider_reference = operation.provider_reference
        self.session.flush()

        if operation.status is GatewayStatus.COMPLETED:
            self.confirmer_operation(transaction.id, provider=provider)
        elif operation.status is GatewayStatus.FAILED:
            self.echouer_operation(transaction.id, provider=provider)

        return transaction

    def confirmer_operation(self, transaction_id: uuid.UUID, *, provider: str) -> Transaction:
        """L'opérateur a confirmé l'opération.

        Dépôt : c'est ici que les deux écritures sont passées. Retrait : elles existent déjà, on
        se contente de clore la transaction.
        """
        transaction = self.transactions.get(transaction_id)
        if transaction is None:
            raise TransactionNotFound()
        if transaction.status != str(TransactionStatus.PENDING):
            return transaction

        if transaction.type == str(TransactionType.DEPOSIT):
            montant = Money.from_db(transaction.amount, transaction.currency or "XOF")
            suspense_id = self._compte_suspense(provider)
            self.ledger.post_sur_transaction(
                transaction,
                lines=[
                    PostingLine(
                        suspense_id,
                        Direction.DEBIT,
                        montant,
                        f"wallet:deposit:{provider}",
                    ),
                    PostingLine(
                        transaction.account_id,
                        Direction.CREDIT,
                        montant,
                        f"wallet:deposit:{provider}",
                    ),
                ],
            )

        self.transactions.marquer(transaction, TransactionStatus.COMPLETED)
        if transaction.amount is not None:
            self._publish_completed(
                transaction, Money.from_db(transaction.amount, transaction.currency or "XOF")
            )
        return transaction

    def echouer_operation(self, transaction_id: uuid.UUID, *, provider: str) -> Transaction:
        """L'opérateur a rejeté l'opération.

        Dépôt : aucune écriture n'avait été passée, la transaction devient simplement `failed`.
        Retrait : les fonds avaient quitté le compte, il faut les y ramener par contre-passation
        — jamais par un `UPDATE`/`DELETE` sur les écritures d'origine (§2).
        """
        transaction = self.transactions.get(transaction_id)
        if transaction is None:
            raise TransactionNotFound()
        if transaction.status != str(TransactionStatus.PENDING):
            return transaction

        if transaction.type == str(TransactionType.WITHDRAWAL):
            self.ledger.contre_passer(transaction, reference=f"wallet:reversal:{provider}")
            self.transactions.marquer(transaction, TransactionStatus.REVERSED)
        else:
            self.transactions.marquer(transaction, TransactionStatus.FAILED)
        return transaction
