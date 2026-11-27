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

Son identite doit etre celle de DiddiSend : `sub=service:diddisend`. Le header `X-Client-ID`
doit correspondre au `client_id` porte par le meme token. Un token Pilotage, meme signe et avec
le meme scope, n'est pas accepte sur cette route.

Corps :

```json
{
  "amount": 12000,
  "currency": "XOF",
  "business_reference": "diddish:order:order-123"
}
```

Contraintes du corps : `amount` est un entier strictement positif en XOF, `currency` vaut
uniquement `XOF`, et `business_reference` contient entre 1 et 128 caracteres. Les champs
supplementaires sont refuses.

DiddiPay retourne `200` et `verified=true` uniquement si :

- le PaymentIntent existe et appartient a DiddiShop ou DiddiFood, les deux owners autorises pour
  DiddiSend en staging ;
- son statut est `succeeded` ;
- sa devise est `XOF` ;
- son montant correspond exactement ;
- sa `business_reference` correspond exactement.

Réponse de succès exacte :

```json
{
  "verified": true,
  "payment_intent_id": "uuid",
  "owner_client_id": "diddishop-staging-diddipay",
  "business_reference": "diddish:order:order-123",
  "amount": 12000,
  "currency": "XOF",
  "status": "succeeded"
}
```

Erreurs possibles :

| HTTP | Code | Signification |
| --- | --- | --- |
| `401` | `UNAUTHENTICATED` | Token absent, invalide, mauvais `sub` ou mauvais `X-Client-ID`. |
| `403` | `SERVICE_CLIENT_FORBIDDEN` / `SERVICE_SCOPE_REQUIRED` / `PAYMENT_INTENT_VERIFY_NOT_CONFIGURED` | DiddiSend n'est pas autorise ou n'est pas configure. |
| `404` | `PAYMENT_INTENT_NOT_FOUND` | Intent inconnu ou owner non autorise, sans divulguer son existence. |
| `409` | `PAYMENT_INTENT_NOT_VERIFIED` | Le paiement existe mais ne peut pas etre valide. `details.reason` vaut `status_not_succeeded`, `currency_mismatch`, `amount_mismatch` ou `business_reference_mismatch`. |
| `422` | validation FastAPI | UUID, montant, devise, reference ou champs supplementaires invalides. |

DiddiSend peut rejouer cette verification avec le meme `payment_intent_id` et les memes valeurs,
sans creer un nouveau paiement. La verification est en lecture seule : elle ne modifie ni le
PaymentIntent ni son statut. Aucun secret webhook DiddiShop ou DiddiFood n'est necessaire pour
cet appel.
