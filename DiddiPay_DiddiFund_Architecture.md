# DiddiPay / DiddiFund — Architecture applicative & Conception

**Stack retenue :** Python / FastAPI · PostgreSQL
**Périmètre historique :** APP BASE hébergeant `wallet` et `fund`, consommant DiddiFreeID.
**Statut :** architecture wallet historique conservée pour la compatibilité et l'audit
**Document miroir :** `DiddiPay_DiddiFund_Contrat_API.md`
**Dépendances externes** : DiddiFreeID (identité, voir `DiddiFreeID_Contrat_API.md`)

> **Pivot MVP 2026 :** DiddiPay est désormais l'orchestrateur `PaymentIntent`, pas le nom du wallet.
> Le nouveau contrat de référence est `DiddiPay_Contrat_API.md`. Le module `wallet` décrit dans ce
> document reste une capacité legacy du monolithe. Les nouvelles intégrations DiddiGo, DiddiFund et
> futurs modules suivent `DiddiPay_Backend_Integration_Brief.md`.

---

## 0. Rôle dans l'écosystème et contraintes de charge

### Architecture cible après pivot

```text
Frontend -> module métier -> DiddiPay PaymentIntent -> adaptateur PSP -> Paystack
                         <- événements signés + reconciliation <- webhook PSP
```

- le module métier possède la course, l'investissement, le prêt ou la commande ;
- DiddiPay possède l'intention, les tentatives, l'idempotence et le statut de paiement normalisé ;
- Paystack est le rail externe du MVP et peut être remplacé par un adaptateur direct ;
- le sous-ledger `payments.*` explique captures, frais, remboursements et settlements ;
- `wallet.*` reste isolé et pourra devenir un moyen de paiement `wallet` derrière le même contrat.

Cette cible coexiste volontairement avec l'architecture wallet ci-dessous. Aucune table financière
legacy n'est supprimée pendant le MVP ; la stratégie détaillée est dans
`DiddiPay_Migration_Runbook.md`.

Le cahier des charges positionne DiddiPay comme "le moteur de règlement à tous les autres modules" — un
rôle transverse comparable à DiddiFreeID, mais avec un profil de charge inverse : ici, ce qui compte
n'est pas le volume de lecture, c'est **la rigueur de chaque écriture**. Une transaction mal comptabilisée
a un impact financier direct et immédiat, contrairement à un profil utilisateur en cache légèrement
périmé côté DiddiFreeID.

**Volume cible : ~10 000 transactions/jour**, soit une moyenne de 7/minute — largement dans la capacité
d'une base PostgreSQL correctement indexée, même avec des pics x10-x20 aux heures de forte activité. Ce
n'est pas un problème d'échelle, c'est un problème d'intégrité transactionnelle et d'auditabilité.

### Pourquoi pas de CQRS ici, contrairement à DiddiFreeID

| | DiddiFreeID | Wallet/Fund |
|---|---|---|
| Sollicité par | Tous les modules, à chaque requête | Uniquement lors d'opérations financières explicites |
| Contournable par vérification locale | Oui (JWT) | Non — chaque paiement doit toucher le ledger central, pas de "cache" possible sur un solde |
| Ce qui prime | Disponibilité en lecture massive | Cohérence stricte à l'écriture (ACID) |
| Conclusion | CQRS léger justifié | **Pas de CQRS** — architecture transactionnelle classique, une transaction DB = une opération métier |

Le solde d'un compte n'est jamais "à jour à quelques minutes près" comme peut l'être un profil utilisateur
— toute lecture de solde doit refléter l'état réellement commis en base. Introduire une séparation
lecture/écriture ici ajouterait un risque (lire un solde périmé avant d'autoriser un paiement) sans aucun
bénéfice de charge, puisque le volume ne le justifie pas. C'est un exemple direct du principe qu'on a
appliqué partout dans ce projet : **la complexité architecturale se justifie par un besoin mesuré, pas par
anticipation.**

---

## 1. Architecture applicative — organisation en modules verticaux

Même gabarit que DiddiGo et DiddiFreeID : monolithe modulaire, 4 couches, extraction possible plus tard.

```
payfund_app/
├── core/                          # config, DB engine, sécurité
│   ├── config.py
│   └── database.py
│
├── shared_kernel/
│   ├── contracts/
│   │   ├── identity_provider.py   # IdentityVerifierPort — consomme DiddiFreeID (JWT local, voir son doc)
│   │   └── wallet_provider.py     # WalletServicePort — utilisé par `fund`, implémenté par `wallet`
│   └── events/                    # bus interne : payment.completed, loan.disbursed, campaign.closed...
│
├── modules/
│   │
│   ├── wallet/                    # ── Module DiddiPay ──
│   │   ├── presentation/          # routers : dépôt, retrait, transfert P2P, paiement marchand, historique
│   │   ├── application/           # use cases : DeposerFonds, RetirerFonds, TransfererP2P, PayerMarchand,
│   │   │                          # ConvertirDevise, ReconcilierComptesQuotidien (job planifié)
│   │   ├── domain/                # entités : Account, LedgerEntry, Transaction · value object : Money
│   │   └── infra/                 # modèles SQLAlchemy (schéma `wallet`), adaptateurs Orange Money/MTN/
│   │                               # Wave/Moov/cartes, implémentation du WalletServicePort
│   │
│   └── fund/                      # ── Module DiddiFund ──
│       ├── presentation/          # routers : campagnes, investissement, simulation de prêt, échéancier
│       ├── application/           # use cases : CreerCampagne, ValiderCampagne, CloturerCampagne,
│       │                          # DecaisserPret, EncaisserRemboursement, EnvoyerRelances (job planifié)
│       ├── domain/                # entités : Campaign, Loan, RepaymentSchedule, Investment
│       └── infra/                 # modèles SQLAlchemy (schéma `fund`), client du WalletServicePort
│                                   # (implémentation "in-process" pour l'instant, voir section 5)
│
└── main.py
```

**Règle de frontière inter-module, identique à DiddiGo** : `fund` ne fait jamais de requête SQL directe
sur les tables `wallet.*`. Il passe systématiquement par `WalletServicePort` — deux méthodes suffisent au
départ : `decaisser(compte_id, montant, reference) -> TransactionId` et
`encaisser(compte_id, montant, reference) -> TransactionId`. C'est cette frontière, pas la technologie,
qui permettra d'extraire `fund` en service séparé le jour venu sans toucher à `wallet`.

---

## 2. Le ledger double entrée — cœur du module `wallet`

Chaque mouvement financier génère **deux écritures**, jamais une seule : un débit sur un compte, un
crédit sur un autre, dont la somme est toujours nulle. C'est ce qui rend le système auditable et détecte
mécaniquement toute incohérence (si la somme d'un batch d'écritures n'est pas nulle, quelque chose est
cassé — pas besoin d'attendre la réconciliation quotidienne pour le savoir).

Exemple : un dépôt Mobile Money de 5000 XOF crée deux écritures — un crédit de 5000 sur le compte
utilisateur, un débit de 5000 sur un compte technique "Mobile Money suspense" représentant les fonds
encaissés côté opérateur mais pas encore réconciliés. La réconciliation quotidienne vérifie que ce compte
suspense retombe à zéro une fois les relevés Orange Money/MTN traités.

**Aucune écriture n'est jamais modifiée ou supprimée** — une correction se fait par une écriture inverse
(contre-passation), jamais par un `UPDATE`/`DELETE`. C'est non négociable pour un système financier
auditable.

---

## 3. Schéma de données

```sql
CREATE SCHEMA IF NOT EXISTS wallet;
CREATE SCHEMA IF NOT EXISTS fund;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

### 3.1 Module `wallet`

```sql
CREATE TABLE wallet.accounts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL UNIQUE,          -- référence logique vers identity.users(id) (DiddiFreeID)
    account_type    VARCHAR(20) NOT NULL DEFAULT 'user',  -- user | merchant | technical (comptes suspense)
    currency        CHAR(3) NOT NULL DEFAULT 'XOF',
    status          VARCHAR(20) NOT NULL DEFAULT 'active', -- active | frozen | closed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Le solde n'est PAS stocké directement : il se calcule (ou se matérialise en vue) à partir des écritures.
-- Optionnel : colonne de solde caché mise à jour dans la même transaction que chaque écriture, pour éviter
-- de recalculer une somme sur tout l'historique à chaque lecture — à activer si mesuré nécessaire.
CREATE TABLE wallet.ledger_entries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID NOT NULL REFERENCES wallet.accounts(id),
    transaction_id  UUID NOT NULL,                 -- regroupe les 2 (ou +) écritures d'une même opération
    direction       VARCHAR(6) NOT NULL CHECK (direction IN ('debit','credit')),
    amount          NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    currency        CHAR(3) NOT NULL,
    reference       VARCHAR(100),                  -- ex. "fund:loan:disbursement:<loan_id>"
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_account ON wallet.ledger_entries(account_id, created_at);
CREATE INDEX idx_ledger_transaction ON wallet.ledger_entries(transaction_id);

CREATE TABLE wallet.transactions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type                VARCHAR(30) NOT NULL,      -- deposit | withdrawal | p2p_transfer | merchant_payment | fund_disbursement | fund_repayment
    status              VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending | completed | failed | reversed
    origin_module       VARCHAR(30),               -- 'fund', 'ride', 'shop'... — pour le filtrage demandé par l'UX du cahier des charges
    idempotency_key     VARCHAR(100) UNIQUE,        -- voir section 4
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

-- Compte technique de suspense pour chaque intégration externe (un par opérateur)
CREATE TABLE wallet.gateway_accounts (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider    VARCHAR(30) NOT NULL UNIQUE,       -- orange_money | mtn_momo | wave | moov | card_gateway
    account_id  UUID NOT NULL REFERENCES wallet.accounts(id)
);
```

### 3.2 Module `fund`

```sql
CREATE TABLE fund.campaigns (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_user_id   UUID NOT NULL,                 -- référence logique vers identity.users(id)
    title           VARCHAR(200) NOT NULL,
    goal_amount     NUMERIC(14,2) NOT NULL,
    raised_amount   NUMERIC(14,2) NOT NULL DEFAULT 0,   -- matérialisé, recalculable depuis `investments`
    currency        CHAR(3) NOT NULL DEFAULT 'XOF',
    status          VARCHAR(20) NOT NULL DEFAULT 'draft', -- draft | active | closed | cancelled
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ
);

CREATE TABLE fund.investments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id     UUID NOT NULL REFERENCES fund.campaigns(id),
    investor_user_id UUID NOT NULL,
    amount          NUMERIC(14,2) NOT NULL,
    wallet_transaction_id UUID NOT NULL,           -- trace vers wallet.transactions, via WalletServicePort
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE fund.loans (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    borrower_user_id    UUID NOT NULL,
    principal_amount    NUMERIC(14,2) NOT NULL,
    diddi_score_at_grant SMALLINT,                 -- traçabilité de la décision (cf. cahier des charges IA)
    status              VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending | disbursed | repaying | closed | defaulted
    disbursed_at        TIMESTAMPTZ,
    wallet_transaction_id UUID
);

CREATE TABLE fund.repayment_schedule (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    loan_id         UUID NOT NULL REFERENCES fund.loans(id),
    installment_no  SMALLINT NOT NULL,
    due_date        DATE NOT NULL,
    amount_due      NUMERIC(14,2) NOT NULL,
    amount_paid     NUMERIC(14,2) NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'due',  -- due | paid | late | defaulted
    paid_at         TIMESTAMPTZ
);

-- Reprend le principe ride_status_history / user_status_history déjà établi dans l'écosystème
CREATE TABLE fund.loan_status_history (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    loan_id     UUID NOT NULL REFERENCES fund.loans(id),
    from_status VARCHAR(20),
    to_status   VARCHAR(20) NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB
);
```

---

## 4. Idempotence — critique pour les intégrations Mobile Money

Les passerelles Orange Money/MTN/Wave/Moov peuvent renvoyer un timeout côté réseau alors que l'opération
a en réalité réussi côté opérateur. Sans protection, un retry créerait un double crédit. D'où la colonne
`idempotency_key` sur `wallet.transactions` : chaque appel entrant (dépôt, retrait, décaissement DiddiFund
vers un compte) porte une clé unique fournie par l'appelant (le frontend, ou `fund/application` pour ses
propres appels internes). Si une transaction avec la même clé existe déjà, on renvoie son résultat déjà
connu au lieu de la rejouer.

Cette règle s'applique aussi à l'appel `fund → wallet` via `WalletServicePort` : `DecaisserPret` doit
transmettre une clé d'idempotence stable (ex. dérivée de `loan.id`), pour qu'un retry côté `fund` (ex.
après un crash serveur juste après l'appel) ne décaisse pas deux fois le même prêt.

---

## 5. Intégration avec DiddiFreeID et le reste de l'écosystème

- **Authentification** : `presentation/` de `wallet` et `fund` vérifient le JWT localement via JWKS, comme
  tous les autres modules (voir `DiddiFreeID_Contrat_API.md`).
- **Création automatique de compte wallet** : `wallet` s'abonne à l'événement `user.registered` publié par
  DiddiFreeID et crée le compte correspondant — l'utilisateur n'a jamais besoin d'un appel explicite
  "créer mon wallet".
- **Gel de compte sur suspension** : `wallet` s'abonne à `user.suspended` et passe le compte associé en
  `status = frozen` immédiatement, sans attendre l'expiration du token en cours.
- **`WalletServicePort` — implémentation actuelle vs future** : aujourd'hui, `fund/infra` implémente ce
  port par un **appel de fonction direct** vers `wallet/application` (in-process, transaction DB partagée
  possible si les deux écritures doivent être atomiques ensemble). Le jour où `fund` est extrait en
  service séparé, seule cette implémentation change pour un appel HTTP — `fund/application` et
  `fund/domain` restent inchangés, exactement le principe déjà appliqué pour `payment` chez DiddiGo.

---

## 6. Sécurité et conformité

- **Isolation du périmètre `wallet`** : scope PCI DSS réduit à ce module — `fund` ne stocke ni ne
  manipule jamais de données de carte, uniquement des montants et des références de transaction.
- **Chiffrement** : AES-256 au repos, TLS 1.3 en transit (déjà spécifié au cahier des charges).
- **Tokenisation** des numéros de compte Mobile Money / cartes côté `wallet/infra` — jamais stockés en
  clair, jamais transmis à `fund`.
- **KYC/AML** : le KYC "identité" (qui êtes-vous) reste porté par DiddiFreeID ; le KYC "financier"
  (limites de transaction, déclarations AML) est propre à `wallet` et **ne doit pas** être mélangé au
  profil DiddiFreeID générique — un utilisateur peut être identifié sans être encore autorisé à des
  opérations financières au-delà d'un seuil.
- **Scoring anti-fraude et scoring crédit (IA)** : consomment en lecture les données de `wallet` et
  `fund`, mais vivent dans un module/service à part (cohérent avec la séparation "Intelligence
  Artificielle & Data" du cahier des charges) — ni `wallet` ni `fund` n'embarquent de logique de
  scoring en dur.

---

## 7. Prochaines étapes suggérées

1. Poser le squelette `payfund_app/modules/{wallet,fund}/...` et le `WalletServicePort` en premier —
   c'est la frontière la plus structurante du projet.
2. Implémenter `wallet` seul seul d'abord (compte, dépôt, retrait, ledger) avec un provider Mobile Money
   simulé (stub) avant de brancher Orange Money/MTN réels.
3. Écrire les tests du ledger en priorité — toute somme d'écritures d'une transaction doit être nulle,
   à vérifier systématiquement (test automatique, pas seulement en réconciliation quotidienne).
4. Implémenter `fund` en s'appuyant sur `WalletServicePort` in-process.
5. Détailler le job `ReconcilierComptesQuotidien` (comparaison relevés opérateurs vs comptes suspense).
