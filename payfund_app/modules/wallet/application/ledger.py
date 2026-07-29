"""Service d'écriture au ledger — le seul endroit du code qui insère dans `ledger_entries`.

Deux garanties y sont centralisées :

1. **Double entrée équilibrée** (Architecture §2) — `assert_balanced` est appelé avant toute
   persistance, pas seulement dans les tests.
2. **Idempotence** (Architecture §4) — une clé déjà vue renvoie la transaction d'origine sans
   rejouer l'opération. La contrainte `UNIQUE` sur `idempotency_key` fait office de garde-fou
   contre deux requêtes concurrentes portant la même clé.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from payfund_app.modules.wallet.domain.entities import (
    AccountStatus,
    AccountType,
    Direction,
    PostingLine,
    TransactionStatus,
    UnbalancedLedgerError,
    assert_balanced,
)
from payfund_app.modules.wallet.domain.errors import (
    AccountNotActive,
    AccountNotFound,
    CurrencyMismatch,
    InsufficientBalance,
)
from payfund_app.modules.wallet.domain.money import Money
from payfund_app.modules.wallet.infra.models import Account, Transaction
from payfund_app.modules.wallet.infra.repositories import (
    AccountRepository,
    LedgerRepository,
    TransactionRepository,
)


class LedgerService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.ledger = LedgerRepository(session)
        self.transactions = TransactionRepository(session)

    # --- Contrôles préalables ------------------------------------------------

    def _load_debitable(self, account_id: uuid.UUID) -> Account:
        account = self.accounts.get_for_update(account_id)
        if account is None:
            raise AccountNotFound()
        if account.status != str(AccountStatus.ACTIVE):
            # DiddiFreeID §4 : sur `user.suspended`, « geler les transactions sortantes ».
            raise AccountNotActive()
        return account

    def _load_creditable(self, account_id: uuid.UUID) -> Account:
        account = self.accounts.get(account_id)
        if account is None:
            raise AccountNotFound()
        if account.status == str(AccountStatus.CLOSED):
            raise AccountNotActive("Compte clôturé : impossible de le créditer.")
        return account

    def _autorise_solde_negatif(self, account: Account) -> bool:
        """Comptes de contrepartie autorisés à être négatifs : suspense d'opérateur et position
        de change.

        C'est mécanique dans l'exemple du §2 : un dépôt de 5 000 crédite l'utilisateur et débite
        le compte « Mobile Money suspense », qui part de zéro et se retrouve donc à −5 000 en
        attendant le reversement de l'opérateur. Même chose pour une position de change pendant
        une conversion.

        Tout autre compte — utilisateur, marchand, pool de campagne — reste soumis au contrôle de
        solde : un pool ne peut pas décaisser plus qu'il n'a collecté.
        """
        return bool(account.allows_negative_balance)

    def ensure_sufficient(self, account_id: uuid.UUID, montant: Money) -> None:
        solde = self.accounts.balance(account_id)
        if not solde.couvre(montant):
            raise InsufficientBalance(
                details={"balance": solde.amount, "requested": montant.amount}
            )

    # --- Écriture ------------------------------------------------------------

    def post(
        self,
        *,
        type_: str,
        lines: list[PostingLine],
        idempotency_key: str,
        origin_module: str | None = None,
        status: TransactionStatus = TransactionStatus.COMPLETED,
    ) -> tuple[Transaction, bool]:
        """Persiste une opération complète. Retourne `(transaction, rejouee)`.

        `rejouee=True` signifie que la clé d'idempotence était déjà connue : rien n'a été écrit,
        la transaction d'origine est renvoyée telle quelle (Contrat API §0).
        """
        existing = self.transactions.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing, True

        assert_balanced(lines)

        completed_at = (
            datetime.now(timezone.utc) if status is TransactionStatus.COMPLETED else None
        )
        try:
            with self.session.begin_nested():
                transaction = self.transactions.create(
                    type_=type_,
                    status=str(status),
                    origin_module=origin_module,
                    idempotency_key=idempotency_key,
                    completed_at=completed_at,
                )
                for line in lines:
                    self.ledger.add_entry(
                        account_id=line.account_id,  # type: ignore[arg-type]
                        transaction_id=transaction.id,
                        direction=line.direction,
                        money=line.money,
                        reference=line.reference,
                    )
                self.session.flush()
        except IntegrityError:
            # Course entre deux requêtes portant la même clé : la perdante relit le résultat
            # de la gagnante au lieu de rejouer l'opération.
            concurrent = self.transactions.get_by_idempotency_key(idempotency_key)
            if concurrent is None:
                raise
            return concurrent, True

        return transaction, False

    def post_sur_transaction(
        self, transaction: Transaction, *, lines: list[PostingLine]
    ) -> None:
        """Passe les écritures d'une transaction déjà créée.

        Sert au dépôt confirmé : l'en-tête existe depuis l'initiation, les écritures n'arrivent
        qu'au retour de l'opérateur. L'invariant de somme nulle s'applique comme partout.
        """
        assert_balanced(lines)
        for line in lines:
            self.ledger.add_entry(
                account_id=line.account_id,  # type: ignore[arg-type]
                transaction_id=transaction.id,
                direction=line.direction,
                money=line.money,
                reference=line.reference,
            )
        self.session.flush()

    def contre_passer(self, transaction: Transaction, *, reference: str) -> Transaction:
        """Annule une transaction déjà écrite par une écriture inverse (§2).

        Les écritures d'origine ne sont ni modifiées ni supprimées : on en ajoute de nouvelles,
        de sens opposé, sous une transaction distincte qui pointe vers l'originale. L'historique
        garde ainsi la trace des deux mouvements.
        """
        originales = self.ledger.entries_of(transaction.id)
        if not originales:
            raise UnbalancedLedgerError(
                "Contre-passation impossible : la transaction ne porte aucune écriture."
            )

        contre_passation = self.transactions.create(
            type_=transaction.type,
            status=str(TransactionStatus.COMPLETED),
            origin_module=transaction.origin_module,
            idempotency_key=f"reversal:{transaction.id}",
            completed_at=datetime.now(timezone.utc),
            account_id=transaction.account_id,
            money=(
                Money.from_db(transaction.amount, transaction.currency or "XOF")
                if transaction.amount is not None
                else None
            ),
            reverses_transaction_id=transaction.id,
        )

        inverses = [
            PostingLine(
                entry.account_id,
                Direction.CREDIT
                if entry.direction == str(Direction.DEBIT)
                else Direction.DEBIT,
                Money.from_db(entry.amount, entry.currency),
                reference,
            )
            for entry in originales
        ]
        self.post_sur_transaction(contre_passation, lines=inverses)
        return contre_passation

    def transfer(
        self,
        *,
        source_account_id: uuid.UUID,
        destination_account_id: uuid.UUID,
        montant: Money,
        type_: str,
        reference: str,
        idempotency_key: str,
        origin_module: str | None = None,
        status: TransactionStatus = TransactionStatus.COMPLETED,
    ) -> tuple[Transaction, bool]:
        """Débit d'un compte + crédit d'un autre, en une transaction ledger à somme nulle."""
        existing = self.transactions.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing, True

        source = self._load_debitable(source_account_id)
        destination = self._load_creditable(destination_account_id)

        if source.currency != destination.currency or source.currency != montant.currency:
            # Un transfert direct ne convertit jamais : il faut passer par `CurrencyExchange`,
            # qui produit deux transactions et trace le taux appliqué.
            raise CurrencyMismatch(
                details={
                    "source": source.currency,
                    "destination": destination.currency,
                    "amount": montant.currency,
                }
            )

        if not self._autorise_solde_negatif(source):
            self.ensure_sufficient(source.id, montant)

        return self.post(
            type_=type_,
            origin_module=origin_module,
            idempotency_key=idempotency_key,
            status=status,
            lines=[
                PostingLine(source.id, Direction.DEBIT, montant, reference),
                PostingLine(destination.id, Direction.CREDIT, montant, reference),
            ],
        )
