# Variables d'environnement

Les valeurs sensibles doivent être injectées par l'environnement de déploiement, jamais
commitées dans le dépôt.

| Variable | Rôle | Défaut code | Staging | Production |
| --- | --- | --- | --- | --- |
| `DEPLOYMENT_ENVIRONMENT` | Environnement applicatif | `local` | `staging` | `production` |
| `DIDDIFREEID_JWKS_URL` | JWKS de vérification JWT | URL staging | URL staging | URL production |
| `SUPPORT_AUDIENCE` | Audience DiddiSupport | `diddipay` | `diddipay` | `diddipay` |
| `SUPPORT_CLIENT_IDS` | Clients Support autorisés | vide | ID staging | ID production |
| `SUPPORT_CONTEXT_SCOPE` | Scope contexte | `diddipay:support-context:read` | idem | idem |
| `SUPPORT_REFUND_SCOPE` | Scope remboursement | `diddipay:support-actions:refund-request` | idem | idem |
| `PAYMENT_EVENT_TRANSPORT` | Transport événements | `http_callback` | selon recette | selon architecture validée |
| `PAYMENT_CALLBACK_TARGETS` | Cibles et secrets callbacks | `{}` | JSON staging | JSON production |
| `PAYMENT_REDIS_STREAM` | Stream Redis événements | `diddipay.payment.events.v1` | idem | idem |
| `PAYMENT_SERVICE_KEYS` | Clés S2S par module | vide | secrets staging | secrets production |
| `PAYMENT_PROCESSOR_MODE` | Mode processeur | `sandbox` | `sandbox` | mode approuvé |
| `QR_SIGNING_SECRET` | Signature QR | `change-me-in-production` | secret staging | secret fort obligatoire |
| `CORS_ORIGINS` | Origines web autorisées | `*` | domaines staging | domaines production |
| `DOCS_PUBLIC_ENABLED` | Publie ou masque le portail documentaire | `false` | `true` | `false` |
| `DOCS_PORT` | Port hôte du portail documentaire | `48080` | port libre staging | port interne proxy |

La liste complète et les exemples de ports sont maintenus dans le fichier versionné
`.env.example` à la racine du dépôt ; il n'est pas publié dans le portail public.
