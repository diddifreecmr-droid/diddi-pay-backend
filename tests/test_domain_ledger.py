"""Invariant du ledger double entrée.

Architecture §7, étape 3 : « Écrire les tests du ledger en priorité — toute somme d'écritures
d'une transaction doit être nulle, à vérifier systématiquement ».
"""

import uuid

import pytest

from payfund_app.modules.wallet.domain.entities import (
    Direction,
    PostingLine,
    UnbalancedLedgerError,
    assert_balanced,
)
from payfund_app.modules.wallet.domain.money import Money

A = uuid.uuid4()
B = uuid.uuid4()
C = uuid.uuid4()


def test_deux_ecritures_opposees_sont_equilibrees():
    assert_balanced(
        [
            PostingLine(A, Direction.DEBIT, Money(5000)),
            PostingLine(B, Direction.CREDIT, Money(5000)),
        ]
    )


def test_ecriture_unique_refusee():
    with pytest.raises(UnbalancedLedgerError):
        assert_balanced([PostingLine(A, Direction.CREDIT, Money(5000))])


def test_montants_differents_refuses():
    with pytest.raises(UnbalancedLedgerError, match="déséquilibrées"):
        assert_balanced(
            [
                PostingLine(A, Direction.DEBIT, Money(5000)),
                PostingLine(B, Direction.CREDIT, Money(4000)),
            ]
        )


def test_deux_debits_sans_credit_refuses():
    with pytest.raises(UnbalancedLedgerError):
        assert_balanced(
            [
                PostingLine(A, Direction.DEBIT, Money(1000)),
                PostingLine(B, Direction.DEBIT, Money(1000)),
            ]
        )


def test_repartition_sur_plusieurs_comptes_equilibree():
    # Un débit peut se répartir sur plusieurs crédits (ex. commission marchand) tant que la
    # somme reste nulle.
    assert_balanced(
        [
            PostingLine(A, Direction.DEBIT, Money(1000)),
            PostingLine(B, Direction.CREDIT, Money(950)),
            PostingLine(C, Direction.CREDIT, Money(50)),
        ]
    )


def test_montant_nul_refuse():
    with pytest.raises(UnbalancedLedgerError, match="strictement positif"):
        assert_balanced(
            [
                PostingLine(A, Direction.DEBIT, Money(0)),
                PostingLine(B, Direction.CREDIT, Money(0)),
            ]
        )


def test_une_transaction_ne_mele_pas_deux_devises():
    """Une conversion passe par deux transactions reliées, pas par une transaction bancale."""
    with pytest.raises(UnbalancedLedgerError, match="plusieurs devises"):
        assert_balanced(
            [
                PostingLine(A, Direction.DEBIT, Money(1000, "XOF")),
                PostingLine(B, Direction.CREDIT, Money(1000, "EUR")),
            ]
        )
