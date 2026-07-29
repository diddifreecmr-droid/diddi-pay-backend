"""Entités et invariants du domaine `wallet`."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from payfund_app.modules.wallet.domain.money import Money


class AccountType(StrEnum):
    USER = "user"
    MERCHANT = "merchant"
    TECHNICAL = "technical"  # comptes suspense passerelle, pool de campagne...


class AccountStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class TransactionType(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    P2P_TRANSFER = "p2p_transfer"
    MERCHANT_PAYMENT = "merchant_payment"
    FUND_DISBURSEMENT = "fund_disbursement"
    FUND_REPAYMENT = "fund_repayment"
    # Absent de la liste du §3.1 : une conversion produit deux transactions de ce type, une par
    # devise, reliées par `wallet.currency_conversions`.
    CURRENCY_CONVERSION = "currency_conversion"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


@dataclass(frozen=True)
class PostingLine:
    """Une écriture à passer, avant persistance."""

    account_id: object
    direction: Direction
    money: Money
    reference: str | None = None


class UnbalancedLedgerError(Exception):
    """La somme des écritures d'une transaction n'est pas nulle."""


def assert_balanced(lines: list[PostingLine]) -> None:
    """Invariant central du ledger double entrée (Architecture §2).

    « si la somme d'un batch d'écritures n'est pas nulle, quelque chose est cassé — pas besoin
    d'attendre la réconciliation quotidienne pour le savoir ». Cette fonction est appelée avant
    **chaque** persistance d'écritures, pas seulement dans les tests.
    """
    if len(lines) < 2:
        raise UnbalancedLedgerError(
            "Une transaction doit produire au moins deux écritures (double entrée)."
        )

    currencies = {line.money.currency for line in lines}
    if len(currencies) > 1:
        # Une transaction est toujours mono-devise : des écritures dans deux unités différentes
        # ne peuvent pas sommer à zéro. Une conversion se fait en **deux** transactions reliées
        # par un compte de position de change (voir `application/exchange.py`).
        raise UnbalancedLedgerError(
            f"Une transaction ne peut pas mêler plusieurs devises : {sorted(currencies)}"
        )

    total = 0
    for line in lines:
        if not line.money.is_positive():
            raise UnbalancedLedgerError("Une écriture doit porter un montant strictement positif.")
        total += line.money.amount if line.direction is Direction.CREDIT else -line.money.amount

    if total != 0:
        raise UnbalancedLedgerError(
            f"Écritures déséquilibrées : somme = {total}, attendu 0."
        )
