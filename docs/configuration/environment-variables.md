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
| `PAYMENT_PROCESSOR_MODE` | Processeur du nouvel orchestrateur PaymentIntent/Payout (`sandbox`, `paystack`, `pawapay`, `cinetpay` ou `jeko`) | `sandbox` | selon la recette | mode approuvé |
| `PAYMENT_GATEWAY_MODE` | Processeur du wallet legacy (`stub`, `paystack` ou `pawapay`) | `stub` | selon la recette | mode approuvé |
| `PAYSTACK_SECRET_KEY` / `PAYSTACK_WEBHOOK_SECRET` | Credentials Paystack | vide | secrets test | secrets live |
| `PAWAPAY_API_TOKEN` | Jeton PawaPay pour dépôts, retraits et callbacks | vide | secret sandbox | secret production |
| `PAWAPAY_BASE_URL` | Base API PawaPay | sandbox PawaPay | `https://api.sandbox.pawapay.io` | URL production PawaPay |
| `PAWAPAY_DEPOSIT_CALLBACK_URL` | Callback PawaPay des dépôts | vide | URL HTTPS DiddiPay | URL HTTPS DiddiPay |
| `PAWAPAY_REFUND_CALLBACK_URL` | Callback PawaPay des remboursements | vide | URL HTTPS DiddiPay | URL HTTPS DiddiPay |
| `PAWAPAY_PAYOUT_CALLBACK_URL` | Callback PawaPay des payouts | vide | URL HTTPS DiddiPay | URL HTTPS DiddiPay |
| `CINETPAY_API_KEY` / `CINETPAY_API_PASSWORD` | Credentials CinetPay PaymentIntent/Payout | vide | secrets sandbox | secrets production |
| `CINETPAY_BASE_URL` / `CINETPAY_COUNTRY` | Endpoint et pays CinetPay | auto / `CI` | selon le compte | selon le compte |
| `CINETPAY_WEBHOOK_URL` | URL HTTPS publique recevant les notifications de paiement CinetPay | vide | `/payfund/v1/payments/webhooks/cinetpay` | URL HTTPS de production |
| `JEKO_API_KEY` / `JEKO_API_KEY_ID` / `JEKO_STORE_ID` | Credentials et boutique Jèko | vide | store de test approuve | store production |
| `JEKO_BASE_URL` / `JEKO_WEBHOOK_SECRET` | Endpoint et secret callback Jèko | `https://api.jeko.africa` / vide | selon le compte | selon le compte |
| `QR_SIGNING_SECRET` | Signature QR | `change-me-in-production` | secret staging | secret fort obligatoire |
| `CORS_ORIGINS` | Origines web autorisées | `*` | domaines staging | domaines production |
| `DOCS_PUBLIC_ENABLED` | Publie ou masque le portail documentaire | `false` | `true` | `false` |
| `DOCS_PORT` | Port hôte du portail documentaire | `48080` | port libre staging | port interne proxy |

La liste générale est maintenue dans `.env.example`. Les paramètres propres à chaque
processeur sont dans `.env.pawapay.example`, `.env.paystack.example`,
`.env.cinetpay.example`, `.env.jeko.example` et `.env.sandbox.example`. Les fichiers réels
`.env.*` ne doivent jamais être committes.
