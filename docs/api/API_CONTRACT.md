# Contrat API

## Authentification

Les routes protégées utilisent `Authorization: Bearer <JWT>`. Le JWT est vérifié localement avec
le JWKS DiddiFreeID. Les appels inter-services utilisent aussi `X-Client-ID` et les scopes
correspondants.

## Routes Support

| Route | Statut | Usage |
| --- | --- | --- |
| `GET /internal/support/payment/{payment_intent_id}/context` | hors MVP | Contexte minimal d'un paiement pour DiddiSupport |
| `POST /internal/support/refund-requests` | hors MVP | Demande idempotente de remboursement |
| `GET /internal/support/refund-requests/{action_id}` | hors MVP | Suivi d'une demande de remboursement |

Le préfixe `/payfund/v1` est omis dans le tableau pour garder les chemins lisibles.

## Contrat exécutable

Le fichier [OpenAPI généré](openapi.json) ne doit pas être édité à la main. Toute modification de
route ou de schéma doit partir du code, puis régénérer le snapshot et mettre à jour le guide
concerné.
