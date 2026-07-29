# payfund_app — DiddiPay + DiddiFund

Monolithe modulaire Python/FastAPI + PostgreSQL implémentant les modules `wallet` (DiddiPay) et
`fund` (DiddiFund), d'après :

- `DiddiPay_DiddiFund_Architecture.md`
- `DiddiPay_DiddiFund_Contrat_API.md`
- `DiddiFreeID_Contrat_API.md` (identité, consommée en vérification locale de JWT)

## Démarrage

```bash
workon diddipay                        # CPython 3.11.9
pip install -e ".[dev]"
cp .env.example .env
```

**Avant de lancer `docker compose up`**, vérifier que les ports choisis dans `.env`
(`POSTGRES_PORT`, `REDIS_PORT`, `APP_PORT`) sont bien libres sur la machine cible — ce projet est
hébergé aux côtés d'autres piles complètes (Postgres/Redis/Flask/...) sur le même serveur, les
valeurs par défaut de `.env.example` ne sont que des suggestions :

```bash
ss -tulpn | grep -E ":(54329|61780|48213)\b"    # rien ne doit s'afficher
# ou, si d'autres projets tournent déjà en Docker :
docker ps --format "{{.Names}}: {{.Ports}}"
```

Ajuster `POSTGRES_PORT` / `REDIS_PORT` / `APP_PORT` dans `.env` en conséquence — et reporter la
même valeur de port dans `DATABASE_URL` / `REDIS_URL` / `TEST_DATABASE_URL`, qui ne font pas de
substitution automatique (voir les commentaires de `.env.example`).

```bash
docker compose up -d
alembic upgrade head
uvicorn payfund_app.main:app --reload --host 0.0.0.0 --port "${APP_PORT:-48213}"
```

Documentation interactive : `http://localhost:${APP_PORT}/payfund/v1/docs`

## Tests

```bash
docker exec payfund-db psql -U payfund -d payfund -c "CREATE DATABASE payfund_test"
pytest
```

La suite crée son schéma en rejouant les migrations Alembic sur `payfund_test`. Elle charge
`.env` elle-même (`conftest.py`) : `TEST_DATABASE_URL` n'a pas besoin d'être exporté dans le
shell au préalable.

## Organisation

```
payfund_app/
├── core/              config, engine DB, erreurs, vérification JWT locale (JWKS)
├── shared_kernel/
│   ├── contracts/     IdentityVerifierPort · WalletServicePort
│   └── events/        bus interne (Redis Pub/Sub) + adaptateur mémoire pour les tests
└── modules/
    ├── wallet/        presentation · application · domain · infra
    └── fund/          presentation · application · domain · infra
```

`fund` n'importe jamais `payfund_app.modules.wallet` : le seul point de contact est
`fund/infra/wallet_client.py`, qui résout le `WalletServicePort`. C'est le seul fichier à changer
le jour où `fund` devient un service séparé.

## Ce qui est implémenté

| Route | État |
|---|---|
| `GET /wallet/balance` | ✅ |
| `POST /wallet/transfer` | ✅ |
| `POST /wallet/pay/merchant` | ✅ |
| `GET /wallet/transactions` | ✅ filtres `origin_module`, `type`, `from_date`, `to_date` |
| `GET /wallet/transactions/{id}` | ✅ |
| `POST /fund/campaigns` | ✅ |
| `GET /fund/campaigns` | ✅ |
| `GET /fund/campaigns/{id}` | ✅ hors nom d'investisseur (voir plus bas) |
| `POST /fund/campaigns/{id}/invest` | ✅ |
| `POST /wallet/deposit` · `POST /wallet/withdraw` | ✅ passerelle simulée (stub) |
| `POST /fund/loans/simulate` | ✅ |
| `POST /fund/loans` | ✅ prend un `campaign_id` en plus du contrat |
| `GET /fund/loans/{id}` · `GET /fund/loans/{id}/schedule` | ✅ |
| `POST /fund/loans/{id}/repay` | ✅ |
| `POST /wallet/qr/generate` · `POST /wallet/qr/verify` | ✅ format non spécifié par le contrat, fixé ici (voir plus bas) |

Également en place : ledger double entrée avec invariant vérifié à chaque écriture, idempotence
sur toutes les routes qui déplacent des fonds, abonnements `user.registered` / `user.updated` /
`user.suspended`, gel des sorties sur compte suspendu, contre-passation d'un retrait échoué.

### Dépôt et retrait — instant de passage des écritures

Décision produit, non tranchée par les documents :

- **Dépôt** : aucune écriture à l'initiation. L'argent n'existe pas tant que l'opérateur n'a pas
  confirmé. Échec → transaction `failed`, sans jamais aucune écriture.
- **Retrait** : écritures dès l'initiation, les fonds partent vers le compte suspense et sont donc
  réservés. Échec → contre-passation, transaction d'origine marquée `reversed`.

Cela donne leur sens aux quatre statuts du §3.1 : `failed` = opération qui n'a jamais rien écrit,
`reversed` = opération écrite puis annulée par écriture inverse.

Le **canal** par lequel l'opérateur notifie l'issue (webhook entrant ou job de polling) n'est
spécifié nulle part et le contrat n'expose aucune route de callback. La confirmation passe donc
par les use cases `confirmer_operation` / `echouer_operation`, à brancher sur le canal retenu au
moment d'intégrer Orange Money et MTN pour de vrai.

### Prêts — crowdlending

Le pool d'une campagne finance le prêt de son porteur ; ses remboursements y retournent, intérêts
compris, au bénéfice des investisseurs. Le décaissement (`pending → disbursed`) n'a pas de route
HTTP : comme la validation d'une campagne, il relève du back-office et sort du contrat public.

La **redistribution du pool vers les investisseurs** n'est décrite dans aucun document — c'est un
flux distinct, à spécifier.

### QR code de paiement marchand

Contrat §1 : « le frontend scanne, obtient un `merchant_account_id` encodé dans le QR ». Le format
exact est explicitement laissé ouvert (§3 : « à spécifier avec Frontend/Mobile une fois le
composant scanner choisi »). Ce format est maintenant fixé :

- Le QR encode un jeton compact signé HMAC-SHA256 (`domain/qr.py`), pour qu'il ne puisse pas être
  forgé ou altéré afin de rediriger un paiement vers un autre compte. Il n'est pas chiffré : rien
  de confidentiel n'y transite.
- **QR statique** (`amount` absent) : c'est le cas décrit par le contrat — le payeur saisit le
  montant dans l'app après avoir scanné. Seul le propriétaire du compte marchand peut générer son
  QR (`403 NOT_MERCHANT_ACCOUNT_OWNER` sinon).
- **QR à montant fixe** (`amount` fourni, expiration optionnelle) : une **extension au-delà du
  contrat**, pour le cas d'une facture à prix fixe. Ce n'est **pas** un QR à usage unique — rien
  n'empêche qu'il serve pour plusieurs paiements avant expiration ; un vrai usage unique
  demanderait de persister et consommer le jeton, non spécifié à ce stade.
- Ni génération ni vérification ne déplacent de fonds : aucune des deux routes n'exige
  `Idempotency-Key`. Le paiement effectif reste `POST /wallet/pay/merchant`, inchangé.

## Écarts assumés par rapport aux documents

Chacun corrige une contradiction ou un manque des documents source. Ils sont commentés à l'endroit
du code concerné.

| # | Document | Écart |
|---|---|---|
| 1 | Archi §3.1 | `accounts.user_id` passe `NOT NULL UNIQUE` → `NULL` + index unique partiel. Un compte `technical` n'a pas de propriétaire : l'insertion était impossible. |
| 2 | Archi §1 | `WalletServicePort` : `decaisser`/`encaisser` → `debiter`/`crediter`. Le contrat employait les deux noms de façon contradictoire (§2 invest vs §2 repay). |
| 3 | Archi §2 | `debiter`/`crediter` prennent un `contrepartie_compte_id`. Le double entrée interdit un mouvement à un seul compte. |
| 4 | Archi §1 | Ajout de `ouvrir_compte_technique()`, `solde()` et `compte_de_utilisateur()` au port : `fund` ne connaît que des `user_id` et ne peut pas lire `wallet.*`. |
| 5 | Archi §3.2 | `campaigns.wallet_account_id` ajouté : un investissement doit créditer un compte identifié. |
| 6 | Contrat §1 | `wallet.user_phones` ajouté pour résoudre `recipient_phone`. DiddiFreeID n'expose aucune recherche par téléphone ; la table est alimentée par `user.registered`. |
| 7 | Archi §2 | Seuls les comptes suspense de passerelle peuvent passer en solde négatif — mécanique dans l'exemple du dépôt (§2). Les pools de campagne, eux, restent contrôlés. |
| 8 | Contrat §2 | `GET /fund/campaigns/{id}` n'expose pas le nom des investisseurs : ni la préférence de visibilité, ni l'authentification service-à-service de `GET /users/{id}` ne sont spécifiées. |
| 9 | Archi §3.1 | `wallet.transactions` gagne `account_id`, `amount`, `currency`, `provider_reference`, `reverses_transaction_id`. Un dépôt en attente n'a aucune écriture : sans en-tête porteur du montant, il serait introuvable et invisible dans l'historique. |
| 10 | Archi §3.2 | `fund.loans` gagne `campaign_id` (crowdlending), `duration_months`, `interest_rate_applied`, `total_repayable`, `currency`, `created_at` — tous exposés par le contrat mais absents de la table. |
| 11 | Contrat §2 | `POST /fund/loans` prend un `campaign_id` : un prêt est servi par le pool d'une campagne précise, il faut savoir laquelle. |
| 12 | Archi §6 | `ScoringPort` créé, satisfait par un taux fixe configurable (`DEFAULT_INTEREST_RATE`, 6,5 % par défaut = valeur de l'exemple du contrat). Aucune interface du module de scoring IA n'est documentée. |
| 13 | Contrat §3 | Format du QR de paiement fixé (voir plus haut) : jeton signé HMAC, encodant `merchant_account_id` et, en extension, un montant fixe optionnel. Explicitement laissé ouvert par le contrat. |
| 14 | Archi §3.1 | Unicité de compte étendue à `(user_id, currency, account_type)`, plus seulement `(user_id, currency)` : un même utilisateur doit pouvoir posséder un wallet personnel et un compte marchand dans la même devise — nécessaire dès qu'un propriétaire de compte marchand (QR) est aussi client. |

### Points déduits, non inventés

- **Formule de prêt** : intérêt simple sur le capital, réparti en mensualités égales. Déduite de
  l'exemple du contrat §2 — 200 000 sur 6 mois à 6,5 % donne bien 213 000 et 35 500/mois. La
  dernière mensualité absorbe le reliquat d'arrondi, pour que l'échéancier somme exactement au
  total dû.
- **`Money` vs `Balance`** : un montant est toujours positif (en double entrée, c'est la
  `direction` qui porte le sens) ; un solde est une somme algébrique et peut être négatif. Deux
  types distincts plutôt qu'un seul permissif.

## Multi-devises — fondations posées

Aucun paiement international n'est opérationnel (il faut des partenaires), mais rien dans le socle
ne le préclut plus.

- **Montants en unité mineure.** Un montant est un entier d'unités mineures : 5 000 XOF valent
  `5000`, 12,50 EUR valent `1250`. L'exposant vient de `CURRENCIES` (`domain/money.py`) — 0 pour le
  XOF, 2 pour l'euro. Pour le XOF, cela ne change **rien** : ni l'API, ni la base, ni les clients.
  C'est le seul point qui aurait été coûteux à rattraper après la mise en production.
- **Une transaction reste mono-devise.** Deux écritures dans des unités différentes ne peuvent pas
  sommer à zéro ; assouplir l'invariant reviendrait à renoncer à la seule vérification qui détecte
  mécaniquement une incohérence.
- **Une conversion = deux transactions** reliées par un compte de position de change, une par
  devise (`application/exchange.py`). Le solde de ce compte est l'exposition au risque de change de
  la plateforme, et il absorbe les reliquats d'arrondi.
- **Aucun taux codé en dur.** `ForeignExchangeRatePort` lit `wallet.exchange_rates` ; sans
  cotation, la conversion est refusée (`503 EXCHANGE_RATE_UNAVAILABLE`) plutôt que devinée. Le taux
  retenu est figé sur la conversion, pour qu'un historique rejoué donne le même résultat.
- **Un compte par devise et par utilisateur** (l'unicité porte sur `(user_id, currency)`). Tant
  qu'on n'opère qu'en XOF, cela revient à un compte unique, comme avant.

Reste à décider avec le produit et les partenaires : détenir ou non des soldes en devise
étrangère, modèle de commission et de spread, comptes correspondants, et le volet réglementaire
BCEAO (agrément change, déclarations transfrontalières, seuils AML). `ConvertirDevise` n'a
volontairement pas de route HTTP — aucune n'est spécifiée au contrat.

## Conventions

- Montants : entiers d'unités mineures. Garanti par le value object `Money`.
- Erreurs : `{"error": {"code", "message", "details"}}`, codes HTTP du contrat DiddiFreeID §0.
- Idempotence : en-tête `Idempotency-Key` obligatoire sur toute route qui déplace des fonds ;
  une clé déjà vue renvoie la transaction d'origine sans rien rejouer.
- Aucune écriture de ledger n'est jamais modifiée ni supprimée.
