# Brief frontend — DiddiSupport — 2026-10-23

## À implémenter

- afficher le contexte du paiement avant l'action opérateur ;
- demander une clé d'idempotence pour chaque remboursement ;
- afficher `pending`, `succeeded`, `failed` et `unknown` séparément ;
- conserver et afficher `X-Request-ID` en cas d'incident.

## À ne pas faire

- déclarer un remboursement réussi avant confirmation DiddiPay ;
- permettre un second envoi silencieux avec une nouvelle clé ;
- exposer les secrets de callback ou les détails provider.
