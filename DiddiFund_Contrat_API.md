# DiddiFund - Contrat API

**Version :** 3.0 - integration PaymentIntent

**Base URL :** `/payfund/v1`

**Auth utilisateur :** JWT DiddiFreeID verifie localement

Swagger `/payfund/v1/docs` est le contrat HTTP executable. Ce document precise les frontieres
metier et les cycles asynchrones.

## 1. Responsabilites

DiddiFund possede :

- les campagnes ;
- les investissements ;
- les prets et echeanciers ;
- les regles d'eligibilite ;
- le statut metier d'une collecte d'investissement.

DiddiPay possede le `PaymentIntent`, l'idempotence du paiement, le routage Paystack, les webhooks et
la reconciliation. DiddiFreeID possede l'identite globale. Les roles investisseur, porteur de
campagne et emprunteur restent dans DiddiFund.

## 2. Routes campagnes

- `POST /fund/campaigns` : cree une campagne `draft`.
- `GET /fund/campaigns` : liste les campagnes avec pagination et filtre de statut.
- `GET /fund/campaigns/{campaign_id}` : retourne le detail et les investissements recents.

## 3. Investissement externe via DiddiPay

### `POST /fund/campaigns/{campaign_id}/invest/payment`

Initie un investissement paye par un rail externe provider-neutral.

Header obligatoire :

```http
Authorization: Bearer <diddifreeid-jwt>
Idempotency-Key: investment:<stable-client-operation-id>
```

Corps :

```json
{
  "amount": 10000,
  "channel": "mobile_money",
  "network": "orange",
  "customer_email": "investor@example.com",
  "customer_phone": "+2250700000000",
  "callback_url": "https://fund.diddifree.com/payments/return"
}
```

Reponse `201` :

```json
{
  "id": "ec0a6958-8d78-483f-91aa-5289c7dc47dc",
  "operation_type": "investment",
  "business_reference": "fund:investment:campaign-id:key",
  "payment_intent_id": "1fb670ef-157c-453f-96b7-9584fa6e0244",
  "amount": 10000,
  "currency": "XOF",
  "status": "requires_action",
  "next_action": {
    "type": "redirect",
    "url": "https://checkout.paystack.com/example",
    "instructions": null
  }
}
```

Regles :

- la campagne doit etre active ;
- le proprietaire ne peut pas investir dans sa propre campagne ;
- le montant ne peut pas depasser le reste de l'objectif ;
- la meme cle et la meme requete retournent le meme ordre et le meme PaymentIntent ;
- la meme cle avec une autre campagne, un autre utilisateur ou montant retourne
  `IDEMPOTENCY_CONFLICT` ;
- aucun investissement final et aucun `raised_amount` ne sont crees avant confirmation signee.

### `GET /fund/payment-orders/{order_id}`

Retourne l'ordre appartenant a l'utilisateur authentifie. Cette route permet au frontend de relire
l'etat DiddiFund apres un retour checkout. Un autre utilisateur recoit
`PAYMENT_ORDER_NOT_FOUND` sans fuite d'information.

## 4. Callback DiddiPay

### `POST /fund/payments/webhooks/diddipay`

Route interne appelee par le relay DiddiPay. Les headers et le corps sont documentes dans Swagger.

Headers :

```http
X-DiddiPay-Event-ID: <uuid>
X-DiddiPay-Signature: <hmac-sha256-hex-du-corps-brut>
```

Au premier `payment.succeeded` valide, DiddiFund execute dans une transaction SQL :

1. insertion de l'event id dans `fund.payment_event_inbox` ;
2. verrouillage du `payment_order` et de la campagne ;
3. verification de `payment_intent_id`, `business_reference`, montant et devise ;
4. creation de l'investissement lie au `payment_intent_id` ;
5. increment de `campaign.raised_amount` ;
6. passage de l'ordre a `succeeded` ;
7. commit puis reponse `processed`.

Un event id deja traite retourne `duplicate` sans second investissement. Une signature invalide
retourne `401`. Une reference, un montant ou une devise incoherente retourne `409` et ne cree aucun
effet financier.

Secret de reception :

```env
DIDDIFUND_DIDDIPAY_CALLBACK_SECRET=<same-secret-as-diddipay-diddifund-target>
```

## 5. Wallet legacy

### `POST /fund/campaigns/{campaign_id}/invest`

Ancien flux d'investissement depuis un DiddiWallet. Il exige `amount`, `pin` et `Idempotency-Key`,
debite le wallet investisseur et credite le compte technique de campagne atomiquement.

Cette route reste disponible pendant la migration et pourra devenir demain l'adaptateur
`payment_method=wallet` derriere PaymentIntent. Une nouvelle integration Paystack doit utiliser
`/invest/payment`.

## 6. Prets

- `POST /fund/loans/simulate` : simulation sans mouvement d'argent.
- `POST /fund/loans` : creation d'une demande pour le proprietaire de campagne.
- `GET /fund/loans/{loan_id}` : detail du pret.
- `GET /fund/loans/{loan_id}/schedule` : echeancier.
- `POST /fund/loans/{loan_id}/repay` : remboursement wallet legacy avec PIN.

Le decaissement reste une action back-office. Le remboursement externe par PaymentIntent reutilisera
`fund.payment_orders` avec `operation_type=loan_repayment`; il n'est pas expose comme disponible
avant son sprint de livraison.

## 7. Source de verite

- Le statut du rail externe vient de DiddiPay.
- Le statut de l'ordre d'investissement vient de DiddiFund apres callback valide.
- La campagne et son montant leve restent DiddiFund.
- Le retour navigateur Paystack ne confirme rien.
- Une tentative `unknown` ne doit jamais provoquer automatiquement un second debit.

## 8. Erreurs

Format partage :

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Message lisible",
    "details": null
  }
}
```

Codes importants : `CAMPAIGN_NOT_FOUND`, `CAMPAIGN_NOT_ACTIVE`,
`CANNOT_INVEST_IN_OWN_CAMPAIGN`, `CAMPAIGN_GOAL_ALREADY_REACHED`, `IDEMPOTENCY_CONFLICT`,
`PAYMENT_ORDER_NOT_FOUND`, `PAYMENT_REFERENCE_MISMATCH` et `PAYMENT_AMOUNT_MISMATCH`.
