"""Calcul de prêt — formule déduite de l'exemple du contrat API §2."""

from datetime import date
from decimal import Decimal

import pytest

from payfund_app.modules.fund.domain.loan import (
    InvalidLoanTerms,
    calculer_conditions,
    construire_echeancier,
)


def test_exemple_du_contrat_est_reproduit_exactement():
    """200 000 sur 6 mois à 6,5 % → 35 500/mois, 213 000 au total (Contrat §2)."""
    terms = calculer_conditions(
        principal=200_000, duration_months=6, taux=Decimal("6.5")
    )

    assert terms.total_repayable == 213_000
    assert terms.monthly_installment == 35_500
    assert terms.interest_rate_applied == Decimal("6.5")


def test_taux_nul_rembourse_le_capital():
    terms = calculer_conditions(principal=120_000, duration_months=12, taux=Decimal("0"))
    assert terms.total_repayable == 120_000
    assert terms.monthly_installment == 10_000


@pytest.mark.parametrize(
    "principal,duree,taux",
    [(0, 6, Decimal("6.5")), (-1, 6, Decimal("6.5")), (1000, 0, Decimal("6.5")),
     (1000, 61, Decimal("6.5")), (1000, 6, Decimal("-1"))],
)
def test_conditions_invalides(principal, duree, taux):
    with pytest.raises(InvalidLoanTerms):
        calculer_conditions(principal=principal, duration_months=duree, taux=taux)


def test_echeancier_couvre_exactement_le_total():
    """Aucun franc ne doit se perdre dans les arrondis."""
    terms = calculer_conditions(principal=100_000, duration_months=7, taux=Decimal("6.5"))
    echeances = construire_echeancier(terms, depart=date(2026, 7, 20))

    assert len(echeances) == 7
    assert sum(e.amount_due for e in echeances) == terms.total_repayable


def test_derniere_echeance_absorbe_le_reliquat():
    terms = calculer_conditions(principal=100_000, duration_months=7, taux=Decimal("6.5"))
    echeances = construire_echeancier(terms, depart=date(2026, 7, 20))

    assert all(e.amount_due == terms.monthly_installment for e in echeances[:-1])
    assert echeances[-1].amount_due != 0


def test_echeances_mensuelles_au_meme_quantieme():
    terms = calculer_conditions(principal=200_000, duration_months=6, taux=Decimal("6.5"))
    echeances = construire_echeancier(terms, depart=date(2026, 7, 20))

    assert [e.due_date for e in echeances] == [
        date(2026, 8, 20),
        date(2026, 9, 20),
        date(2026, 10, 20),
        date(2026, 11, 20),
        date(2026, 12, 20),
        date(2027, 1, 20),
    ]


def test_quantieme_inexistant_est_ramene_en_fin_de_mois():
    """Un prêt décaissé le 31 janvier a sa première échéance le 28 février."""
    terms = calculer_conditions(principal=60_000, duration_months=2, taux=Decimal("0"))
    echeances = construire_echeancier(terms, depart=date(2026, 1, 31))

    assert echeances[0].due_date == date(2026, 2, 28)
    assert echeances[1].due_date == date(2026, 3, 31)
