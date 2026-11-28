# Remboursements et événements métier

## Contrat commun à tous les modules

DiddiPay est la source de vérité de l'état du remboursement, quel que soit le module
propriétaire du paiement (`diddigo`, `diddishop`, `diddifood`, `diddisend` ou un futur
module) et quel que soit le processeur utilisé. Lorsqu'un remboursement est demandé par
le module propriétaire ou par une demande déléguée, DiddiPay publie dans son outbox un
événement adressé au `client_id` propriétaire du `PaymentIntent`.

Événements :

- `diddipay.refund.succeeded.v1` : remboursement confirmé par DiddiPay ;
- `diddipay.refund.failed.v1` : processeur ou validation finale en échec ;
- `diddipay.refund.unknown.v1` : appel processeur incertain, résultat à réconcilier.

Exemple de payload métier (sans donnée personnelle) :

```json
{
  "event_version": "1",
  "refund_id": "uuid",
  "payment_intent_id": "uuid",
  "owner_client_id": "diddishop-staging",
  "business_reference": "diddishop:order:123",
  "amount": 2500,
  "currency": "XOF",
  "status": "succeeded",
  "provider_status": "processed",
  "provider_reference": "provider-reference",
  "processor": "paystack",
  "reason": "Order cancelled",
  "occurred_at": "2026-10-10T12:00:00+00:00"
}
```

`amount` est le montant du remboursement confirmé par DiddiPay, en unité entière XOF.
`business_reference` est celui du `PaymentIntent` et permet au module de retrouver sa
commande, livraison ou course. Le module ne doit émettre `order.refunded` qu'à la
réception de `diddipay.refund.succeeded.v1`; les états `failed` et `unknown` ne sont pas
des succès métier.

La livraison est au moins une fois. Le consommateur doit dédupliquer par l'identifiant
de l'enveloppe d'événement et répondre en 2xx uniquement après enregistrement durable.
Une relivraison est normale. La demande de remboursement reste protégée par
`Idempotency-Key`, et un replay identique ne crée pas de nouvel événement métier.

Le module propriétaire reçoit l'événement via `PAYMENT_CALLBACK_TARGETS` ou le transport
Redis Stream configuré. DiddiPay ne diffuse pas un remboursement à tous les modules :
le destinataire est le propriétaire du `PaymentIntent`.

## Parcours

1. DiddiSupport demande le contexte du `payment_intent_id`.
2. L'opérateur vérifie l'identité du client et le montant attendu.
3. DiddiSupport envoie une demande avec une clé d'idempotence.
4. DiddiPay crée une action `pending` et traite le remboursement de façon asynchrone.
5. DiddiSupport interroge l'action ou reçoit son événement d'audit signé de résultat.
6. Le module propriétaire reçoit en parallèle l'événement métier standard décrit ci-dessus.

## Garanties

- Le montant est exprimé dans l'unité mineure de la devise.
- Une même clé d'idempotence ne doit pas créer deux remboursements.
- Un résultat `unknown` ne doit jamais être transformé automatiquement en succès.
- Chaque callback porte `X-Request-ID` et est signé avec le secret de la cible.
- Les événements sont publiés par l'outbox après validation de la transaction.

## Événements d'audit Support

- `diddipay.refund.succeeded.v1`
- `diddipay.refund.failed.v1`
- `diddipay.refund.unknown.v1`

Voir le [contrat DiddiSupport](../integrations/diddisupport.md) pour les scopes et les champs.
