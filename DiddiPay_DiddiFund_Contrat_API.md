# DiddiPay / DiddiFund — Contrat API

> **Contrat legacy wallet.** Ce document reste applicable aux routes `/wallet/*` et aux parcours
> DiddiFund historiques qui utilisent un solde interne. Pour toute nouvelle collecte externe,
> utiliser `DiddiPay_Contrat_API.md` (PaymentIntent) et `DiddiFund_Contrat_API.md`. DiddiPay ne doit
> plus être présenté comme synonyme du wallet.

**Destiné à :** Frontend/Mobile, et aux modules backend consommant `WalletServicePort` en interne.
**Base URL (dev) :** `https://api-dev.diddifree.app/payfund/v1`
**Format :** JSON exclusivement · `Content-Type: application/json`
**Référence architecture :** `DiddiPay_DiddiFund_Architecture.md`
**Dépendance :** `Authorization: Bearer <access_token>` sur toutes les routes — token émis par
DiddiFreeID, vérifié localement (voir `DiddiFreeID_Contrat_API.md`).

---

## 0. Conventions générales

Reprennent celles déjà en usage dans l'écosystème (format d'erreur, codes HTTP, pagination — voir
`DiddiGo_Contrat_API.md` pour le détail, non dupliqué ici).

### En-tête obligatoire sur toute route qui déplace des fonds

```
Idempotency-Key: <uuid généré côté appelant>
```
Si la même clé est envoyée deux fois, la deuxième requête renvoie le résultat de la première sans
rejouer l'opération (voir architecture, section 4). Le frontend doit générer cette clé une seule fois par
intention utilisateur (ex. au moment où l'utilisateur appuie sur "confirmer le paiement"), pas à chaque
tentative réseau.

### Montants

`NUMERIC`, unité entière XOF, jamais de centimes — identique à la convention DiddiGo.

---

## 1. Module `wallet`

### `GET /wallet/balance`

**Réponse `200`**
```json
{ "account_id": "a1b2...", "balance": 45000, "currency": "XOF", "status": "active" }
```
Le frontend ne crée pas le wallet explicitement. Le flux attendu est:
1. login via DiddiFreeID,
2. appel de `GET /wallet/balance`,
3. affichage du solde.
Si le provisioning événementiel a été manqué, le backend self-heal le wallet au premier accès
authentifié. La route ops de backfill reste disponible pour corriger un compte en support.

Le wallet personnel est le compte de l'utilisateur final. Les comptes marchands
sont distincts et appartiennent au module consommateur qui les utilise
(`DiddiGo`, `Shop`, ou futurs modules).

---

### `POST /wallet/deposit`

Initie un dépôt via un opérateur Mobile Money.

**Requête**
```json
{ "provider": "orange_money", "amount": 5000, "phone": "+2250700000000" }
```

**Réponse `202`** (traitement asynchrone côté opérateur)
```json
{ "transaction_id": "t9f1...", "status": "pending" }
```
Le front doit interroger `GET /wallet/transactions/{transaction_id}` ou écouter une notification push
pour connaître l'issue (`completed` ou `failed`).

**Erreurs** : `422` (`INVALID_AMOUNT`), `502` (`GATEWAY_UNAVAILABLE`)

---

### `POST /wallet/withdraw`

**Requête** : `{ "provider": "paystack", "amount": 3000, "phone": "+2250700000000", "pin": "1234" }`
**Réponse `202`** : même format que le dépôt.
**Erreurs** : `409` (`INSUFFICIENT_BALANCE`), `502` (`GATEWAY_UNAVAILABLE`)

---

### `POST /wallet/transfer`

Transfert P2P.

**Requête normale** : `{ "recipient_phone": "+2250701111111", "amount": 2000, "pin": "1234" }`

Si DiddiPay répond `STEP_UP_OTP_REQUIRED`, le frontend obtient auprès de DiddiFreeID une preuve
signée avec `purpose=wallet.transfer.high_value`, puis rejoue avec
`"step_up_token": "<jwt-court-diddifreeid>"`. Le code OTP brut n'est jamais envoyé à DiddiPay.
**Réponse `201`**
```json
{ "transaction_id": "t9f2...", "status": "completed", "amount": 2000, "currency": "XOF" }
```
Contrairement au dépôt/retrait (dépendant d'un opérateur externe), un transfert P2P interne est
synchrone — les deux écritures de ledger sont commises dans la même transaction DB.

**Erreurs** : `404` (`RECIPIENT_NOT_FOUND`), `409` (`INSUFFICIENT_BALANCE`, `PIN_REQUIRED`,
`STEP_UP_OTP_REQUIRED`, `STEP_UP_PROOF_ALREADY_USED`), `403` (`INVALID_PIN`,
`STEP_UP_PROOF_INVALID`), `410` (`STEP_UP_PROOF_EXPIRED`),
`422` (`CANNOT_TRANSFER_TO_SELF`)

---

### `POST /wallet/pay/merchant`

Paiement marchand par QR Code (le frontend scanne, obtient un `merchant_account_id` encodé dans le QR).

**Requête** : `{ "merchant_account_id": "m4d5...", "amount": 1500, "pin": "1234", "origin_module": "shop" }`
**Réponse `201`** : même format que transfert.
`origin_module` permet le filtrage d'historique par module d'origine demandé dans le cahier des charges
(UX : "historique filtrable par module d'origine").

Le module qui initie le paiement fournit seulement le contexte métier
(`origin_module`, `business_reference`), pas un solde local.

**Erreurs** : `404` (`MERCHANT_NOT_FOUND`), `409` (`INSUFFICIENT_BALANCE`)

---

### Step-up d'un transfert sensible

Il n'existe plus de route OTP locale DiddiPay. Le challenge et sa vérification appartiennent à
DiddiFreeID. DiddiPay ne reçoit que le JWT court signé dans `step_up_token`, vérifie le JWKS, le
`sub`, le `purpose`, la durée de vie et le `jti`, puis consomme ce `jti` une seule fois.

---

### `POST /wallet/pin/reset`

Réinitialise le PIN avec un code de récupération.

**Requête** : `{ "recovery_code": "abc...", "new_pin": "2468", "confirm_new_pin": "2468" }`
**Réponse `200`** : PIN réinitialisé.

---

### `POST /wallet/ops/pin/reset`

Réinitialisation support/ops avec audit.

**Requête** : `{ "user_id": "u123...", "new_pin": "1111", "confirm_new_pin": "1111", "reason": "support recovery" }`
**Réponse `200`** : PIN réinitialisé et audité.

---

### `GET /wallet/transactions?origin_module=shop&page=1&page_size=20`

Historique paginé, filtrable par `origin_module`, `type`, `from_date`, `to_date`.

```json
{
  "data": [
    { "id": "t9f2...", "type": "merchant_payment", "amount": 1500, "currency": "XOF",
      "status": "completed", "origin_module": "shop", "created_at": "2026-07-28T10:15:00Z" }
  ],
  "pagination": { "page": 1, "page_size": 20, "total_items": 132, "total_pages": 7 }
}
```

### `GET /wallet/transactions/{transaction_id}`

Détail d'une transaction, incluant son `status` à jour — utile pour le polling après un dépôt/retrait en
`pending`.

---

## 2. Module `fund`

### `POST /fund/campaigns`

**Requête**
```json
{ "title": "Extension atelier de couture", "goal_amount": 500000, "currency": "XOF" }
```
**Réponse `201`** : `{ "campaign_id": "c1a2...", "status": "draft" }`
Une campagne créée est en `draft` — elle doit être validée (processus hors contrat public, back-office)
avant de passer `active` et devenir visible aux investisseurs.

---

### `GET /fund/campaigns?status=active&page=1`

Liste paginée des campagnes actives, pour la découverte investisseur.

```json
{
  "data": [
    { "id": "c1a2...", "title": "Extension atelier de couture", "goal_amount": 500000,
      "raised_amount": 210000, "currency": "XOF", "status": "active" }
  ],
  "pagination": { "page": 1, "page_size": 20, "total_items": 34, "total_pages": 2 }
}
```

### `GET /fund/campaigns/{campaign_id}`

Détail complet, y compris la liste des derniers investissements (vue allégée, pas de détail investisseur
individuel au-delà de son nom si l'investisseur a choisi la visibilité publique).

---

### `POST /fund/campaigns/{campaign_id}/invest`

**Requête** : `{ "amount": 10000, "pin": "1234" }`
Appelle en interne `WalletServicePort.encaisser()` pour débiter l'investisseur — atomique avec la
création de l'`investment` (les deux dans la même transaction DB, `fund` et `wallet` étant in-process au
lancement).

Le wallet reste la source de vérité pour les écritures monétaires.

**Réponse `201`**
```json
{ "investment_id": "i7b3...", "campaign_id": "c1a2...", "amount": 10000, "wallet_transaction_id": "t9f3..." }
```

**Erreurs** : `409` (`INSUFFICIENT_BALANCE`, `CAMPAIGN_NOT_ACTIVE`, `CAMPAIGN_GOAL_ALREADY_REACHED`)

---

### `POST /fund/loans/simulate`

Simulateur de prêt — ne crée rien, calcul pur.

**Requête** : `{ "amount": 200000, "duration_months": 6 }`
**Réponse `200`**
```json
{
  "principal": 200000, "duration_months": 6, "monthly_installment": 35500,
  "total_repayable": 213000, "interest_rate_applied": 6.5
}
```
`interest_rate_applied` dépend du Diddi-Score de l'utilisateur courant (déduit du token) — deux
utilisateurs peuvent obtenir des simulations différentes pour le même montant.

---

### `POST /fund/loans`

Demande de prêt réelle. Ne décaisse pas immédiatement — passe par une étape d'évaluation (scoring, voir
architecture section 6).

**Requête** : `{ "amount": 200000, "duration_months": 6 }`
**Réponse `201`** : `{ "loan_id": "l8c4...", "status": "pending" }`

---

### `GET /fund/loans/{loan_id}`

```json
{
  "id": "l8c4...", "status": "repaying", "principal_amount": 200000, "currency": "XOF",
  "disbursed_at": "2026-07-20T09:00:00Z",
  "next_installment": { "due_date": "2026-08-20", "amount_due": 35500, "status": "due" }
}
```

### `GET /fund/loans/{loan_id}/schedule`

Échéancier complet.

```json
{
  "data": [
    { "installment_no": 1, "due_date": "2026-08-20", "amount_due": 35500, "amount_paid": 35500, "status": "paid" },
    { "installment_no": 2, "due_date": "2026-09-20", "amount_due": 35500, "amount_paid": 0, "status": "due" }
  ]
}
```

---

### `POST /fund/loans/{loan_id}/repay`

Remboursement manuel d'une échéance (en plus des prélèvements automatiques éventuels — mécanisme à
préciser avec le produit : prélèvement auto sur solde disponible vs paiement actif par l'emprunteur).

**Requête** : `{ "amount": 35500, "pin": "1234" }`
**Réponse `200`** : échéance mise à jour, appelle `WalletServicePort.decaisser()` côté emprunteur puis
crédite le pool de la campagne correspondante.

Le remboursement débite le wallet emprunteur et crédite le pool, mais le suivi
de l'échéancier et du statut du prêt reste entièrement dans DiddiFund.

**Erreurs** : `409` (`INSUFFICIENT_BALANCE`, `INSTALLMENT_ALREADY_PAID`)

---

## 3. Ce qui n'est volontairement pas encore dans ce contrat

- Génération et vérification de QR Code de paiement (format exact du payload encodé) — à spécifier avec
  Frontend/Mobile une fois le composant scanner choisi.
- Programme de fidélité Diddi-Points / cashback (prévu au cahier des charges, non détaillé ici).
- Relevé de compte exportable en PDF — probablement un endpoint asynchrone (génération différée +
  notification), à spécifier séparément.
- Endpoints d'administration/validation de campagne (`draft → active`) — back-office, hors contrat public.
- Détail du mécanisme de prélèvement automatique des échéances de prêt — décision produit en attente.

Si un de ces points bloque une maquette plus tôt que prévu, à signaler pour ajout proprement documenté
plutôt qu'improvisé côté client.
