"""Implémentation provisoire du `ScoringPort`.

Taux unique, réglable par `DEFAULT_INTEREST_RATE`. Sa valeur par défaut (6,5 %) est celle de
l'exemple du contrat API §2, pour que la simulation documentée y corresponde exactement.

À remplacer par un client du module de scoring IA quand son interface sera arrêtée. Rien d'autre
que ce fichier ne devra changer.
"""

from decimal import Decimal
from uuid import UUID

from payfund_app.core.config import get_settings


class TauxFixeScoring:
    def __init__(self, taux: Decimal | None = None) -> None:
        self.taux = taux if taux is not None else get_settings().default_interest_rate

    def taux_pour(self, user_id: UUID) -> Decimal:
        return self.taux

    def score_de(self, user_id: UUID) -> int | None:
        # Aucun Diddi-Score disponible tant que le module de scoring n'expose rien.
        return None


def get_scoring() -> TauxFixeScoring:
    return TauxFixeScoring()
