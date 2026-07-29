"""WalletServicePort — la frontière `fund` → `wallet`.

Architecture §1 : « `fund` ne fait jamais de requête SQL directe sur les tables `wallet.*`. Il
passe systématiquement par `WalletServicePort` ». C'est cette frontière qui permettra d'extraire
`fund` en service séparé sans toucher à `wallet` (§5).

Deux écarts assumés par rapport à l'architecture, tous deux imposés par le ledger double entrée :

1. **Nommage.** L'architecture proposait `decaisser()` / `encaisser()`, mais le contrat API les
   employait de façon contradictoire : `encaisser()` pour débiter l'investisseur (§2 invest) et
   `decaisser()` pour débiter l'emprunteur (§2 repay). Renommés `debiter()` / `crediter()`, qui
   décrivent l'effet sur le compte nommé, sans point de vue implicite.

2. **Contrepartie explicite.** Le §2 impose que « chaque mouvement financier génère deux
   écritures, jamais une seule ». Un `debiter(compte, montant)` à un seul argument de compte est
   donc impossible à honorer : il manque le compte crédité en face. Les deux méthodes prennent
   par conséquent un `contrepartie_compte_id`. Elles décrivent le même mouvement à somme nulle,
   vu depuis l'un ou l'autre bout.
"""

from typing import Protocol
from uuid import UUID


class WalletServicePort(Protocol):
    def debiter(
        self,
        compte_id: UUID,
        contrepartie_compte_id: UUID,
        montant: int,
        reference: str,
        idempotency_key: str,
        type_transaction: str,
        origin_module: str | None = None,
    ) -> UUID:
        """Retire `montant` de `compte_id` et le porte sur `contrepartie_compte_id`.

        Retourne l'id de la transaction wallet créée.

        `idempotency_key` est obligatoire y compris pour les appels internes : l'architecture §4
        impose qu'un retry côté `fund` (crash serveur juste après l'appel) ne rejoue pas
        l'opération. La clé doit être **stable et dérivée du métier**
        (ex. `fund:loan:disbursement:<loan_id>`), jamais régénérée à chaque tentative.

        Lève `InsufficientBalance`, `AccountNotFound` ou `AccountNotActive`.
        """
        ...

    def crediter(
        self,
        compte_id: UUID,
        contrepartie_compte_id: UUID,
        montant: int,
        reference: str,
        idempotency_key: str,
        type_transaction: str,
        origin_module: str | None = None,
    ) -> UUID:
        """Porte `montant` sur `compte_id`, prélevé sur `contrepartie_compte_id`.

        Strictement symétrique de `debiter` — mêmes règles d'idempotence, et le contrôle de solde
        porte alors sur la contrepartie.
        """
        ...

    def ouvrir_compte_technique(self, reference: str) -> UUID:
        """Ouvre un compte `account_type='technical'` (sans propriétaire) et retourne son id.

        Ajout consécutif à la décision « un compte wallet par campagne » : `fund` a besoin d'une
        contrepartie ledger dès la création d'une campagne, et ne peut pas insérer lui-même dans
        `wallet.accounts` sans violer la règle de frontière du §1.
        """
        ...

    def solde(self, compte_id: UUID) -> int:
        """Solde courant d'un compte, en unité entière de sa devise.

        Nécessaire à `fund` pour exposer le montant collecté réellement présent sur le compte
        d'une campagne sans lire les tables `wallet.*`.
        """
        ...

    def compte_de_utilisateur(self, user_id: UUID) -> UUID:
        """Compte wallet d'un utilisateur, à partir de son `user_id` (le `sub` du JWT).

        `fund` ne connaît que des `user_id` : c'est ce qu'il reçoit dans le token. La résolution
        vers un `account_id` doit passer par le port, sinon `fund` devrait interroger
        `wallet.accounts` directement — ce que la règle de frontière du §1 interdit.

        Lève `AccountNotFound`.
        """
        ...
