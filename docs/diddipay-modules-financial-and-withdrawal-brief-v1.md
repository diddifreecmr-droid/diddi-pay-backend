# DiddiPay et modules metiers - contrat financier et retraits v1

**Statut :** brief d'alignement avant implementation

**Perimetre :** DiddiPay, DiddiGo, DiddiFood, DiddiSend et DiddiAdmin/Pilotage

**Devise MVP :** XOF entier

## 1. Decision produit

DiddiPay orchestre les paiements externes et les payouts externes.

Chaque module metier reste proprietaire de :

- ses paiements metier ;
- son wallet metier ;
- son historique ;
- ses commissions ;
- ses regles d'eligibilite ;
- ses encours cash.

Le cash ne cree pas de `PaymentIntent` DiddiPay. Il est confirme et enregistre par le module qui connait le contexte metier.

## 2. Definition de l'encours DiddiSend

Pour une livraison DiddiFood avec paiement `cash_on_delivery`, l'encours est le montant brut effectivement collecte par le livreur.

```text
encours_cash_brut = cash_collecte_par_le_livreur - cash_remis_par_le_livreur
```

L'encours ne deduit pas les commissions DiddiFood.

Exemple :

- commande et livraison payees en especes : `20 000 XOF` ;
- commission DiddiFood : `2 000 XOF` ;
- cash remis par le livreur : `0 XOF` ;
- encours cash : `20 000 XOF` ;
- commission due sur le ledger du livreur : `2 000 XOF`.

La commission est une ecriture distincte. Elle peut etre prelevee sur le ledger du livreur, un revenu futur ou un top-up. Elle ne reduit jamais l'encours cash physique a remettre.

Cette politique s'applique uniquement au cash DiddiFood identifie comme `cash_on_delivery`. Un paiement cash DiddiGo ou un autre cash metier est historise par son module, mais ne devient pas automatiquement un encours DiddiSend.

## 3. Ecriture financiere commune

Le premier format est le contrat financier commun utilise par les routes internes et les historiques de ledger.

```json
{
  "entry_id": "uuid",
  "account_ref": "diddisend:courier:789",
  "service": "diddisend",
  "flow_type": "customer_payment",
  "direction": "credit",
  "state": "posted",
  "amount": 12000,
  "currency": "XOF",
  "source": {
    "type": "delivery",
    "id": "delivery-123"
  },
  "payment_source": {
    "channel": "cash",
    "network": null,
    "processor": null
  },
  "payment_intent_id": null,
  "related_entry_id": null,
  "business_reference": "diddisend:delivery:123",
  "idempotency_key": "diddisend:delivery:123:customer-payment",
  "occurred_at": "2026-10-10T12:00:00Z"
}
```

Valeurs minimales de `flow_type` :

```text
customer_payment
cash_collection_due
cash_remittance
earnings
food_commission
topup
withdrawal_reserved
withdrawal_succeeded
withdrawal_released
refund
adjustment
```

Une remise cash et une commission ne modifient pas l'ecriture originale du paiement. Elles produisent des ecritures compensatoires reliees par `related_entry_id`.

## 4. Routes par module

### DiddiPay

Base staging : `https://pay-api-staging.diddifree.com/payfund/v1`

| Route | Use case | Responsabilite |
|---|---|---|
| `POST /payment-intents` | `CreatePaymentIntent` | Creer un paiement externe electronique |
| `GET /payment-intents/{intent_id}` | `GetPaymentIntent` | Lire le statut financier externe |
| `POST /payments/webhooks/paystack` | `ReceiveProviderWebhook` | Recevoir et traiter le webhook Paystack |
| `GET /payment-intents/{intent_id}/financial-summary` | `GetPaymentFinancialSummary` | Lire captures, frais, remboursements et settlements |
| `POST /payouts` | `CreatePayout` | Demander un retrait externe pour un module metier |
| `GET /payouts/{payout_id}` | `GetPayout` | Lire le statut d'un retrait |
| `GET /payouts/{payout_id}/financial-summary` | `GetPayoutFinancialSummary` | Lire les frais et mouvements du payout |
| `GET /internal/pilotage/accounting-summary` | `GetAccountingSummary` | Exporter les ecritures DiddiPay vers Pilotage |

DiddiPay ne debite jamais directement le wallet d'un module. Il confirme le payout, puis le module applique ses propres ecritures de reservation et de succes.

### DiddiGo

Base staging : `https://go-staging.diddifree.com`

| Route | Use case | Responsabilite |
|---|---|---|
| `POST /v1/payments/{ride_id}/prepare` | `PrepareRidePayment` | Preparer un paiement electronique ou local |
| `POST /v1/payments/{ride_id}/confirm-cash` | `ConfirmRideCash` | Confirmer un paiement cash DiddiGo |
| `GET /v1/payments/{ride_id}` | `GetRidePayment` | Retourner le paiement de la course |
| `GET /v1/drivers/me/wallet/ledger` | `GetDriverLedger` | Retourner l'historique financier du chauffeur |
| `POST /v1/drivers/me/wallet/withdrawals` | `RequestDriverWithdrawal` | Nouvelle route a implementer |
| `GET /v1/drivers/me/wallet/withdrawals/{id}` | `GetDriverWithdrawal` | Nouvelle route a implementer |
| `GET /internal/pilotage/finance-summary` | `GetDiddiGoFinanceSummary` | Exporter les paiements cash, electroniques, commissions et revenus |

Le cash DiddiGo reste dans DiddiGo. Il ne passe pas par DiddiPay et ne devient pas automatiquement un encours DiddiSend.

### DiddiFood

Base staging : `https://diddifood-backend-staging.diddifree.com`

| Route | Use case | Responsabilite |
|---|---|---|
| `POST /food/v1/orders/{order_id}/payments/prepare` | `PrepareOrderPayment` | Creer le PaymentIntent electronique via DiddiPay |
| `POST /food/v1/restaurants/{restaurant_id}/orders/{order_id}/payments/confirm-cash` | `ConfirmOrderCash` | Confirmer le cash a la livraison |
| `GET /food/v1/orders/{order_id}/payment` | `GetOrderPayment` | Retourner le paiement de la commande |
| `GET /food/v1/restaurants/{restaurant_id}/wallet/entries` | `ListRestaurantWalletEntries` | Historique du wallet restaurant |
| `GET /food/v1/restaurants/{restaurant_id}/wallet/withdrawal-quote` | `QuoteRestaurantWithdrawal` | Calculer frais et montant net |
| `POST /food/v1/restaurants/{restaurant_id}/wallet/withdrawals` | `RequestRestaurantWithdrawal` | Reserver le solde et demander un payout DiddiPay |
| `GET /food/v1/restaurants/{restaurant_id}/wallet/withdrawals/{id}` | `GetRestaurantWithdrawal` | Lire le retrait |
| `GET /food/v1/internal/pilotage/finance-summary` | `GetDiddiFoodFinanceSummary` | Exporter paiements, cash, commissions et payouts |

DiddiFood possede deja le parcours de retrait dans l'API staging. Il faut maintenant verifier son raccordement complet a `POST /payouts` DiddiPay, notamment reservation, callback, reconciliation et liberation en cas d'echec.

### DiddiSend

Base staging : `https://diddisend-api-staging.diddifree.com`

| Route | Use case | Responsabilite |
|---|---|---|
| `POST /v1/deliveries/{delivery_id}/payment-intent` | `RequestDeliveryPayment` | Demander un paiement electronique via DiddiPay |
| `POST /v1/admin/deliveries/{delivery_id}/wallet-settlement` | `SettleDelivery` | Calculer le settlement de livraison |
| `POST /v1/couriers/me/wallet/remittances` | `CreateCourierRemittance` | Declarer une remise d'argent cash |
| `POST /v1/couriers/me/wallet/remittances/{id}/reconcile` | `ReconcileCourierRemittance` | Confirmer et appliquer la remise |
| `GET /v1/couriers/me/wallet/entries` | `GetCourierLedger` | Retourner l'historique du wallet coursier |
| `POST /v1/couriers/me/wallet/withdrawals` | `RequestCourierWithdrawal` | Nouvelle route a implementer |
| `GET /v1/couriers/me/wallet/withdrawals/{id}` | `GetCourierWithdrawal` | Nouvelle route a implementer |
| `GET /internal/pilotage/finance-summary` | `GetDiddiSendFinanceSummary` | Exporter settlements, encours, remises, commissions et retraits |

`remittance` et `withdrawal` sont deux operations opposees :

- `remittance` : le livreur remet a la plateforme le cash collecte ;
- `withdrawal` : la plateforme paie au livreur une somme disponible dans son wallet.

Elles ne doivent jamais partager le meme statut ni la meme idempotency key.

## 5. Flux de paiement electronique

```text
DiddiGo / DiddiFood / DiddiSend
    -> POST DiddiPay /payment-intents
    -> Paystack
    -> webhook ou reconciliation DiddiPay
    -> payment.succeeded
    -> module metier
    -> ecriture customer_payment + earnings
```

Le module metier reste la source de verite de l'effet metier. DiddiPay reste la source de verite du paiement externe.

## 6. Flux cash DiddiFood

```text
Client choisit cash_on_delivery
    -> DiddiFood confirme le paiement cash
    -> DiddiFood poste customer_payment
    -> DiddiSend enregistre cash_collection_due brut
    -> DiddiFood commission est postee separement sur le ledger du livreur
    -> livreur remet le cash
    -> DiddiSend poste cash_remittance
    -> encours cash diminue
```

Le montant brut du cash collecte ne doit pas etre reduit de la commission DiddiFood.

## 7. Regles de retrait

Avant de demander un retrait, le module metier doit :

1. verifier que le solde est `available` ;
2. verifier que le montant est en XOF entier ;
3. reserver le montant dans son propre ledger ;
4. creer une cle d'idempotence stable ;
5. appeler `POST /payfund/v1/payouts` avec un `business_reference` metier ;
6. attendre le callback ou executer la reconciliation ;
7. transformer `withdrawal_reserved` en `withdrawal_succeeded` ;
8. liberer la reservation en cas d'echec definitif.

Un payout `processing` ne doit jamais etre considere comme paye.

## 8. Pilotage et historiques

Les routes suivantes doivent exposer des `entries[]` conformes au premier format :

```text
DiddiPay  GET /payfund/v1/internal/pilotage/accounting-summary
DiddiGo   GET /internal/pilotage/finance-summary
DiddiFood GET /food/v1/internal/pilotage/finance-summary
DiddiSend GET /internal/pilotage/finance-summary
```

Les routes de paiement par objet metier peuvent continuer a retourner leur format actuel. Elles doivent seulement conserver les references communes :

```text
business_reference
payment_intent_id si paiement electronique
channel
network
processor
status
```

Les historiques frontend utilisent les routes du module metier. DiddiAdmin/Pilotage utilise les routes internes de resume et ne lit jamais les bases des modules directement.

## 9. Ordre d'implementation

### Sprint 1 - Contrat financier commun

- Ajouter le schema `FinancialEntry` aux contrats internes.
- Fixer `flow_type`, `payment_source`, `state` et `related_entry_id`.
- Ne pas casser les reponses frontend existantes.

### Sprint 2 - Encours DiddiSend

- Limiter l'exposition aux livraisons DiddiFood `cash_on_delivery`.
- Calculer l'encours sur le cash brut collecte.
- Sortir les commissions DiddiFood dans des ecritures separees.
- Ajouter les tests top-up, remise cash, commission et seuil d'encours.

### Sprint 3 - Payout DiddiFood

- Verifier le parcours existant quote -> reserve -> DiddiPay payout.
- Tester callback, reconciliation, succes, echec et liberation de reservation.

### Sprint 4 - Payout DiddiGo et DiddiSend

- Ajouter les routes de retrait manquantes.
- Reutiliser le contrat DiddiPay `Payout`.
- Implementer les ecritures `withdrawal_reserved`, `withdrawal_succeeded` et `withdrawal_released`.

### Sprint 5 - Pilotage et recette staging

- Ajouter `entries[]` dans les resumes financiers internes.
- Comparer cash, mobile money, commissions, remises et retraits.
- Tester les doublons, timeouts, callbacks manques et reconciliation.

## 10. Regles de securite

- Les frontends n'appellent jamais DiddiPay directement.
- Les appels module -> DiddiPay utilisent S2S et idempotence.
- Aucun module ne lit la base d'un autre module.
- Aucun retrait ne devient `succeeded` sur la seule reponse HTTP initiale.
- Les commissions ne sont jamais absorbees dans l'encours cash brut.
- Les ecritures sont immuables ; les corrections sont des ecritures compensatoires.
