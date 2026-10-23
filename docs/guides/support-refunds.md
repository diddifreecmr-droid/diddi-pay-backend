# Support et remboursements

## Parcours

1. DiddiSupport demande le contexte du `payment_intent_id`.
2. L'opérateur vérifie l'identité du client et le montant attendu.
3. DiddiSupport envoie une demande avec une clé d'idempotence.
4. DiddiPay crée une action `pending` et traite le remboursement de façon asynchrone.
5. DiddiSupport interroge l'action ou reçoit l'événement signé de résultat.

## Garanties

- Le montant est exprimé dans l'unité mineure de la devise.
- Une même clé d'idempotence ne doit pas créer deux remboursements.
- Un résultat `unknown` ne doit jamais être transformé automatiquement en succès.
- Chaque callback porte `X-Request-ID` et est signé avec le secret de la cible.
- Les événements sont publiés par l'outbox après validation de la transaction.

## Événements

- `diddipay.refund.succeeded.v1`
- `diddipay.refund.failed.v1`
- `diddipay.refund.unknown.v1`

Voir le [contrat DiddiSupport](../integrations/diddisupport.md) pour les scopes et les champs.
