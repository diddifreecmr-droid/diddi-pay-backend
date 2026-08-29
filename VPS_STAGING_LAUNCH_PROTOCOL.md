# Protocole de test et de lancement VPS - DiddiPay / DiddiFund

## 1. Portee et decision de lancement

Ce protocole concerne le **staging** actuellement configure :

- DiddiFreeID staging ;
- cle Paystack `sk_test_*` ;
- devise XOF ;
- DiddiPay comme orchestrateur de `PaymentIntent` ;
- Paystack comme premier processeur externe ;
- DiddiGo et DiddiFund comme modules clients.

Il permet d'autoriser un lancement staging avec de l'argent de test. Il ne constitue pas une
autorisation de production avec de l'argent reel.

### Decision actuelle

| Environnement | Decision | Condition |
|---|---|---|
| staging | GO conditionnel | toutes les gates de ce document passent |
| production | NO-GO | cle test, reconciliation PaymentIntent non planifiee et supervision a finaliser |

Un `health=ok` prouve seulement que le processus HTTP vit. Un lancement paiement exige aussi la
base, les migrations, l'authentification, Paystack, le webhook, le callback module, l'idempotence,
la reconciliation, les sauvegardes et le rollback.

## 2. Architecture de deploiement attendue

```text
Internet
   |
   | HTTPS 443
   v
Reverse proxy / TLS
   |
   | APP_PORT 48213
   v
payfund-app:8000
   |                 |
   | Docker network  | Docker network
   v                 v
payfund-db:5432    payfund-redis:6379
```

Regles reseau :

- seuls `80/443` doivent etre publics, plus `22` limite aux IP d'administration ;
- `54329` et `61780` sont lies a `127.0.0.1` par Compose et ne doivent jamais etre publics ;
- `48213` doit idealement etre lie a `127.0.0.1` si le reverse proxy tourne sur l'hote ;
- si un reverse proxy Docker separe exige `0.0.0.0:48213`, le firewall doit bloquer ce port depuis
  Internet et n'autoriser que le chemin necessaire au proxy.

Verifier avant chaque lancement :

```bash
ss -tulpn
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Resultat attendu : aucune publication publique de PostgreSQL ou Redis.

## 3. Pre-requis avant de toucher au stack

- Le DNS `pay-api-staging.diddifree.com` pointe vers le VPS.
- Le certificat TLS est valide et renouvele automatiquement.
- Portainer peut lire le depot Git et la branche `main`.
- Le fichier `.env` staging a ete transfere par un canal prive et importe dans Portainer.
- Aucun secret n'est colle dans Git, un ticket, Slack, WhatsApp ou une capture d'ecran.
- Le receiver DiddiGo existe a
  `https://go-api-staging.diddifree.com/internal/webhooks/diddipay` avant d'activer ses paiements.
- Le compte Paystack test accepte XOF et les canaux que l'equipe veut tester.
- Le webhook Paystack est configure vers :

```text
https://pay-api-staging.diddifree.com/payfund/v1/payments/webhooks/paystack
```

Le retour navigateur (`callback_url`) n'est jamais une preuve de paiement. Seul le webhook signe,
ou une reconciliation ulterieure avec le provider, peut rendre le paiement final.

## 4. Sauvegarde obligatoire avant mise a jour

Creer une sauvegarde PostgreSQL sur le VPS avant tout redeploiement :

```bash
sudo install -d -m 700 /opt/backups/diddipay
BACKUP="/opt/backups/diddipay/payfund_$(date -u +%Y%m%dT%H%M%SZ).dump"
docker exec payfund-db pg_dump -U payfund -d payfund -Fc > "$BACKUP"
test -s "$BACKUP"
sha256sum "$BACKUP" > "$BACKUP.sha256"
pg_restore -l "$BACKUP" > /dev/null
echo "$BACKUP"
```

Ces commandes prouvent que le fichier existe et que son catalogue est lisible. Elles ne prouvent
pas encore qu'une restauration complete fonctionne. Une restauration doit etre testee regulierement
dans une base ou un VPS isole, jamais par-dessus la base staging active.

Conserver :

- le chemin de la sauvegarde ;
- son hash SHA-256 ;
- le SHA Git actuellement deploye ;
- l'heure UTC ;
- le nom de l'operateur.

## 5. Import du `.env` dans Portainer

Dans Portainer :

1. Ouvrir `Stacks` puis le stack DiddiPay/DiddiFund.
2. Ouvrir `Editor` puis `Environment variables`.
3. Charger le fichier `.env` local prepare pour le staging.
4. Verifier les noms des variables sans afficher leurs valeurs dans les logs.
5. Enregistrer la configuration avant de redeployer.

Variables indispensables :

```text
APP_PORT
POSTGRES_PORT
REDIS_PORT
POSTGRES_BIND_HOST
REDIS_BIND_HOST
APP_BIND_HOST
DIDDIFREEID_JWKS_URL
DIDDIFREEID_ISSUER
PAYMENT_GATEWAY_MODE
PAYMENT_PROCESSOR_MODE
PAYSTACK_SECRET_KEY
PAYMENT_SERVICE_KEYS
PAYMENT_CALLBACK_TARGETS
DIDDIFUND_DIDDIPAY_CALLBACK_SECRET
QR_SIGNING_SECRET
CORS_ORIGINS
```

Valeurs attendues pour ce staging :

```text
PAYMENT_GATEWAY_MODE=paystack
PAYMENT_GATEWAY_AUTOCONFIRM=false
PAYMENT_PROCESSOR_MODE=paystack
PAYSTACK_BASE_URL=https://api.paystack.co
DIDDIFREEID_ISSUER=diddifree-id
SQL_ECHO=false
```

`PAYSTACK_WEBHOOK_SECRET` peut rester vide dans la configuration actuelle : DiddiPay utilise alors
`PAYSTACK_SECRET_KEY` pour verifier `x-paystack-signature`, ce qui correspond au mecanisme Paystack.

Ne pas publier la valeur de `PAYMENT_SERVICE_KEYS`. DiddiGo ne recoit que sa propre cle, jamais
celle de DiddiFund. Chaque module recoit egalement son propre secret HMAC de callback.

## 6. Deploiement dans Portainer

1. Noter le SHA Git deploye avant changement.
2. Selectionner la branche `main` et activer le nouveau pull de l'image/depot.
3. Choisir `Update the stack` avec reconstruction de l'image.
4. Ne pas supprimer les volumes.
5. Suivre les logs `payfund-app` jusqu'au demarrage complet.

Les logs attendus contiennent :

```text
Context impl PostgresqlImpl
Will assume transactional DDL
Application startup complete
Uvicorn running on http://0.0.0.0:8000
```

Les migrations Alembic sont executees automatiquement par `docker-entrypoint.sh`. Le stack actuel
est mono-instance ; ne pas augmenter le nombre de replicas tant que les migrations sont lancees par
chaque conteneur au demarrage.

## 7. Verification locale sur le VPS

### 7.1 Conteneurs

```bash
docker inspect --format '{{.State.Health.Status}}' payfund-db
docker inspect --format '{{.State.Health.Status}}' payfund-redis
docker inspect --format '{{.State.Health.Status}}' payfund-app
docker logs --tail 100 payfund-app
```

Les trois statuts doivent etre `healthy`. Les logs ne doivent contenir ni traceback, ni erreur de
connexion, ni secret.

### 7.2 Migrations

```bash
docker exec payfund-app alembic heads
docker exec payfund-app alembic current
docker exec payfund-app alembic check
```

Resultat attendu pour cette version :

```text
7f3a1c9e2b6d (head)
No new upgrade operations detected.
```

`heads` et `current` doivent designer la meme revision. Sinon, le lancement est NO-GO.

### 7.3 Configuration chargee sans imprimer les secrets

```bash
docker exec payfund-app python -c "from payfund_app.core.config import get_settings; s=get_settings(); print({'processor': s.payment_processor_mode, 'gateway': s.payment_gateway_mode, 'paystack': bool(s.paystack_secret_key), 'service_clients': sorted(s.payment_service_key_map), 'callback_clients': sorted(s.payment_callback_targets), 'sql_echo': s.sql_echo})"
```

Attendu :

- `processor` et `gateway` a `paystack` ;
- `paystack` a `True` ;
- `service_clients` contient `diddigo` et `diddifund` ;
- `callback_clients` contient `diddigo` et `diddifund` ;
- `sql_echo` a `False`.

## 8. Smoke tests HTTP publics

Depuis une autre machine que le VPS :

```bash
export BASE_URL=https://pay-api-staging.diddifree.com/payfund/v1
curl -fsS "$BASE_URL/health"
curl -fsS "$BASE_URL/ready"
curl -fsS "$BASE_URL/openapi.json" > /dev/null
curl -fsS "$BASE_URL/docs" > /dev/null
```

Attendu :

```json
{"status":"ok"}
{"status":"ready"}
```

Verifier aussi qu'une route S2S refuse un appel anonyme :

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE_URL/payment-intents"
```

Attendu : `401`.

Verifier CORS pour une origine DiddiFree :

```bash
curl -i -X OPTIONS "$BASE_URL/payment-intents" \
  -H 'Origin: https://go-staging.diddifree.com' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type,x-client-id,x-service-key,idempotency-key'
```

La reponse doit contenir `access-control-allow-origin` pour l'origine demandee.

## 9. Suite boite noire complete contre le staging

Executer cette suite depuis le poste de test ou la CI, pas dans le conteneur de production :

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export PAYFUND_BASE_URL=https://pay-api-staging.diddifree.com/payfund/v1
export IDENTITY_BASE_URL=https://auth-staging.diddifree.com/identity/v1
pytest tests_live/ -v
```

Au premier lancement, la suite demande les OTP de deux utilisateurs. Ils sont ensuite conserves
dans `tests_live/.auth_cache.json`, fichier ignore par Git. En CI, injecter les access/refresh tokens
par variables secretes comme explique dans `tests_live/README.md`.

Analyser separement :

- `passed` : comportement valide ;
- `skipped` : verifier la raison, surtout si elle concerne un depot reste `pending` ;
- `failed` : lancement NO-GO tant que la cause n'est pas comprise et corrigee.

Un grand nombre de `skipped` ne doit jamais etre presente comme une suite totalement validee.

## 10. Test PaymentIntent Paystack de bout en bout

### 10.1 Creer le paiement comme DiddiGo

Ne pas mettre la cle S2S directement dans l'historique shell :

```bash
read -rsp 'DiddiGo service key: ' DIDDIGO_SERVICE_KEY
echo
export IDEMPOTENCY_KEY="ops-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
export BASE_URL=https://pay-api-staging.diddifree.com/payfund/v1
```

Creer un petit paiement de test. Remplacer l'e-mail par un compte de test controle :

```bash
cat > /tmp/diddipay-smoke-request.json <<'JSON'
{
  "business_reference": "ops:staging:payment-smoke",
  "amount": 100,
  "currency": "XOF",
  "channel": "card",
  "customer_email": "replace-with-test-user@example.com",
  "callback_url": "https://go-staging.diddifree.com/payment-return",
  "description": "DiddiPay staging smoke test",
  "metadata": {"test": true, "source": "vps-launch-protocol"}
}
JSON

curl -fsS -D /tmp/diddipay-smoke-headers.txt \
  -o /tmp/diddipay-smoke-response.json \
  -X POST "$BASE_URL/payment-intents" \
  -H 'Content-Type: application/json' \
  -H 'X-Client-ID: diddigo' \
  -H "X-Service-Key: $DIDDIGO_SERVICE_KEY" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  --data-binary @/tmp/diddipay-smoke-request.json

jq '{id,status,attempts}' /tmp/diddipay-smoke-response.json
```

Attendu : HTTP `201`, un identifiant stable, un statut non final et une `next_action.url` Paystack.

### 10.2 Prouver l'idempotence

Rejouer exactement la meme requete avec la meme `Idempotency-Key` :

```bash
curl -fsS \
  -X POST "$BASE_URL/payment-intents" \
  -H 'Content-Type: application/json' \
  -H 'X-Client-ID: diddigo' \
  -H "X-Service-Key: $DIDDIGO_SERVICE_KEY" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  --data-binary @/tmp/diddipay-smoke-request.json \
  | jq '{id,status}'
```

L'identifiant doit etre identique. Modifier ensuite le montant tout en gardant la meme cle doit
produire `409 IDEMPOTENCY_CONFLICT`, jamais un second paiement.

### 10.3 Terminer le checkout Paystack

1. Ouvrir `next_action.url` dans un navigateur controle.
2. Utiliser un instrument de test actuellement documente dans le dashboard Paystack.
3. Terminer le checkout.
4. Ne pas marquer la course payee sur la seule page de retour.

### 10.4 Verifier le webhook et le statut final

```bash
export PAYMENT_INTENT_ID="$(jq -r '.id' /tmp/diddipay-smoke-response.json)"

curl -fsS "$BASE_URL/payment-intents/$PAYMENT_INTENT_ID" \
  -H 'X-Client-ID: diddigo' \
  -H "X-Service-Key: $DIDDIGO_SERVICE_KEY" \
  | jq '{id,status,attempts}'

docker logs --since 10m payfund-app 2>&1 \
  | grep -E 'payment.webhook.processed|payment.intent|payment.callback'
```

Attendu :

- Paystack affiche une livraison webhook HTTP `200` ;
- DiddiPay passe le `PaymentIntent` a `succeeded` une seule fois ;
- DiddiGo recoit un callback HMAC valide ;
- DiddiGo enregistre l'identifiant d'evenement dans son inbox unique ;
- la course passe a l'etat paye dans une transaction locale atomique.

Depuis le dashboard Paystack, renvoyer le meme webhook. Le statut doit rester identique et aucun
second effet metier ne doit apparaitre dans DiddiGo.

Nettoyer le shell apres le test :

```bash
unset DIDDIGO_SERVICE_KEY IDEMPOTENCY_KEY PAYMENT_INTENT_ID
rm -f /tmp/diddipay-smoke-request.json /tmp/diddipay-smoke-response.json \
  /tmp/diddipay-smoke-headers.txt
```

## 11. Jobs operationnels obligatoires

Le callback sortant vers les modules est durable, mais il doit etre relaye periodiquement. Sur le
VPS, planifier les commandes avec `flock` pour eviter deux executions concurrentes :

```cron
* * * * * flock -n /tmp/diddipay-callback-relay.lock docker exec payfund-app python -m payfund_app.ops relay-payment-events --limit 100 2>&1 | logger -t diddipay-callback-relay
*/2 * * * * flock -n /tmp/diddipay-housekeeping.lock docker exec payfund-app python -m payfund_app.ops housekeeping 2>&1 | logger -t diddipay-housekeeping
*/5 * * * * docker exec payfund-app python -m payfund_app.ops payment-events-status 2>&1 | logger -t diddipay-payment-events-status
```

Verifier manuellement apres installation :

```bash
docker exec payfund-app python -m payfund_app.ops relay-payment-events --limit 100
docker exec payfund-app python -m payfund_app.ops housekeeping
docker exec payfund-app python -m payfund_app.ops payment-events-status
```

`dead_letter` doit rester a `0`. Toute valeur superieure a zero exige une alerte et une analyse.

### Limite actuelle a ne pas cacher

`housekeeping` reconcilie actuellement les depots du wallet historique. Le coeur PaymentIntent
possede son use case de reconciliation, mais il n'est pas encore expose par une commande worker
planifiable. Le webhook Paystack fonctionne, mais le fallback automatique complet d'un webhook
PaymentIntent manque n'est donc pas encore exploitable en production.

Consequence :

- staging avec cle Paystack test : autorise si toutes les autres gates passent ;
- production avec argent reel : NO-GO jusqu'au cablage, test et monitoring de ce worker.

## 12. Observation pendant les premieres 24 heures

Surveiller au minimum :

- taux HTTP `5xx` ;
- latence de creation d'un PaymentIntent ;
- erreurs et timeouts Paystack ;
- PaymentIntents non finaux trop anciens ;
- webhooks invalides, ignores et dupliques ;
- callbacks `pending`, `retried` et `dead_letter` ;
- ecart entre montants captures, rembourses, frais, net attendu et settlement ;
- disponibilite de DiddiFreeID JWKS ;
- espace disque PostgreSQL et volume Docker ;
- age et succes de la derniere sauvegarde.

Commandes de premiere analyse :

```bash
docker logs --since 1h payfund-app
docker stats --no-stream payfund-app payfund-db payfund-redis
docker exec payfund-app python -m payfund_app.ops payment-events-status
```

Ne jamais journaliser un JWT, une cle S2S, une cle Paystack, un secret callback, un PIN ou des
donnees de carte.

## 13. Rollback applicatif

Si le nouveau conteneur ne devient pas healthy ou provoque des erreurs :

1. Fermer temporairement la creation de nouveaux paiements au niveau DiddiGo/DiddiFund.
2. Conserver la base et les volumes intacts.
3. Dans Portainer, redeployer le SHA Git precedent connu comme stable.
4. Ne pas executer `alembic downgrade` sur une base financiere active.
5. Relancer `/health`, `/ready`, `alembic current` et les smoke tests.
6. Laisser les paiements deja envoyes a Paystack se finaliser ou etre reconcilies.
7. Documenter l'incident, les PaymentIntents concernes et la decision prise.

Une restauration de sauvegarde n'est pas un rollback applicatif normal. Elle est reservee a une
corruption ou perte de donnees confirmee, avec arret des ecritures et procedure d'incident dediee.

## 14. Checklist GO / NO-GO staging

### Infrastructure

- [ ] DNS et TLS valides.
- [ ] Postgres et Redis non publics.
- [ ] Sauvegarde creee, hashee et catalogue lisible.
- [ ] SHA Git de la release enregistre.
- [ ] Trois conteneurs `healthy`.
- [ ] Migrations sur `7f3a1c9e2b6d (head)` et `alembic check` propre.

### Securite

- [ ] `.env` absent de Git.
- [ ] Cle Paystack de test chargee uniquement cote serveur.
- [ ] Cles S2S distinctes par module.
- [ ] Secrets callback distincts par module.
- [ ] `SQL_ECHO=false`.
- [ ] Route S2S anonyme refusee avec `401`.

### Fonctionnel

- [ ] `/health`, `/ready`, Swagger et OpenAPI accessibles en HTTPS.
- [ ] Suite `tests_live` analysee sans echec ignore.
- [ ] Creation PaymentIntent `201`.
- [ ] Replay idempotent avec le meme identifiant.
- [ ] Checkout Paystack test termine.
- [ ] Webhook signe traite une seule fois.
- [ ] Callback DiddiGo signe, deduplique et applique.
- [ ] Statut DiddiPay et etat metier DiddiGo coherents.

### Operations

- [ ] Relay callbacks et housekeeping planifies.
- [ ] `dead_letter=0`.
- [ ] Logs et alertes consultables.
- [ ] Rollback vers le SHA precedent compris et teste.

Une seule case critique non validee signifie **NO-GO**. On corrige d'abord ; on ne lance pas en
esperant que la production confirmera le comportement.
