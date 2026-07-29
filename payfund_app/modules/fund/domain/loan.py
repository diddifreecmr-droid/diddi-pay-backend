"""Calcul de prêt — logique pure, sans base de données.

La formule est **déduite de l'exemple du contrat API §2**, pas inventée :

    200 000 sur 6 mois à 6,5 %  →  total_repayable = 213 000, monthly_installment = 35 500

soit 200 000 × 1,065 = 213 000, puis 213 000 ÷ 6 = 35 500. C'est un intérêt **simple appliqué au
capital** sur toute la durée, réparti en mensualités égales — et non un amortissement à intérêts
dégressifs, qui donnerait un autre résultat.

Seul point non couvert par l'exemple, dont la division tombe juste : la répartition d'un reste.
Choix retenu, signalé comme tel — toutes les mensualités sont égales et la **dernière absorbe le
reliquat**, pour que la somme de l'échéancier égale exactement `total_repayable` (un centime
d'écart sur un prêt est une anomalie comptable, pas un arrondi acceptable).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MAX_DURATION_MONTHS = 60


@dataclass(frozen=True)
class Installment:
    installment_no: int
    due_date: date
    amount_due: int


@dataclass(frozen=True)
class LoanTerms:
    principal: int
    duration_months: int
    interest_rate_applied: Decimal
    total_repayable: int
    monthly_installment: int


class InvalidLoanTerms(ValueError):
    pass


def calculer_conditions(
    *, principal: int, duration_months: int, taux: Decimal
) -> LoanTerms:
    if principal <= 0:
        raise InvalidLoanTerms("Le capital doit être strictement positif.")
    if duration_months <= 0 or duration_months > MAX_DURATION_MONTHS:
        raise InvalidLoanTerms(
            f"La durée doit être comprise entre 1 et {MAX_DURATION_MONTHS} mois."
        )
    if taux < 0:
        raise InvalidLoanTerms("Le taux ne peut pas être négatif.")

    total = (Decimal(principal) * (Decimal(1) + taux / Decimal(100))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    mensualite = (total / Decimal(duration_months)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return LoanTerms(
        principal=principal,
        duration_months=duration_months,
        interest_rate_applied=taux,
        total_repayable=int(total),
        monthly_installment=int(mensualite),
    )


def _ajouter_mois(origine: date, mois: int) -> date:
    """Même quantième le mois suivant, ramené au dernier jour si celui-ci n'existe pas.

    Un prêt décaissé un 31 janvier a sa première échéance le 28 (ou 29) février.
    """
    total = origine.month - 1 + mois
    annee = origine.year + total // 12
    mois_cible = total % 12 + 1
    jours_du_mois = [31, 29 if _bissextile(annee) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(annee, mois_cible, min(origine.day, jours_du_mois[mois_cible - 1]))


def _bissextile(annee: int) -> bool:
    return annee % 4 == 0 and (annee % 100 != 0 or annee % 400 == 0)


def construire_echeancier(terms: LoanTerms, *, depart: date) -> list[Installment]:
    """Échéancier mensuel. La dernière mensualité absorbe le reliquat d'arrondi."""
    echeances: list[Installment] = []
    cumul = 0
    for numero in range(1, terms.duration_months + 1):
        derniere = numero == terms.duration_months
        montant = (
            terms.total_repayable - cumul if derniere else terms.monthly_installment
        )
        cumul += montant
        echeances.append(
            Installment(
                installment_no=numero,
                due_date=_ajouter_mois(depart, numero),
                amount_due=montant,
            )
        )
    return echeances
