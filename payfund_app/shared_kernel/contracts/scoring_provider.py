"""ScoringPort — le taux d'intérêt appliqué à un emprunteur.

Le contrat API §2 indique que `interest_rate_applied` « dépend du Diddi-Score de l'utilisateur »,
et l'architecture §6 précise que le scoring crédit « vit dans un module/service à part » dont
« ni `wallet` ni `fund` n'embarquent de logique en dur ».

Aucune interface de ce module n'étant documentée à ce jour, `fund` s'adresse à ce port. Il est
aujourd'hui satisfait par une implémentation à taux fixe configurable ; brancher le vrai module
de scoring ne changera que l'adaptateur, jamais `fund/application` ni `fund/domain`.
"""

from decimal import Decimal
from typing import Protocol
from uuid import UUID


class ScoringPort(Protocol):
    def taux_pour(self, user_id: UUID) -> Decimal:
        """Taux d'intérêt à appliquer à cet emprunteur, en pourcentage (ex. `Decimal("6.5")`)."""
        ...

    def score_de(self, user_id: UUID) -> int | None:
        """Diddi-Score courant, conservé dans `fund.loans.diddi_score_at_grant` pour la
        traçabilité de la décision. `None` tant que le module de scoring n'existe pas."""
        ...
