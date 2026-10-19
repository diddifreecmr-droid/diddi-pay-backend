# Évaluation Jèko (Jeko Africa) — agrégateur de paiement

Suit la méthodologie de `docs/handoff-evaluation-agregateur-paiement.md`.
**État : contrat COMPLET extrait des schémas OpenAPI officiels (serveur MCP
`mcp.jeko.africa`) ET TESTÉ EN DIRECT contre `api.jeko.africa` avec une clé marchande
valide** (les deux stores de test `DIDDIFREE` / `DiddiGo` sont à solde 0 → tests payout
sans risque financier). Résultats réels en **§11**. Seul le volet **escrow / Service
Provider** (l'angle BCEAO) reste non testable : il exige une **clé plateforme** + un
onboarding Service Provider (confirmé en direct par un `403`, §11). Ni intégration ni
code écrits — « tester et documenter » uniquement, en attente de permission explicite (§3).

> Historique : une première version de ce rapport déduisait « pas d'endpoint solde »
> et « pas de remboursement » depuis des sondes `404`. **Corrigé** : les bons chemins
> existent (`/stores/{id}/balance`, remboursement via escrow). Les `404` venaient de
> chemins devinés, pas de l'absence réelle. Le contrat ci-dessous est tiré des schémas
> officiels, pas de suppositions.

## 0. Identité de l'API & environnement — piège important

- **Base URL unique** : `https://api.jeko.africa`, préfixe de route `/partner_api/…`.
  Pas de host séparé prod/sandbox.
- **PAS d'environnement sandbox.** Doc explicite : *« Il n'existe pas d'environnement
  sandbox : vos appels d'intégration touchent les mêmes données que la production. »*
  Isolation via un **store de test** dédié + un **rôle développeur** limité à ce store.
  La clé fournie (`jeko_…`, « test api key ») est donc rattachée à un **store de test en
  production**, pas à un sandbox.
  → **Prudence §0 handoff** : un `POST /transfers` ou une `release` d'escrow depuis un
  store de test **approvisionné** déplacerait de vrais fonds. Tant que le store de test
  n'est pas approvisionné, risque nul — mais **ne jamais supposer « sandbox = gratuit »**.
  Confirmer avec l'opérateur que le store de test n'est pas approvisionné avant tout payout.
- Clés fournies :
  - `jeko_85adfa…` — « test api key » → **`X-API-KEY`** (le secret).
  - `b58b1a5a…` (64 hex) — « API webhook generale » → **secret de signature webhook**
    (§5), **pas** un identifiant d'API.
- Sources : doc web `developer.jeko.africa` + **serveur MCP officiel**
  `https://mcp.jeko.africa/mcp` (outils `search_docs`, `get_endpoint` ; ressources
  `jeko://api/*` = schémas OpenAPI complets ; répond **sans authentification**). C'est
  de là que vient le contrat exact ci-dessous.

## 1. Authentification — vérifié en direct + schéma officiel

Deux en-têtes **tous deux obligatoires** sur chaque requête `/partner_api/…` :

| En-tête | Valeur | Fourni ? |
|---|---|---|
| `X-API-KEY` | le secret `jeko_…` | ✅ oui |
| `X-API-KEY-ID` | l'identifiant de la clé (UUID, distinct du secret) | ✅ **fourni** (`12823823-…`) |
| `Content-Type` | `application/json` | — |

**Auth validée en direct** : `GET /partner_api/stores` → `200`, renvoie les stores
`DIDDIFREE` et `DiddiGo`. L'accès API est donc bien activé sur ce business (pas de 403).

Pas de JWT, pas de `Bearer`, pas de login (contraste avec CinetPay v1). Les deux clés se
récupèrent dans **Dashboard Business (business.jeko.africa) → Paramètres → API & Webhooks**.

**⚠️ Deuxième verrou, distinct des clés : l'accès API doit être ACTIVÉ.** Doc officielle :
*« Générer vos clés ne suffit pas. »* Si l'accès n'est pas activé → **HTTP 403**
`{"id":"business_not_enabled_for_api_access"}`. Un **401** signifie clés/en-têtes
incorrects ; un **403** signifie clés bonnes mais accès non activé (étape de conformité).

**Testé en direct** contre `GET /partner_api/stores` (appel inoffensif, handoff étape 3) :

| En-têtes envoyés | Réponse réelle |
|---|---|
| `X-API-KEY` seul | `401` · `"X-API-KEY-ID header is required"` |
| `Authorization: Bearer <jeko>` | `401` · `"X-API-KEY header is required"` (Bearer ignoré) |
| `X-API-KEY=jeko` + `X-API-KEY-ID=<secret webhook>` | `401` · `"Invalid API key ID"` |
| `X-API-KEY=jeko` + `X-API-KEY-ID=<faux>` | `401` · `"Invalid API key ID"` |
| `X-API-KEY=FAKE` + `X-API-KEY-ID=FAKE` | `401` · `"Invalid API key ID"` |

Interprétation : `X-API-KEY-ID` structurellement obligatoire, validé **avant** le
`X-API-KEY`. Le secret webhook n'est **pas** un key-ID valide. **Impossible d'authentifier
le moindre appel** sans le `X-API-KEY-ID` → tout le reste est bloqué. (La validité du
`jeko_…` lui-même n'a pas pu être confirmée, l'ID étant rejeté en premier.)

## 2. Capacités — endpoints (schémas OpenAPI officiels)

`401` en direct = route existe derrière le mur d'auth ; routes et champs ci-dessous
confirmés par les ressources `jeko://api/*` du MCP.

| Capacité | Endpoint | Méthode | Clé requise |
|---|---|---|---|
| Liste des stores | `/partner_api/stores` | GET | marchande |
| **Solde d'un store** | `/partner_api/stores/{storeId}/balance` | GET | marchande |
| Dispositifs (TPE) | `/partner_api/devices` | GET | marchande |
| Collecte (payin) | `/partner_api/payment_requests` | POST | marchande |
| Vérif collecte | `/partner_api/payment_requests/{id}` | GET | marchande |
| Lien de paiement | `/partner_api/payment_links` | POST | marchande |
| Vérif lien | `/partner_api/payment_links/{id}` | GET | marchande |
| Payout (transfert) | `/partner_api/transfers` | POST | marchande |
| **Vérif payout** | `/partner_api/transfers/{id}` | GET | marchande |
| Contacts (bénéficiaires) | `/partner_api/contacts` | GET / POST / DELETE | marchande |
| Liste des banques | `/partner_api/banks` | GET | marchande |
| **Grand livre transactions** | `/partner_api/transactions` | GET | marchande |
| Escrow — lister/créer/lire | `/partner_api/escrows`, `…/{id}` | GET / POST / GET | **platform** |
| **Escrow — release** | `/partner_api/escrows/{id}/release` | POST | **platform** |
| **Escrow — refund** | `/partner_api/escrows/{id}/refund` | POST | **platform** |
| Service Providers (marketplace) | `/partner_api/…` (nombreux, §6) | GET/POST/DELETE | **platform** |

Il n'y a **pas** d'endpoint générique `/partner_api/refunds` (404 en direct) : le
remboursement passe par l'escrow (voir §4).

### Collecte (paiement) — page hébergée ✅ (le « bon cas » du handoff)

- **Init** : `POST /partner_api/payment_requests`
  - Champs : `storeId` (UUID), `amountCents` (**centimes**, multiple de 100, mini 100 =
    1 XOF), `currency` (`XOF`), `reference` (unique, 5–100 car.),
    `paymentDetails.type` (`"redirect"`), `paymentDetails.data.paymentMethod`
    (`wave`|`orange`|`mtn`|`moov`|`djamo`|`jeko`),
    `paymentDetails.data.successUrl`, `paymentDetails.data.errorUrl` (HTTPS).
  - Réponse : `id`, `storeId`, `reference`, `type`, `paymentMethod`, `status`
    (`pending`), **`redirectUrl`** (ex. `https://pay.jeko.africa/pay_request/pr/{id}`),
    `errorReason`.
  - **`redirectUrl` = URL de redirection hébergée** → se mappe **directement** sur
    `NextActionType.REDIRECT`, comme Paystack / CinetPay (`payment_url`) / PawaPay Payment
    Page. **Aucun changement requis côté DiddiGo/DiddiSend/DiddiFood.** Scénario idéal §9 handoff.
- **Vérification** : `GET /partner_api/payment_requests/{id}`
  - Réponse : `id`, `status` (`pending`|`success`|`error`), et si abouti
    `transaction{ id, amount{amount,currency}, fees{amount,currency}, status,
    counterpartLabel, counterpartIdentifier (ex. +225…), executedAt }`.
  - → **Vérification par polling disponible** (≠ webhook-only). La doc *recommande* le
    webhook comme « seule confirmation fiable », mais le GET existe pour la réconciliation
    (gate BCEAO). **Important : un paiement échoué n'émet AUCUN webhook** (§5) → le polling
    du GET est le seul moyen de constater un échec.

### Charge directe (push mobile money sans page marchande)

- Même endpoint, `paymentDetails.data.forceProviderDirect: true` +
  `paymentDetails.data.payerPhone` (`+2250765432108` ou `2250765432108`).
- MTN/Moov : push USSD direct ; `redirectUrl` = page d'attente Jèko. Orange :
  `redirectUrl` → `webpay.orange.ci`.
- **Favorable** : même en direct, la réponse contient **toujours un `redirectUrl`** →
  intégrable via `REDIRECT` sans écran custom par app (≠ push « sans URL » de PawaPay).

### Payout (transfert) — modèle « contact », schéma exact

- **Création** : `POST /partner_api/transfers` — `amountCents` **mini 500 (= 5 XOF)**,
  `storeId`, `currency`, `reference` (optionnel 5–100), `description` (optionnel).
  **Résolution du bénéficiaire : fournir exactement UN des quatre** :
  1. `contactId` (UUID d'un bénéficiaire pré-créé) ;
  2. **inline mobile money** : `name`, `paymentMethod` ∈ {`orange`,`wave`,`mtn`,`moov`,
     `djamo`}, `identifier.reference` (= numéro de téléphone) → crée un contact masqué ;
  3. **inline bancaire** : `name`, `paymentMethod`=`bank`, `identifier` = RIB complet
     (`bankName`, `bankCode`, `swiftCode`, `agencyCode`, `accountNumber`, `key`) ;
  4. **Jèko→Jèko** : `paymentMethod`=`jeko` + `destinationStoreId` → **synchrone, 0 frais**.
  - Statuts transfert : `pending` → `success` | `error`. Échecs typiques (doc dédiée) :
    **solde insuffisant**, **contact introuvable**, **refus opérateur**.
- **Vérification** : `GET /partner_api/transfers/{id}` → statut + détails (polling payout OK).
- **Contacts** : `GET`/`POST`/`DELETE /partner_api/contacts` — gestion explicite des
  bénéficiaires. Les transferts inline créent un contact *masqué* (absent de `GET /contacts`
  tant qu'il n'est pas créé explicitement).

### Solde — `GET /partner_api/stores/{storeId}/balance`

- Réponse : `{ amount (en centimes), currency }`. **Disponible = total − bloqué**, somme
  des wallets du store. (Mon 404 initial venait du mauvais chemin `/balance` ; le vrai
  chemin exige le `storeId`.) → à appeler **avant un transfert** pour vérifier les fonds.

## 3. Statuts

- Collecte / transfert : `pending` → `success` | `error` (+ `errorReason`).
- Escrow : `pending` | `held` | `released` | `refunded` | `expired`.
- Finaux côté collecte/transfert : `success`, `error`. `pending` = en vol
  (→ `PROCESSING` / `REQUIRES_ACTION` côté DiddiPay).

## 4. Remboursement — via ESCROW uniquement (pas d'endpoint générique)

- **Pas de `POST /partner_api/refunds`** (404 en direct). À la place, Jèko expose un
  **séquestre (escrow)** :
  - Fonds d'une collecte peuvent être **retenus** (`ESCROW_HELD`) au lieu d'être crédités
    directement — via `escrowId` sur un lien de paiement / `Escrows_createEscrow`.
  - `POST /partner_api/escrows/{id}/release` → **libère** vers le wallet du marchand (crée
    une transaction `type=escrow` ; double release = `409`).
  - `POST /partner_api/escrows/{id}/refund` → **rembourse le payeur d'origine** (« Partner
    GET /transactions stays empty for this job » = pas de transaction dépensable créée).
- **Accès : `platform key only`** (clé Service Provider, §6). Un store marchand simple ne
  peut pas rembourser ; il faut opérer en **plateforme/Service Provider**.
- Conséquence intégration : le remboursement DiddiPay n'est **pas** un simple « annuler une
  transaction quelconque ». Il est **conditionnel** : seul un montant mis en escrow peut
  être remboursé, et seulement avec une clé plateforme. Deux options côté `refund_payment` :
  (a) `NotImplementedError` si DiddiPay n'utilise pas l'escrow / n'a pas de clé plateforme
  (comme CinetPay) ; (b) implémenter via `escrows/{id}/refund` **si** le flux escrow +
  clé plateforme est en place. À trancher avec l'opérateur.

## 5. Webhooks — signature forte ✅

- Signature : en-tête **`Jeko-Signature`** = **HMAC-SHA256 du corps brut (raw body), en
  hexadécimal**. Vérifier sur le **corps brut**, jamais le JSON re-sérialisé.
- Secret = **la clé `b58b1a5a…` fournie** (« API webhook generale »). → Jèko fournit un
  **vrai secret HMAC** (meilleur que PawaPay non câblé / CinetPay simple echo de token).
- En-têtes : `Jeko-Signature`, `Jeko-Event` (nom d'événement), `Jeko-Webhook-Version`.
- Politique : jusqu'à **3 tentatives**, traitement **idempotent** attendu.
- **Un paiement échoué n'émet PAS d'événement** → l'absence de webhook = échec **ou**
  pending ; constater l'échec via `GET payment_requests/{id}`.

Événements et payloads (schémas officiels) :

| Événement | Déclencheur | Champs clés du payload |
|---|---|---|
| `TRANSACTION_COMPLETED` | paiement **ou** transfert abouti | `id`, `amount{amount,currency}`, `fees`, `status`, `counterpartLabel/Identifier`, `paymentMethod`, **`transactionType` (`payment`\|`transfer`)**, `storeId`, `transactionDetails{id,reference,paymentLinkId}`, `executedAt` (pas de champ `event` dans le corps) |
| `ESCROW_HELD` | fonds retenus en séquestre | `event`, `escrowId`, `storeId`, `status:held`, `amount`, `paymentRequestId`, `walletAvailableBalance` |
| `ESCROW_RELEASED` | libération vers le marchand | `event`, `escrowId`, `transactionId`, `status:released`, `walletAvailableBalance` |
| `ESCROW_REFUNDED` | remboursement au payeur | `event`, `escrowId`, `status:refunded`, `walletAvailableBalance` (pas de tx dépensable) |
| `SERVICE_PROVIDER_LINK_REQUEST` | rattachement marchand↔plateforme | `id`, `status`, `merchantBusinessId/Name`, `serviceProviderBusinessId/Name`, `ownerPhone`, `expiresAt` |
| `COMPLIANCE_VERIFICATION_COMPLETED` | KYC/KYB terminé | `id`, `type` (`kyc`\|`kyb`), `status` (`succeeded`\|`failed`), `merchantBusinessId` |

- **Non vérifié en pratique** (aucun webhook reçu, pas d'endpoint public pour déclencher).
  Schéma + secret connus ; la vérif HMAC réelle reste à faire. Confirmation d'abord via
  `GET …/{id}` + réconciliation (gate BCEAO).

## 6. Modèle « Service Provider » (escrow + marketplace) — PERTINENT BCEAO

Découverte majeure : Jèko n'est pas qu'un PSP mobile money, c'est une **plateforme
marketplace/escrow**. Endpoints Service Provider (clé plateforme) :
`onboardBusiness`, `createBusinessApiKey`, `list/create/getBusinessLinkRequests`,
`listBusinessLinks`, `detachBusinessLink`, `assign/unassignFeeCatalog`,
`create/delete/listBusinessWebhookSubscription`, `create/getComplianceVerification`
(KYC/KYB), `listActivities`, `listLocations`, + tout le bloc **escrow** (held/release/refund).

→ **Lien direct avec la contrainte d'agrément BCEAO** (cf. [[contrainte-agrement-bceao]] :
collecter puis reverser pour des tiers exige un agrément ou un **adossement**). Le modèle
Service Provider + escrow de Jèko correspond **exactement** au besoin DiddiFund
([[diddifund-est-du-crowdlending]] : le pool d'une campagne finance le prêt du porteur) :
collecter des contributeurs → **retenir en escrow** → **libérer** au porteur ou
**rembourser** les contributeurs. C'est potentiellement l'adossement réglementaire
recherché. **À creuser sérieusement avec l'opérateur** — c'est peut-être l'intérêt n°1 de
Jèko pour DiddiPay, au-delà du simple payin/payout. (Hors périmètre du test immédiat ;
nécessite une clé plateforme + onboarding Service Provider, pas la clé de test marchande.)

## 7. Montants, frais, limites, réseaux

- **Unité : `amountCents` en CENTIMES.** Payin : multiple de 100, mini 100 (= 1 XOF).
  Transfert : mini **500** (= 5 XOF). Lien de paiement : mini 10 000 centimes (= 100 XOF),
  `title` 10–255 car., `allowMultiplePayments`, `escrowId` optionnel.
  → **Piège d'intégration** : envoyer `XOF × 100` (≠ CinetPay en francs entiers).
- Max : **2 000 000 F par transaction réseau**.
- Frais (doc) : payin 1,5 % (checkout/liens), 1 % (Box Wave) ; payout mobile money 1,5 %,
  virement bancaire **1 000 F fixe** ; échecs **gratuits** ; Jèko→Jèko gratuit. Frais réels
  aussi renvoyés dans `transaction.fees`.
- Rate limit : **500 req/min**, **1 000 / 5 min** par entreprise ; dépassement → blocage
  temporaire 10–15 min.
- **Pays : Côte d'Ivoire (CI)**, devise **XOF**. Réseaux : **`wave`**, `orange`, `mtn`,
  `moov`, **`djamo`** (néobanque, spécificité Jèko), `jeko` (interne), `bank` (RIB /
  GIM-UEMOA), + cartes Visa/Mastercard sur la page hébergée. **Wave natif payin ET payout.**
- ⚠️ « Listé » ≠ « activé sur le store de test » → vérifier Wave en direct une fois débloqué
  (ne pas répéter le piège Wave des évaluations précédentes).

## 8. Tableau comparatif

| Capacité | **Jèko** | CinetPay v1 | PawaPay | PayDunya | FeexPay |
|---|---|---|---|---|---|
| Collecte page hébergée (REDIRECT) | ✅ `redirectUrl` | ✅ testé live (`channel=ALL`) | ✅ Payment Page | — | — |
| Push direct mobile money | ✅ `forceProviderDirect` **+ renvoie un `redirectUrl`** | ⚠️ `direct_pay` ok | ✅ /v2/deposits (sans URL) | — | — |
| Vérif collecte par polling | ✅ `GET payment_requests/{id}` | ✅ | ✅ | — | — |
| Payout | ✅ `POST /transfers` (modèle contact) | ✅ /v1/transfer testé live | ✅ /v2/payouts | ⚠️ pas de sandbox décaissement | — |
| Vérif payout par polling | ✅ `GET transfers/{id}` | ✅ | ✅ | — | — |
| Remboursement | ⚠️ **via escrow, clé plateforme** (`escrows/{id}/refund`) | ❌ absent | ✅ /v2/refunds | — | — |
| Solde | ✅ `GET stores/{id}/balance` | ✅ /v1/balances | ✅ | — | — |
| Grand livre / transactions | ✅ `GET /transactions` | — | — | — | — |
| Escrow / séquestre | ✅ **held/release/refund** (adossement BCEAO ?) | ❌ | ❌ | ❌ | ❌ |
| Marketplace / Service Provider | ✅ onboarding + liens + KYC/KYB | ❌ | ❌ | ❌ | ❌ |
| Wave | ✅ natif payin+payout (non vérifié live, auth bloquée) | ✅ accepté live | ❌ pas actif | documenté | documenté |
| Djamo | ✅ natif | ❌ | ❌ | ❌ | ❌ |
| Signature webhook | ✅ **HMAC-SHA256 raw body** (secret fourni) | token echo | — (non câblé) | — | — |
| Sandbox | ❌ **pas de sandbox** (store de test en prod) | ✅ host sandbox | ✅ host sandbox | partiel | — |
| Auth | 2 en-têtes + activation d'accès séparée | JWT via login | token | token | token |

## 9. Accès — RÉSOLU (clé marchande) ; escrow/SP en attente de clé plateforme

Le `X-API-KEY-ID` (`12823823-…`) a été fourni → **auth OK, tests 3–10 déroulés en direct**
(§11). L'accès API est activé (pas de 403 `business_not_enabled_for_api_access`).

Reste non testable avec la clé actuelle (clé **marchande**) : le volet **escrow** et
**Service Provider**, qui répond `403 "Escrow requires a platform API key"` /
`403 "business_not_service_provider"` (§11). Pour l'explorer (c'est l'angle BCEAO, §6) :
- une **clé plateforme** (platform API key), distincte de la clé marchande ;
- l'onboarding du business DIDDIFREE comme **Service Provider**.

Clés manipulées en variables d'environnement uniquement ; aucun script ni secret commité.

## 10. Recommandation (en attente de décision — PAS d'intégration sans « oui »)

Jèko est le **candidat le plus intéressant** évalué à ce jour, pour trois raisons :

1. **Page hébergée `redirectUrl`** → `NextActionType.REDIRECT`, **zéro changement client**
   (DiddiGo/DiddiSend/DiddiFood) — même profil d'intégration que CinetPay.
2. **Couverture complète XOF/CI** : Wave + Orange + MTN + Moov + Djamo + carte + virement
   bancaire, avec **vrais endpoints de vérification** (collecte ET payout), **endpoint de
   solde**, **grand livre `/transactions`**, et **signature webhook HMAC-SHA256** forte.
3. **Modèle escrow + Service Provider** (§6) — potentiellement **l'adossement réglementaire
   BCEAO** pour le collecter-puis-reverser de DiddiFund. Aucun autre agrégateur évalué n'offre ça.

Points de vigilance avant tout code : **(a)** `amountCents` en centimes (×100) ; **(b)** pas
de sandbox → discipline sur le store de test non approvisionné ; **(c)** modèle `contact`
pour les payouts ; **(d)** remboursement **conditionnel à l'escrow + clé plateforme** (pas
un refund générique) ; **(e)** `X-API-KEY-ID` à injecter en variable d'environnement comme
la clé ; **(f)** activer l'accès API côté business.

Si tu veux que j'intègre Jèko, **dis-le explicitement** (handoff §3). Je reproduirai alors
le contrat de `pawapay_processor.py` / `cinetpay_processor.py` (`PaymentProcessorPort`,
`ProcessorCapabilities` précises, exclusion `SIMULATED_PROCESSOR_NAMES`, enregistrement
conditionné par env dans `deps.py`, tests sur le modèle existant, priorité explicite, puis
re-vérification en direct). **D'ici là, aucune ligne de code d'intégration.**

Si tu veux l'explorer plus loin côté escrow/BCEAO, il faudra une **clé plateforme** +
l'onboarding Service Provider du business DIDDIFREE (§6, §9).

## 11. Tests en direct — résultats réels (clé marchande, store `DIDDIFREE`, solde 0)

Exécutés contre `api.jeko.africa` (production, pas de sandbox) avec la clé marchande
fournie. Les deux stores sont à **solde 0 XOF** → aucun payout ne peut déplacer de fonds
(gate « insufficient balance »). Scripts d'exploration dans le scratchpad, creds en
variables d'env, **rien commité**.

| Étape | Appel | Résultat réel |
|---|---|---|
| Auth | `GET /partner_api/stores` | ✅ `200` · stores `DIDDIFREE` (`0ae7d8e1…`), `DiddiGo` (`75ee7e7c…`) |
| Solde | `GET /stores/{id}/balance` | ✅ `200` · `{amount:0, currency:XOF}` (les deux stores) |
| Devices / Contacts | `GET /devices`, `/contacts` | ✅ `200` · `[]` (vides) |
| Grand livre | `GET /transactions` | ✅ `200` · `{total:0, perPage:100, currentPage:1, data:[]}` (paginé) |
| Banques | `GET /banks` | ✅ `200` · liste CI (`id`, `name`, `swiftCode`, `bankCode`, `country:CIV`) |
| **Collecte Wave (page hébergée)** | `POST /payment_requests` `amountCents:100`, `wave` | ✅ `200` · **`redirectUrl: https://pay.jeko.africa/pr/{id}`** · `status:pending` |
| Vérif collecte | `GET /payment_requests/{id}` | ✅ `200` · `status:pending` · expose **`escrowId:null`** (champ escrow présent) |
| **Charge directe Orange** | `forceProviderDirect:true` + `payerPhone` | ✅ `200` · **vraie URL Orange Money** `mpayment.orange-money.com/ci/…` |
| **Charge directe Wave** | idem, `wave` | ✅ `200` · **vraie URL Wave** `pay.wave.com/c/cos-…?a=1&c=XOF&m=DIDDIFREE…` |
| Échec: montant non multiple 100 | `amountCents:150` | ✅ `422` · `{errors:[{rule:not_multiple_of_100, field:amountCents}]}` |
| Échec: paymentMethod invalide | `paymentMethod:bitcoin` | ✅ `422` · enum · **`meta.choices:[orange,wave,mtn,moov,djamo,jeko]`** |
| **Payout Wave** (inline) | `POST /transfers` `amountCents:500`, `wave` | ✅ endpoint vivant · `400` · **`{id:insufficient_balance}`** (solde 0, rien n'a bougé) |
| Payout sans bénéficiaire | `POST /transfers` sans destinataire | ✅ `422` · `{rule:mutuallyExclusive}` (validation avant tout mouvement) |
| Escrow (list/create) | `GET`/`POST /escrows` | ⛔ `403` · **`"Escrow requires a platform API key"`** |
| Service Provider | `GET /service_providers/activities` | ⛔ `403` · **`"business_not_service_provider"`** |

**Enseignements confirmés en direct :**
1. **Page hébergée `redirectUrl` ✅** (le « bon cas » handoff) — `NextActionType.REDIRECT`,
   zéro changement client. URL réelle : `https://pay.jeko.africa/pr/{id}`.
2. **Wave bout-en-bout ✅** : payin page hébergée, payin direct (URL `pay.wave.com`), et
   payout accepté jusqu'au gate de solde. Wave est **réellement actif** sur le compte (≠ le
   piège Wave des autres agrégateurs).
3. **Charge directe ✅** : Orange → URL Orange Money ; Wave → URL Wave ; toutes renvoient
   quand même un `redirectUrl` → intégrable via REDIRECT sans écran custom.
4. **Deux familles d'erreurs** : validation `422 {errors:[{rule,field,meta}]}` (avant tout
   traitement) vs métier `400/403 {id,message,extras}` (ex. `insufficient_balance`,
   `forbidden_action`). Le code d'intégration devra parser les deux.
5. **`amountCents` strict** : multiple de 100 imposé côté serveur (`422` sinon) — confirme
   l'unité centimes. Payout mini 500c.
6. **Escrow + Service Provider = clé plateforme uniquement** (403 en direct). L'angle BCEAO
   (§6) nécessite donc une démarche séparée (platform key + onboarding SP), non couverte par
   la clé de test marchande.

**Non vérifié (impossible en l'état) :** settlement complet d'une collecte (nécessite qu'un
payeur réel autorise le paiement) et d'un payout (nécessite d'approvisionner un store) ;
**signature webhook** en pratique (aucun webhook reçu, pas d'endpoint pour le déclencher) —
le secret et le schéma HMAC-SHA256 sont connus (§5), confirmation prévue via `GET …/{id}` +
réconciliation. Les endpoints sont prouvés vivants et corrects jusqu'à ces points de blocage.

## 12. Intégration DiddiPay — FAITE (modèle PawaPay + Paystack)

Autorisation explicite reçue. Adaptateur écrit sur le modèle de `pawapay_processor.py`
(payout/verify, `ProcessorCallUncertain` sur transport) et `paystack_processor.py`
(flux REDIRECT, conversion minor-units ×100).

- **`payfund_app/modules/payments/infra/jeko_processor.py`** — `JekoPaymentProcessor`.
  - Capacités : `currencies={XOF}`, `directions={COLLECTION, PAYOUT}` (**pas REFUND**),
    `channels={mobile_money}`, `networks=frozenset()` (vide → ne détourne aucun réseau dédié ;
    le réseau exact est mappé à l'init/payout : `{orange,wave,mtn,moov,djamo}` → `paymentMethod`).
  - Collecte : `POST /partner_api/payment_requests` → `redirectUrl` → `NextActionType.REDIRECT`
    (identique Paystack). `amountCents = XOF × 100`. `reference = dpi_{attempt_id.hex}`.
    `provider_reference` = l'`id` Jèko renvoyé.
  - Vérif : `GET /partner_api/payment_requests/{id}` ; `pending→PROCESSING`, `success→SUCCEEDED`
    (lit `transaction.amount/fees` en centimes ÷100), `error→FAILED`.
  - Payout : `POST /partner_api/transfers` (bénéficiaire inline MM : `name`+`paymentMethod`+
    `identifier.reference`=téléphone). Vérif : `GET /partner_api/transfers/{id}`.
  - `refund_payment` → `NotImplementedError` (escrow + clé plateforme). `parse_webhook` →
    `NotImplementedError` (gate BCEAO SCRUM-555 ; secret HMAC câblé en config pour plus tard).
  - Parse les **deux familles d'erreurs** : `422 {errors:[…]}` et `400/403 {id,message,extras}`.
- **`core/config.py`** : `jeko_api_key`, `jeko_api_key_id`, `jeko_store_id`,
  `jeko_base_url` (défaut `https://api.jeko.africa`), `jeko_webhook_secret`.
- **`presentation/deps.py::get_processor_registry()`** : enregistré **si** les 3 clés sont
  présentes (key + key_id + store_id), **priorité 120** (après Paystack 100 / CinetPay 110 →
  jamais auto-sélectionné devant l'existant ; utilisé sur sélection explicite ou en repli).
- **Tests** : `tests/test_jeko_processor.py` (27) + 2 cas de registry dans
  `tests/test_payment_processor_registry_deps.py`. **Suite complète : 473 passent.**
- **Re-vérifié en direct** (pas que les mocks) : le vrai `JekoPaymentProcessor` contre
  `api.jeko.africa` → init Wave `requires_action`+`redirectUrl` réel, verify `processing`,
  payout `failed/insufficient_balance` (store solde 0, aucun mouvement). Mappings ×100 /
  `paymentDetails` / parsing d'erreurs confirmés par le vrai code.
- `DiddiPay_Contrat_API.md` / payouts contract : **inchangés** — comportement visible = REDIRECT
  (identique Paystack), aucun nouveau champ côté DiddiGo/DiddiSend/DiddiFood.
- **Pas encore poussé/déployé.** Ne jamais redéployer soi-même via Portainer (pousser git puis
  demander à l'opérateur).

Config à poser côté environnement (staging/prod) pour activer Jèko :
`JEKO_API_KEY`, `JEKO_API_KEY_ID`, `JEKO_STORE_ID` (store visé), `JEKO_WEBHOOK_SECRET`
(pour plus tard). Sans ces variables, le processeur n'est tout simplement pas enregistré.
