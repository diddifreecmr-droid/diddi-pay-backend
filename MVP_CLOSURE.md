# DiddiPay / DiddiFund - Cloture MVP

Date de validation locale : 2026-08-14

## 1. Statut

Le code du perimetre MVP est termine et valide localement. Son activation complete en staging ou
production reste conditionnee aux secrets et services externes listes plus bas.

Le MVP livre :

- wallet personnel auto-provisionne et self-heal au premier acces ;
- ledger double entree, idempotence, historique et contre-passations ;
- depot provider asynchrone, webhook Paystack durable et reconciliation ;
- transfert P2P et paiement marchand ;
- PIN Argon2id obligatoire sur chaque debit initie par l'utilisateur ;
- recovery codes, changement de PIN et reset admin audite ;
- step-up DiddiFreeID signe, court, lie a un purpose et a usage unique ;
- campagnes, investissements, prets, echeanciers et remboursements DiddiFund ;
- hooks KYC vers DiddiFiles sans stockage des fichiers dans DiddiPay ;
- backfill/provisioning ops, outbox, inbox webhook, logs structures, health et readiness ;
- contrat OpenAPI executable et briefs frontend/backend.

## 2. Migrations obligatoires

La revision attendue est `0012_consumed_step_up_proofs (head)`.

| Revision | Objet principal |
|---|---|
| `0001` | schemas wallet/fund et ledger initial |
| `0002` | depots et prets |
| `0003` | structures multi-devise |
| `0004` | unicite des comptes par type |
| `0005` | reference metier des transactions |
| `0006` | outbox durable |
| `0007` | journaux de reconciliation |
| `0008` | inbox durable des webhooks |
| `0009` | references KYC/DiddiFiles |
| `0010` | PIN et recovery codes |
| `0011` | audit recovery et table OTP historique |
| `0012` | registre des preuves step-up consommees |

La table OTP de `0011` est conservee pour compatibilite de schema mais n'est plus utilisee par
l'API. Sa suppression est une migration post-MVP distincte, apres verification des environnements.

## 3. Ordre de deploiement

1. Sauvegarder PostgreSQL et verifier l'espace disque.
2. Configurer les variables obligatoires et les secrets.
3. Deployer l'image ; `docker-entrypoint.sh` execute `alembic upgrade head` avant Uvicorn.
4. Verifier `alembic current` et `alembic check` dans le conteneur.
5. Verifier `GET /payfund/v1/health`, `GET /payfund/v1/ready` et OpenAPI.
6. Configurer le webhook Paystack vers `POST /payfund/v1/wallet/webhooks/paystack`.
7. Executer un depot sandbox de bout en bout puis verifier transaction, ledger, inbox et
   reconciliation avant d'autoriser du volume reel.

Ne jamais executer un downgrade destructif sur une base contenant des transactions sans plan de
restauration teste. Un rollback applicatif doit conserver les donnees et le ledger.

## 4. Configuration obligatoire

- `DATABASE_URL`
- `REDIS_URL`
- `DIDDIFREEID_JWKS_URL`
- `DIDDIFREEID_ISSUER`
- `DIDDIFREEID_STEP_UP_MAX_TTL_SECONDS`
- `WALLET_STEP_UP_THRESHOLD_XOF`
- `QR_SIGNING_SECRET` avec une valeur forte et differente par environnement
- `CORS_ORIGINS` restreint en production
- `PAYMENT_GATEWAY_MODE=paystack` pour le depot reel
- `PAYSTACK_SECRET_KEY`
- `PAYSTACK_WEBHOOK_SECRET`

Les secrets ne doivent jamais etre commites, imprimes dans les logs ou inclus dans une image.

## 5. Dependances externes bloquantes pour le live

DiddiFreeID doit fournir les parcours challenge/verification et emettre des JWT RS256 avec :

- `purpose=wallet.pin.set` pour le premier PIN ;
- `purpose=wallet.transfer.high_value` pour un transfert sensible ;
- `sub`, `iss`, `purpose`, `jti`, `iat` et `exp` ;
- une duree de vie maximale compatible avec la configuration DiddiPay.

Paystack doit fournir les cles de l'environnement cible et envoyer les webhooks signes. Le MVP
reel Paystack est limite au depot wallet. Le retrait Paystack reel n'est pas annonce comme livre.

## 6. Validation realisee

- suite Python/PostgreSQL complete : `191 passed` ;
- image Docker reconstruite apres chaque sprint de cloture ;
- conteneurs API, PostgreSQL et Redis sains ;
- `alembic current` : `0012 (head)` ;
- `alembic check` : aucune operation de migration manquante ;
- health et readiness : HTTP 200 ;
- OpenAPI : schemas de succes et champs de securite verifies ;
- preflight CORS verifie pour localhost, DiddiFree et Vercel.

## 7. Report explicite apres MVP

- migration de Redis Pub/Sub vers Redis Streams avec consumer groups et replay ;
- suppression de la table OTP historique ;
- payouts/retraits Paystack reels ;
- adaptateurs reels Orange Money, Wave, MTN MoMo et Moov ;
- multi-devise active et conversion en production ;
- automatisation complete du KYC et decisions de risque avancees ;
- redistribution du rendement DiddiFund aux investisseurs ;
- haute disponibilite, tests de charge et plan de reprise apres sinistre formalise.

Le Pub/Sub actuel peut perdre un evenement lorsqu'un consommateur est indisponible. Pour le MVP,
le self-heal wallet, les endpoints de backfill et l'outbox limitent l'impact, mais ne remplacent pas
un bus durable inter-services. Redis Streams est donc la premiere evolution d'infrastructure apres
MVP, pas une capacite a annoncer comme deja livree.

## 8. Decision de cloture

Le MVP peut etre cloture sur le plan code et contrat. Le passage en argent reel exige une validation
staging des dependances externes, un backup restaure avec succes et un runbook d'incident partage
avec l'equipe d'exploitation.
