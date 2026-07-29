"""ForeignExchangeRatePort — d'où vient un taux de change.

Aucun document du projet ne spécifie de source de cotation. Le port existe donc pour que le choix
d'un fournisseur (partenaire bancaire, agrégateur, cotation BCEAO) reste un adaptateur, sur le
même principe que `WalletServicePort` et `ScoringPort`.

L'implémentation par défaut lit la table `wallet.exchange_rates`. Tant qu'aucun taux n'y est
posé, une conversion est **refusée** — jamais devinée.
"""

from decimal import Decimal
from typing import Protocol


class RateUnavailable(Exception):
    """Aucun taux connu pour cette paire de devises."""


class ForeignExchangeRatePort(Protocol):
    def taux(self, base: str, quote: str) -> Decimal:
        """Combien d'unités usuelles de `quote` vaut 1 unité usuelle de `base`.

        Lève `RateUnavailable` si la paire n'est pas cotée.
        """
        ...
