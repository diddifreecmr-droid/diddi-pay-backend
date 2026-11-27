# Integration DiddiSend / DiddiPay

## Verification d'un paiement DiddiShop ou DiddiFood

DiddiSend ne lit pas directement les PaymentIntents DiddiShop avec la route proprietaire :

```text
GET /payment-intents/{intent_id}
```

Cette route est volontairement limitee au `client_id` qui a cree l'intent et retourne `404` pour
un autre proprietaire.

Pour valider un trajet ou une livraison payee par DiddiShop ou DiddiFood, DiddiSend utilise :

```http
POST /payfund/v1/payment-intents/{payment_intent_id}/verify
Authorization: Bearer <service-token-diddifreeid>
X-Client-ID: diddisend-staging
Content-Type: application/json
```

Le token doit viser `aud=diddipay` et porter uniquement le scope necessaire :

```text
diddipay:payment-intents:verify
```

Corps :

```json
{
  "amount": 12000,
  "currency": "XOF",
  "business_reference": "diddish:order:order-123"
}
```

DiddiPay retourne `200` et `verified=true` uniquement si :

- le PaymentIntent existe et appartient a DiddiShop ou DiddiFood, les deux owners autorises pour
  DiddiSend en staging ;
- son statut est `succeeded` ;
- sa devise est `XOF` ;
- son montant correspond exactement ;
- sa `business_reference` correspond exactement.

Sinon :

- `404 PAYMENT_INTENT_NOT_FOUND` si l'intent n'existe pas ou n'appartient pas a un owner
  autorise ;
- `409 PAYMENT_INTENT_NOT_VERIFIED` si le paiement existe mais n'est pas final ou ne correspond
  pas aux valeurs attendues.

DiddiSend peut rejouer cette verification avec le meme `payment_intent_id` et les memes valeurs,
sans creer un nouveau paiement. Aucun secret webhook DiddiShop ou DiddiFood n'est necessaire pour
cet appel.
