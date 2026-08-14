"""Implémentation du `WalletServicePort` par le module `wallet`.

Architecture §5 : aujourd'hui, `fund/infra` consomme ce port par **appel de fonction direct**
(in-process), ce qui permet de partager la transaction DB quand deux écritures doivent être
atomiques ensemble. Le jour où `fund` devient un service séparé, seule l'implémentation côté
`fund/infra` change pour un client HTTP — cette classe-ci reste le point d'entrée.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from payfund_app.modules.wallet.application.ledger import LedgerService
from payfund_app.modules.wallet.domain.entities import AccountType
from payfund_app.modules.wallet.domain.errors import AccountNotFound
from payfund_app.modules.wallet.domain.money import Money
from payfund_app.modules.wallet.infra.repositories import AccountRepository


class WalletService:
    """Adaptateur in-process. La session passée est celle de la requête en cours."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.ledger = LedgerService(session)

    def debiter(
        self,
        compte_id: uuid.UUID,
        contrepartie_compte_id: uuid.UUID,
        montant: int,
        reference: str,
        idempotency_key: str,
        type_transaction: str,
        origin_module: str | None = None,
    ) -> uuid.UUID:
        transaction, _ = self.ledger.transfer(
            source_account_id=compte_id,
            destination_account_id=contrepartie_compte_id,
            montant=Money(montant),
            type_=type_transaction,
            reference=reference,
            idempotency_key=idempotency_key,
            origin_module=origin_module,
        )
        transaction.amount = Money(montant).to_db()
        transaction.currency = Money(montant).currency
        return transaction.id

    def crediter(
        self,
        compte_id: uuid.UUID,
        contrepartie_compte_id: uuid.UUID,
        montant: int,
        reference: str,
        idempotency_key: str,
        type_transaction: str,
        origin_module: str | None = None,
    ) -> uuid.UUID:
        transaction, _ = self.ledger.transfer(
            source_account_id=contrepartie_compte_id,
            destination_account_id=compte_id,
            montant=Money(montant),
            type_=type_transaction,
            reference=reference,
            idempotency_key=idempotency_key,
            origin_module=origin_module,
        )
        transaction.amount = Money(montant).to_db()
        transaction.currency = Money(montant).currency
        return transaction.id

    def ouvrir_compte_technique(self, reference: str) -> uuid.UUID:
        account = self.accounts.create(
            user_id=None, account_type=AccountType.TECHNICAL, reference=reference
        )
        return account.id

    def solde(self, compte_id: uuid.UUID) -> int:
        return self.accounts.balance(compte_id).amount

    def compte_de_utilisateur(self, user_id: uuid.UUID) -> uuid.UUID:
        account = self.accounts.get_by_user(user_id)
        if account is None:
            account = self.accounts.create(user_id=user_id, account_type=AccountType.USER)
        return account.id
