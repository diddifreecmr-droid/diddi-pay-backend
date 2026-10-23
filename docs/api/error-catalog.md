# Catalogue d'erreurs

Les erreurs HTTP utilisent le format JSON de l'application, avec un code métier stable lorsque
le domaine en fournit un.

| HTTP | Signification | Action client |
| --- | --- | --- |
| `401` | Jeton absent, invalide ou expiré | Renouveler le jeton |
| `403` | Audience, client ou scope non autorisé | Vérifier l'identité et les permissions |
| `404` | Ressource absente ou non visible pour le client | Vérifier l'identifiant |
| `409` | Conflit d'idempotence ou transition impossible | Relire l'état avant de réessayer |
| `422` | Corps, UUID, enum ou limite invalide | Corriger la requête |
| `429` | Limite de débit atteinte | Réessayer avec backoff |
| `500` | Erreur interne | Conserver `X-Request-ID` et ouvrir un incident |
| `502` | Réponse provider inexploitable | Vérifier le statut asynchrone et l'outbox |
