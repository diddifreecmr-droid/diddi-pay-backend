# Évaluation CinetPay — agrégateur de paiement

Suit la méthodologie de `docs/handoff-evaluation-agregateur-paiement.md`.
**État : contrat vérifié depuis le SDK officiel ET testé en direct contre le
sandbox `api.cinetpay.net`** (depuis le conteneur de staging `diddi-pay-backend`,
IP whitelistée). Voir §10 pour les résultats réels — qui ont corrigé une hypothèse
clé du code (channel). Settlement complet des payouts non vérifié (solde sandbox = 0).

## 0. Identité de l'API — piège important

Il existe **deux API CinetPay distinctes**, à ne pas confondre :

| | API Checkout « classique » | **API v1 mobile money (celle-ci)** |
|---|---|---|
| Base URL | `api-checkout.cinetpay.com/v2` | `api.cinetpay.net` (sandbox) / `api.cinetpay.co` (prod) |
| Auth | `apikey` + `site_id` dans chaque requête | JWT via `api_key` + `api_password` |
| Format clé | opaque + site_id numérique | `sk_test_…` / `sk_live_…` |

La clé fournie, `sk_test_…`, est une clé **sandbox de l'API v1**. C'est bien
celle-ci qu'on intègre. Source : SDK officiel `github.com/cinetpay/cinetpay-python`
(+ `cinetpay-mcp`), lu directement car la doc web ne se charge pas proprement.

## 1. Authentification — vérifié depuis la source SDK

- `POST /v1/oauth/login`, corps JSON `{"api_key": …, "api_password": …}`.
- Réponse : `access_token`. Mis en cache ~23 h (82800 s) côté SDK.
- Requêtes authentifiées : en-tête `Authorization: Bearer {access_token}`.
- En-têtes par défaut : `Accept: application/json`, `Content-Type: application/json`.
- Rejeu automatique du login si code `1003` / statut `EXPIRED_TOKEN`.
- **Sandbox vs prod déterminé par le préfixe de la clé** : `sk_test_` → `api.cinetpay.net`,
  `sk_live_` → `api.cinetpay.co`. Donc la clé fournie est **sans ambiguïté sandbox** →
  tests en direct sûrs (rien en production).
- Garde SSRF : hôtes autorisés = `api.cinetpay.net`, `api.cinetpay.co`, `localhost`.

## 2. Capacités

### Collecte (paiement) — page hébergée ✅ (le « bon cas » du handoff)

- **Init** : `POST /v1/payment`
  - Champs envoyés : `currency`, `merchant_transaction_id`, `amount`, `lang`,
    `designation`, `client_email`, `client_first_name`, `client_last_name`,
    `success_url`, `failed_url`, `notify_url`, `channel`
    (+ optionnels `payment_method`, `client_phone_number`, `direct_pay`, `otp_code`).
  - Réponse : `code`, `status`, `payment_token`, `notify_token`, `transaction_id`,
    `merchant_transaction_id`, **`payment_url`**, `details{code,status,message,must_be_redirected}`.
  - **`payment_url` = URL de redirection hébergée** → se mappe directement sur
    `NextActionType.REDIRECT`, comme Paystack / la Payment Page PawaPay.
    **Aucun changement requis côté DiddiGo/DiddiSend/DiddiFood.**
  - `channel="PUSH"` existe aussi (push direct sans redirection) mais exigerait un
    nouvel écran par app — à ne pas utiliser pour un premier palier (même leçon que PawaPay).
- **Vérification** : `GET /v1/payment/{identifier}` où `identifier` ∈
  {`payment_token`, `transaction_id`, `merchant_transaction_id`}.
  Réponse : `code`, `status`, `merchant_transaction_id`, `transaction_id`,
  `user{name,email,phone_number}`.

### Payout (transfert)

- **Création** : `POST /v1/transfer`
  - Champs : `currency`, `merchant_transaction_id`, `phone_number` (format international),
    `amount`, `payment_method` (ex. `OM_CI`, `WAVE_SN`), `reason`, `notify_url`.
  - Réponse : `code`, `status`, `merchant_transaction_id`, `transaction_id`,
    `notify_token`, `amount`, `fee_amount`, `phone_number`, `currency`.
- **Vérification** : `GET /v1/transfer/{transaction_id}`.
- ⚠️ Le MCP officiel `cinetpay-mcp` a **désactivé `create_transfer`** en v0.1.1
  (« return unavailable message ») — à investiguer en direct : le transfert est-il
  réellement activé sur le compte/sandbox, ou nécessite-t-il une activation séparée ?

### Solde

- `GET /v1/balances` → `available_balance`, `currency`. Premier appel inoffensif idéal
  pour valider les clés (handoff étape 3).

### Remboursement — ABSENT

- **Aucun endpoint de remboursement dans cette API** (le SDK n'expose que
  `payment`, `transfer`, `balance`). Contraste avec PawaPay (`/v2/refunds` réel).
  → `refund_payment` devra lever `NotImplementedError` / capacité REFUND non déclarée.

## 3. Statuts

`TransactionStatus` : `OK`, `SUCCESS`, `OPERATION_ERROR`, `NOT_FOUND`,
`INVALID_CREDENTIALS`, `INVALID_PARAMS`, `EXPIRED_TOKEN`, `INVALID_TOKEN`,
`TRANSACTION_EXIST`, `INITIATED`, `PENDING`, `EXPIRED`, `OTP_ERROR`, `OTP_EXPIRED`,
`INSUFFICIENT_BALANCE`, `USER_NOT_FOUND`, `USER_IS_BLOCKED`, `FAILED`, `NOT_ALLOWED`.

Statuts **finaux** : `SUCCESS`, `FAILED`, `TRANSACTION_EXIST`, `INSUFFICIENT_BALANCE`.
Tout le reste = encore en vol (→ `PROCESSING` / `REQUIRES_ACTION`).

## 4. Pays & réseaux — et WAVE ✅

| Pays | Code | Opérateurs (`payment_method`) |
|---|---|---|
| Côte d'Ivoire | CI | OM_CI, MOOV_CI, MTN_CI, **WAVE_CI** |
| Burkina Faso | BF | OM_BF, MOOV_BF, **WAVE_BF** |
| Mali | ML | OM_ML, MOOV_ML |
| Sénégal | SN | OM_SN, FREE_SN, EXPRESSO_SN, **WAVE_SN** |
| Togo | TG | MOOV_TG, TMONEY_TG |
| Guinée | GN | OM_GN, MTN_GN |
| Cameroun | CM | OM_CM, MTN_CM |
| Bénin | BJ | MOOV_BJ, MTN_BJ |
| RDC | CD | OM_CD, AIRTEL_CD, MPESA_CD, AFRICELL_CD |
| Niger | NE | AIRTEL_NE, MOOV_NE, ZAMANI_NE |

**Wave figure explicitement dans le catalogue (CI/SN/BF)** — c'est le premier
agrégateur évalué à lister Wave nativement. MAIS : « listé dans le SDK » ≠ « activé
sur le compte ». À **vérifier en direct** (via `/v1/balances` puis un paiement réel
`WAVE_CI`) avant toute affirmation — ne pas répéter le piège Wave des autres.

## 5. Webhooks

- `parse_notification` extrait : `notify_token`, `transaction_id`,
  `merchant_transaction_id`, `user` (optionnel).
- `verify_notification` : **PAS de signature HMAC avec secret**. C'est une simple
  comparaison *timing-safe* (`hmac.compare_digest`) entre le `notify_token` qu'on a
  stocké à l'initiation et celui renvoyé dans le webhook. → il faut persister le
  `notify_token` reçu à l'init et le rapprocher à la réception.
- Non vérifié en pratique (aucun webhook reçu). Comme pour PawaPay, la confirmation
  passera par `verify_payment`/`verify_payout` + réconciliation (gate conformité BCEAO,
  SCRUM-555) plutôt que par le webhook dans un premier temps.

## 6. Bornes de montant

- Asserts côté SDK : paiement 100 – 2 500 000 ; transfert 500 – 1 500 000 (XOF).
- ⚠️ **Non appliqué côté API comme annoncé** : en direct, une collecte à `amount=10`
  (sous le mini SDK de 100) a été **acceptée** par `/v1/payment` (transaction créée), pas
  rejetée pour borne. Ne pas se fier aux bornes SDK comme garde-fou serveur.
- Devises : XOF (+ XAF/CDF/GNF selon pays).

## 7. Tableau comparatif

| Capacité | CinetPay v1 | PawaPay | PayDunya | FeexPay |
|---|---|---|---|---|
| Collecte page hébergée (REDIRECT) | ✅ testé live (`channel=ALL`) | ✅ Payment Page | — | — |
| Push direct mobile money | ⚠️ `channel=MOBILE_MONEY` → OPERATION_ERROR sur ce compte | ✅ /v2/deposits | — | — |
| Payout | ✅ /v1/transfer testé live (endpoint vivant, sans « add contact ») | ✅ /v2/payouts | ⚠️ pas de sandbox décaissement | — |
| Remboursement | ❌ absent | ✅ /v2/refunds | — | — |
| Wave | ✅ accepté live (WAVE_CI, transfert + page hébergée) | ❌ pas actif sur le compte | documenté, non vérifié | documenté, non vérifié |
| Signature webhook | token echo (compare_digest) | — (non câblé) | — | — |
| Sandbox clair | ✅ préfixe `sk_test_` | ✅ host sandbox | partiel | — |

(PayDunya/FeexPay : lignes à compléter depuis l'historique de session ou l'opérateur.)

## 8. BLOCAGE pour les tests en direct — prouvé empiriquement

Les étapes 3–10 du handoff (solde, collecte réelle, échec volontaire, payout réel,
vérif Wave en direct) exigent un JWT, obtenu uniquement par `POST /v1/oauth/login`.
**Testé en direct contre le sandbox `api.cinetpay.net`** (clé seule, pas de password) :

| Corps envoyé à `/v1/oauth/login` | Réponse réelle |
|---|---|
| `{api_key, api_password: ""}` | `422` · `1004` · `INVALID_PARAMS` |
| `{api_key}` (sans le champ) | `422` · `1004` · `INVALID_PARAMS` |
| `{api_key, api_password: "<faux>"}` | `422` · `1005` · `INVALID_CREDENTIALS` |

Interprétation : champ manquant/vide → `1004` (requête malformée) ; champ présent
mais faux → `1005` (couple validé). Donc **l'`api_password` est structurellement
obligatoire** : la clé seule ne peut pas authentifier, et tout le reste (balance,
payment, transfer) dépend du Bearer issu de ce login.

### Mise à jour : couple api_key+api_password valide, mais IP non whitelistée

Un `api_password` a ensuite été fourni. Testé en direct, contrôle A/B même IP, même instant :

| Couple envoyé | Réponse réelle |
|---|---|
| `api_key` + faux password | `422` · `1005` · `INVALID_CREDENTIALS` |
| `api_key` + password fourni | `422` · `2011` · `NOT_ALLOWED` · « This Ip is not whitelisted » |

Le faux password renvoie toujours `1005` depuis cette même IP : la validation des
identifiants passe **avant** le contrôle d'IP. Donc **le couple fourni est valide**
(il franchit la validation des identifiants). Le seul blocage restant est le
**whitelisting d'IP** côté compte CinetPay : l'IP d'où partent les appels doit être
autorisée dans le back-office CinetPay.

→ **Action opérateur** : ajouter à la liste blanche d'IP du compte sandbox soit l'IP
du serveur de staging (c'est de là que partiront les vrais appels), soit l'IP de la
machine de test pour valider depuis le poste. Une fois l'IP autorisée, dérouler les
étapes 3–10 (solde → collecte → échec → payout → Wave). Le password n'est stocké
nulle part dans le dépôt (variable d'environnement uniquement).

Pays cible confirmé : **CI**.

## 9. Intégration DiddiPay — faite

- `payfund_app/modules/payments/infra/cinetpay_processor.py` (modèle `pawapay_processor.py`).
- Capacités : `currencies={XOF}`, `directions={COLLECTION, PAYOUT}` (**pas REFUND**),
  `channels={mobile_money}`, `networks=frozenset()` (page hébergée = choix du payeur ;
  réseaux payout validés en interne, incl. Wave).
- Collecte → `payment_url` → `NextActionType.REDIRECT`. **channel envoyé = `ALL`** (voir §10).
- `refund_payment` → `NotImplementedError` (pas d'API). `parse_webhook` → `NotImplementedError` (BCEAO).
- Enregistré dans `presentation/deps.py::get_processor_registry()`, conditionné par
  `CINETPAY_API_KEY` **et** `CINETPAY_API_PASSWORD`, **priorité 110** (sous Paystack 100 /
  PawaPay 50 → jamais choisi par défaut, utilisé sur sélection explicite ou en repli payout
  Wave). `networks` vide → ne détourne aucun réseau dédié d'un autre processeur.
- Config : `cinetpay_api_key/_api_password/_base_url/_country` dans `core/config.py`.
- Tests : `tests/test_cinetpay_processor.py` (28 tests). Suite complète : **444 passent**.
- `DiddiPay_Contrat_API.md` / payouts contract : **inchangés** — le comportement visible
  (REDIRECT) est identique à Paystack, aucun nouveau champ côté appelants.
- Pas encore poussé/déployé. Ne jamais redéployer soi-même via Portainer.

## 10. Tests en direct — résultats (sandbox, depuis staging `169.58.70.37`)

Exécutés depuis le conteneur `diddi-pay-backend-payment-worker-1` (son IP whitelistée),
scripts d'exploration dans le scratchpad (jamais commités, creds via variables d'env).

| Étape | Endpoint | Résultat réel |
|---|---|---|
| Auth | `POST /v1/oauth/login` | ✅ `200` · JWT `bearer` · `expires_in: 86400` (24 h, pas 23) · compte `direction.technique@diddifree.com` |
| Solde | `GET /v1/balances` | ✅ `200` · `{real_balance:0, available_balance:0, currency:XOF}` (champs **au top-level**, pas sous `data`) |
| Collecte (page) | `POST /v1/payment` channel=`ALL` | ✅ `200` · `payment_url: https://secure.cinetpay.net/checkout/{payment_token}` · `details.status=INITIATED` · `must_be_redirected:true` |
| Collecte channel=`MOBILE_MONEY` | idem | ❌ `422` · `OPERATION_ERROR` « contact admin at paymetrust.net » — **ce channel ne marche pas sur ce compte** |
| Vérif (impayé) | `GET /v1/payment/{merchant_id}` | ✅ `200` · plat `{code:2001, status:INITIATED, transaction_id, user:null}` |
| Échec forcé (id invalide) | `POST /v1/payment` | `200` top-level `OK` mais `details.code:1004 INVALID_PARAMS` → **le vrai statut est dans `details`** ; `merchant_transaction_id` a un **format restreint** (pas d'underscore ; UUID OK) |
| Payout OM_CI | `POST /v1/transfer` | ✅ endpoint vivant · `200` · `status:FAILED` + `details.status:INSUFFICIENT_BALANCE` (solde 0) · `fee_amount:10` · **pas d'étape « add contact » requise** |
| Payout WAVE_CI | `POST /v1/transfer` | ✅ **WAVE_CI accepté** (atteint le contrôle de solde, même INSUFFICIENT_BALANCE) → Wave configuré sur le compte |

**Corrections apportées au code suite au direct :**
1. **channel = `ALL`** (et non `MOBILE_MONEY`, qui renvoie `OPERATION_ERROR`) — c'est `ALL`
   qui sert la page hébergée et laisse le payeur choisir MM/Wave/carte.
2. **Parsing `details`** : le top-level est quasi toujours `200/OK` ; le vrai état
   (INITIATED/FAILED/INVALID_PARAMS/INSUFFICIENT_BALANCE + message) est dans
   `details.{status,message}`. init/verify/payout lisent désormais `details` en priorité.
3. `transaction_id` CinetPay est **au top-level** (ex. `ER-T-...`) → utilisé comme référence.
4. Montants en francs entiers XOF (pas de ×100), confirmé (`amount:500`, `fee_amount:10`).

**Test collecte Wave réel (numéro `0124565432` → `2250124565432`)** : `POST /v1/payment`
avec `payment_method=WAVE_CI` → `200`, **`payment_url` émis**, `transaction_id` créé, et
la vérif renvoie `status:INITIATED` + **`payment_method:WAVE_CI`** → la transaction existe
bien et est liée à Wave. Le `details` de l'init affichait `FAILED / "Paiement échoué"` car
épingler `payment_method`+`client_phone_number` déclenche une **tentative de push Wave direct**
vers le numéro de test (non approvisionné, aucune autorisation Wave humaine) ; l'URL de
checkout reste valide et la vérif reste `INITIATED`. → Wave confirmé bout-en-bout côté
initiation. Note d'intégration : le processeur DiddiPay n'épingle PAS `payment_method` — il
envoie `channel=ALL` (page hébergée, le payeur choisit Wave sur la page), ce qui donne un
`INITIATED` + redirect propre, sans la tentative de push direct.

**Non vérifié (impossible en l'état) :** settlement complet d'une collecte (nécessite qu'un
client paye/autorise réellement sur Wave) et d'un payout (nécessite d'approvisionner le solde
sandbox). Les deux endpoints sont prouvés vivants et corrects jusqu'au point de blocage.

**Webhook signature** : toujours non vérifié en pratique (aucun webhook reçu) ; de toute
façon ce n'est qu'un echo de `notify_token`, confirmation par `verify_*` + réconciliation.
