# DiddiPay / DiddiFund - Cloture MVP PaymentIntent

**Date de validation locale :** 2026-08-15

**Revision Alembic :** `22eb39b4d210 (head)`

## 1. Decision

Le MVP est **code-complete et pret pour validation staging**. Il ne doit pas encore traiter de
l'argent reel tant que les gates externes de la section 7 ne sont pas tous signes.

Cette distinction est importante : les tests prouvent le comportement de notre code, mais ils ne
prouvent ni la configuration du compte marchand Paystack, ni la livraison reseau des webhooks, ni
le rapprochement avec le compte bancaire reel.

## 2. Perimetre livre

### Coeur DiddiPay

- `PaymentIntent` provider-neutral et isole par module ;
- authentification S2S, idempotence et references metier stables ;
- adaptateurs `sandbox` et Paystack sans fuite du provider dans le contrat ;
- webhooks Paystack signes, inbox durable, deduplication et validation montant/devise ;
- reconciliation des tentatives non finales ou incertaines ;
- outbox transactionnelle, callbacks HMAC, retries, dead letters et claim/lease concurrent ;
- annulation locale sure et remboursements Paystack idempotents ;
- sous-ledger double entree pour captures, frais, remboursements et settlements ;
- statut ops des livraisons et logs JSON structures.

### Modules consommateurs

- receiver DiddiGo de reference avec inbox et transition atomique ;
- investissement DiddiFund par PaymentIntent et callback signe ;
- contrat API DiddiPay 3.1, briefing frontend, brief backend et runbook de migration.

### Compatibilite

- wallet personnel auto-provisionne et self-heal ;
- PIN Argon2id, recovery, step-up DiddiFreeID, P2P et paiement marchand legacy ;
- campagnes, investissements wallet, prets et remboursements DiddiFund legacy ;
- references KYC vers DiddiFiles sans stockage des fichiers dans DiddiPay.

Le wallet legacy n'est plus l'identite de DiddiPay. Il reste disponible sans migration destructive
et pourra devenir DiddiWallet comme moyen de paiement additionnel.

## 3. Chaine de migrations

| Revision | Objet principal |
|---|---|
| `0001` a `0012` | wallet, fund, securite PIN/step-up, outbox et historique legacy |
| `0013_payment_orchestration` | intentions, tentatives et evenements provider |
| `0014_payment_outbox` | livraison durable des evenements de paiement |
| `0426796797d5` | ordres de paiement et inbox DiddiFund |
| `a560f55cc5d9` | sous-ledger financier PaymentIntent |
| `22eb39b4d210` | claim/lease et durcissement de l'outbox paiement |

Le demarrage Docker execute `alembic upgrade head` avant Uvicorn. Sur une future architecture avec
plusieurs replicas, les migrations devront sortir de l'entrypoint et etre executees par un job
unique avant le rollout.

## 4. Configuration staging

Variables du nouveau coeur :

```env
DATABASE_URL=postgresql+psycopg://...
PAYMENT_PROCESSOR_MODE=paystack
PAYSTACK_SECRET_KEY=<secret-staging>
PAYSTACK_WEBHOOK_SECRET=<secret-staging>
PAYSTACK_BASE_URL=https://api.paystack.co
PAYMENT_SERVICE_KEYS=diddigo:<secret-1>,diddifund:<secret-2>
PAYMENT_CALLBACK_TARGETS={"diddigo":{"url":"https://go-api-staging.diddifree.com/internal/webhooks/diddipay","secret":"<secret-hmac-1>"},"diddifund":{"url":"http://app:8000/payfund/v1/fund/payments/webhooks/diddipay","secret":"<secret-hmac-2>"}}
DIDDIFUND_DIDDIPAY_CALLBACK_SECRET=<secret-hmac-2>
```

Le mode `PAYMENT_PROCESSOR_MODE=sandbox` fonctionne sans cle Paystack. Le mode `paystack` exige
`PAYSTACK_SECRET_KEY`. `PAYSTACK_WEBHOOK_SECRET` peut etre distinct et doit correspondre a la
configuration du webhook.

Variables plateforme et legacy encore necessaires selon les parcours actives :

- `DIDDIFREEID_JWKS_URL`, `DIDDIFREEID_ISSUER` et `DIDDIFREEID_STEP_UP_MAX_TTL_SECONDS` ;
- `REDIS_URL` et `EVENT_BUS_CHANNEL` ;
- `PAYMENT_GATEWAY_MODE` pour les anciens depots/retraits wallet uniquement ;
- `WALLET_STEP_UP_THRESHOLD_XOF`, `QR_SIGNING_SECRET` et `CORS_ORIGINS` pour le wallet legacy.

Chaque secret doit etre aleatoire, different par environnement, injecte par Portainer ou un
gestionnaire de secrets, jamais commite ni logge. Les callbacks externes doivent etre HTTPS.

## 5. Ordre de deploiement

1. Sauvegarder PostgreSQL et verifier une restauration sur un environnement isole.
2. Deployer d'abord les receivers callback DiddiGo/DiddiFund avec leur inbox idempotente.
3. Configurer les cles service, secrets callback et secrets Paystack.
4. Deployer DiddiPay ; attendre la fin des migrations et le statut healthy.
5. Verifier `alembic current`, `alembic check`, `/health`, `/ready` et `/openapi.json`.
6. Configurer Paystack vers `POST /payfund/v1/payments/webhooks/paystack`.
7. Planifier `relay-payment-events`, la reconciliation et `payment-events-status`.
8. Executer les scenarios staging de la section 7 avant d'ouvrir le trafic.

Ne jamais downgrader ou supprimer une table financiere contenant des transactions. Un rollback
applicatif doit laisser les PaymentIntent deja envoyes au PSP se terminer ou se reconcilier.

## 6. Validation realisee localement

- compilation Python : succes ;
- suite Python/PostgreSQL complete : `253 passed`, aucune erreur ;
- avertissement restant : deprecation `httpx`/Starlette TestClient, non bloquante ;
- image Docker reconstruite et stack demarree ;
- conteneurs API, PostgreSQL et Redis : healthy ;
- `alembic current` : `22eb39b4d210 (head)` ;
- `alembic check` : aucune operation manquante ;
- `/health`, `/ready` et `/openapi.json` : HTTP 200 ;
- endpoint S2S sans credentials : HTTP 401 attendu ;
- smoke S2S Docker : creation `201`, replay sur le meme intent, lecture/liste/resume `200` ;
- annulation/remboursement invalides `409` et webhook non signe `401`, comme attendu ;
- Swagger expose toutes les requetes du coeur, y compris corps et signature du webhook Paystack ;
- tests d'integration couvrant idempotence, doublons, reconciliation, callbacks, remboursements,
  comptabilite et isolation entre modules.

## 7. Gates obligatoires avant argent reel

- vraies cles **staging** Paystack injectees et testees sans jamais etre partagees au frontend ;
- compte marchand Paystack active pour XOF et les canaux reellement annonces au produit ;
- paiement sandbox externe complet : creation, checkout, webhook, callback module et statut metier ;
- scenario webhook manque repare par reconciliation ;
- scenario callback module indisponible repare par retry, sans double effet ;
- remboursement sandbox et settlement verifies contre le dashboard/rapport Paystack ;
- workers ou jobs planifies avec une seule responsabilite et des timeouts explicites ;
- alertes sur erreurs provider, `dead_letter > 0`, reconciliation agee et settlement outstanding ;
- sauvegarde chiffree, restauration chronometree et runbook d'incident partage ;
- revue securite des secrets, HTTPS, acces ops et journaux ;
- validation produit/comptable du recu, des frais, remboursements et litiges.

Sans cle et compte Paystack dans ce depot, les tests locaux utilisent un faux serveur HTTP et ne
peuvent pas certifier le rail externe. Ce n'est pas un defaut du coeur ; c'est une gate de staging.

## 8. Apres MVP

- adaptateurs directs Orange Money, Wave, MTN MoMo et Moov ;
- import et rapprochement automatiques des rapports de settlement ;
- payouts/retraits via le coeur PaymentIntent ;
- DiddiWallet comme moyen de paiement ;
- remplacement du Redis Pub/Sub identite par un bus durable avec replay ;
- multi-devise active, KYC automatise, fraude et limites avancees ;
- tests de charge, haute disponibilite et plan de reprise apres sinistre formalise.

L'outbox PaymentIntent garantit maintenant la reprise des callbacks financiers. Le Pub/Sub
DiddiFreeID utilise pour certains evenements de provisioning wallet reste, lui, non durable ; le
self-heal et le backfill limitent son impact jusqu'a la migration vers Redis Streams ou Kafka.

## 9. Cloture

Le contrat, le code, les migrations, les tests et Docker sont alignes pour le MVP. La prochaine
decision n'est plus une decision d'implementation locale : c'est l'ouverture d'une validation
staging avec Paystack et les modules recepteurs deployes.
