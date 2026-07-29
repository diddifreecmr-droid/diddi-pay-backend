"""`Money` — la convention « unité entière XOF, jamais de centimes » (Contrat §0)."""

from decimal import Decimal

import pytest

from payfund_app.modules.wallet.domain.money import Balance, InvalidAmount, Money


def test_montant_entier_accepte():
    assert Money(5000).amount == 5000
    assert Money(0).currency == "XOF"


@pytest.mark.parametrize("valeur", [1500.50, Decimal("1500.50"), "1500"])
def test_refuse_tout_ce_qui_n_est_pas_un_entier(valeur):
    with pytest.raises(InvalidAmount):
        Money(valeur)


def test_refuse_montant_negatif():
    with pytest.raises(InvalidAmount):
        Money(-1)


def test_refuse_devise_invalide():
    with pytest.raises(InvalidAmount):
        Money(100, "EUROS")


def test_from_db_rejette_une_partie_decimale():
    # Une valeur fractionnaire en base signale une corruption : elle ne doit pas passer
    # silencieusement à l'arrondi.
    with pytest.raises(InvalidAmount):
        Money.from_db(Decimal("1500.50"))


def test_from_db_accepte_le_numeric_a_deux_decimales_nulles():
    assert Money.from_db(Decimal("5000.00")).amount == 5000


def test_addition_et_soustraction():
    assert (Money(1000) + Money(500)).amount == 1500
    assert (Money(1000) - Money(400)).amount == 600


def test_devises_incompatibles():
    with pytest.raises(InvalidAmount):
        Money(1000) + Money(1000, "EUR")


# --- Balance : un solde, contrairement à un montant, peut être négatif -------


def test_solde_negatif_autorise():
    """Le compte suspense d'une passerelle est négatif entre le dépôt et le reversement (§2)."""
    assert Balance(-5000).amount == -5000


def test_solde_couvre_un_montant():
    assert Balance(5000).couvre(Money(5000)) is True
    assert Balance(5000).couvre(Money(5001)) is False
    assert Balance(-1).couvre(Money(1)) is False


def test_solde_ne_compare_pas_des_devises_differentes():
    with pytest.raises(InvalidAmount):
        Balance(5000, "XOF").couvre(Money(10, "EUR"))


def test_solde_fractionnaire_en_base_est_refuse():
    with pytest.raises(InvalidAmount):
        Balance.from_db(Decimal("-1500.50"))


def test_solde_from_db_conserve_le_signe():
    assert Balance.from_db(Decimal("-5000.00")).amount == -5000
