"""Représentation des montants en unité mineure et arithmétique de change."""

from decimal import Decimal

import pytest

from payfund_app.modules.wallet.domain.money import (
    Balance,
    InvalidAmount,
    Money,
    UnknownCurrency,
)


def test_le_xof_est_inchange():
    """Exposant 0 : l'unité mineure du franc CFA est le franc. Rien ne bouge pour l'existant."""
    montant = Money(5000, "XOF")
    assert montant.amount == 5000
    assert montant.major_amount == Decimal(5000)
    assert Money.from_major(5000, "XOF").amount == 5000


def test_une_devise_a_centimes_est_stockee_en_centimes():
    montant = Money.from_major("12.50", "EUR")
    assert montant.amount == 1250
    assert montant.major_amount == Decimal("12.50")
    assert str(montant) == "12.50 EUR"


def test_precision_superieure_a_la_devise_refusee():
    """12,505 € n'existe pas ; l'arrondir en silence ferait disparaître de l'argent."""
    with pytest.raises(InvalidAmount, match="décimale"):
        Money.from_major("12.505", "EUR")


def test_le_xof_n_accepte_aucune_decimale():
    with pytest.raises(InvalidAmount):
        Money.from_major("5000.50", "XOF")


def test_devise_inconnue_refusee():
    with pytest.raises(UnknownCurrency):
        Money(1000, "ZZZ")


def test_conversion_applique_le_taux_et_arrondit():
    # 10 000 XOF à 0,001524 EUR/XOF → 15,24 EUR
    resultat = Money(10_000, "XOF").convertir("EUR", Decimal("0.001524"))
    assert resultat.currency == "EUR"
    assert resultat.amount == 1524
    assert resultat.major_amount == Decimal("15.24")


def test_conversion_vers_une_devise_sans_decimale():
    # 15,24 EUR à 656,00 XOF/EUR → 9 997 XOF (arrondi au franc)
    resultat = Money.from_major("15.24", "EUR").convertir("XOF", Decimal("656"))
    assert resultat.amount == 9997


def test_taux_negatif_ou_nul_refuse():
    for taux in (Decimal("0"), Decimal("-1")):
        with pytest.raises(InvalidAmount):
            Money(1000).convertir("EUR", taux)


def test_solde_en_devise_a_centimes():
    assert Balance(-1250, "EUR").major_amount == Decimal("-12.50")


def test_solde_ne_compare_pas_deux_devises():
    with pytest.raises(InvalidAmount):
        Balance(5000, "XOF").couvre(Money(1000, "EUR"))
