# DiddiPay / DiddiGo - matrice des environnements et routes

Ce document repond a deux questions operationnelles :

1. quelle variable doit etre configuree dans quel stack ;
2. qui appelle quelle route pendant un paiement DiddiGo.

Les valeurs secretes ne figurent jamais dans ce document. Les valeurs staging reelles sont dans
les fichiers locaux ignores par Git :

- `.env` : variables du stack DiddiPay/DiddiFund ;
- `.env.diddigo-diddipay-staging` : cinq variables a fusionner dans le stack DiddiGo.

Le second fichier est un **overlay d'integration**, pas un remplacement du `.env` complet de
DiddiGo. DiddiGo conserve aussi ses propres variables de base de donnees, DiddiFreeID, SMS, etc.

## 1. Le modele mental en une minute

Deux backends communiquent dans les deux directions avec deux secrets differents :

```text
                         secret A: service key
             DiddiGo  ---------------------------->  DiddiPay
                      creation / lecture paiement

                         secret B: callback HMAC
             DiddiGo  <----------------------------  DiddiPay
                       evenement paiement final
```

- **Secret A** authentifie DiddiGo quand il appelle DiddiPay.
- **Secret B** authentifie DiddiPay quand il appelle le callback DiddiGo.
- La cle Paystack reste uniquement dans DiddiPay.
- Aucun de ces trois secrets ne va dans Flutter ou dans le navigateur.

## 2. Variables du stack DiddiPay

Importer le fichier local `.env` dans le stack **DiddiPay/DiddiFund**.

### Variables specifiques a l'integration DiddiGo

```env
PAYMENT_PROCESSOR_MODE=paystack
PAYSTACK_SECRET_KEY=<cle Paystack staging>
PAYSTACK_WEBHOOK_SECRET=
PAYSTACK_BASE_URL=https://api.paystack.co

PAYMENT_SERVICE_KEYS=diddigo:<SECRET_A>,diddifund:<AUTRE_SECRET>
PAYMENT_CALLBACK_TARGETS={"diddigo":{"url":"https://go-api-staging.diddifree.com/internal/webhooks/diddipay","secret":"<SECRET_B>"},"diddifund":{"url":"http://app:8000/payfund/v1/fund/payments/webhooks/diddipay","secret":"<SECRET_DIDDIFUND>"}}
```

| Variable DiddiPay | Role | Qui doit connaitre la valeur |
|---|---|---|
| `PAYMENT_PROCESSOR_MODE` | selectionne l'adaptateur Paystack | DiddiPay seulement |
| `PAYSTACK_SECRET_KEY` | appelle Paystack et verifie ses signatures si aucun secret distinct n'est defini | DiddiPay seulement |
| `PAYSTACK_WEBHOOK_SECRET` | secret webhook ; vide signifie fallback sur la cle Paystack | DiddiPay seulement |
| `PAYMENT_SERVICE_KEYS` | associe chaque `X-Client-ID` a sa cle S2S | DiddiPay et chaque module pour sa propre entree |
| `PAYMENT_CALLBACK_TARGETS` | URL callback et secret HMAC de chaque module | DiddiPay et chaque module pour sa propre entree |

### Variables de plateforme DiddiPay

Celles-ci restent aussi dans le stack DiddiPay :

```env
DIDDIFREEID_JWKS_URL=https://auth-staging.diddifree.com/identity/v1/.well-known/jwks.json
DIDDIFREEID_ISSUER=diddifree-id
DIDDIFREEID_STEP_UP_MAX_TTL_SECONDS=300
EVENT_BUS_CHANNEL=diddifree.events
WALLET_STEP_UP_THRESHOLD_XOF=50000
QR_SIGNING_SECRET=<secret DiddiPay>
CORS_ORIGINS=https://diddifree.com,https://vercel.com
SQL_ECHO=false
```

Ces variables concernent DiddiPay, son wallet historique et son integration DiddiFreeID. Elles ne
doivent pas etre copiees dans DiddiGo, sauf si DiddiGo possede independamment une variable portant
le meme nom pour son propre besoin.

## 3. Variables du stack DiddiGo

Fusionner le fichier local `.env.diddigo-diddipay-staging` dans les variables backend du stack
**DiddiGo** :

```env
DIDDIPAY_BASE_URL=https://pay-api-staging.diddifree.com/payfund/v1
DIDDIPAY_CLIENT_ID=diddigo
DIDDIPAY_SERVICE_KEY=<SECRET_A>
DIDDIPAY_CALLBACK_SECRET=<SECRET_B>
DIDDIPAY_HTTP_TIMEOUT_SECONDS=15
```

| Variable DiddiGo | Role | Correspondance obligatoire dans DiddiPay |
|---|---|---|
| `DIDDIPAY_BASE_URL` | adresse du contrat DiddiPay | domaine public du stack DiddiPay |
| `DIDDIPAY_CLIENT_ID` | identite stable du module | cle `diddigo` dans `PAYMENT_SERVICE_KEYS` |
| `DIDDIPAY_SERVICE_KEY` | authentifie les appels sortants DiddiGo | valeur apres `diddigo:` dans `PAYMENT_SERVICE_KEYS` |
| `DIDDIPAY_CALLBACK_SECRET` | verifie les callbacks entrants | `secret` de l'objet `diddigo` dans `PAYMENT_CALLBACK_TARGETS` |
| `DIDDIPAY_HTTP_TIMEOUT_SECONDS` | limite d'attente reseau | aucune valeur miroir |

### Egalites a respecter

```text
DiddiGo.DIDDIPAY_CLIENT_ID
    == "diddigo"
    == nom de l'entree DiddiPay.PAYMENT_SERVICE_KEYS

DiddiGo.DIDDIPAY_SERVICE_KEY
    == DiddiPay.PAYMENT_SERVICE_KEYS["diddigo"]

DiddiGo.DIDDIPAY_CALLBACK_SECRET
    == DiddiPay.PAYMENT_CALLBACK_TARGETS["diddigo"].secret

DiddiGo callback public
    == DiddiPay.PAYMENT_CALLBACK_TARGETS["diddigo"].url
```

Une seule difference de caractere provoque un `401` pour la service key ou un rejet de signature
pour le callback.

## 4. Les cinq directions HTTP a ne pas confondre

### A. Frontend vers DiddiGo

Le frontend demande a DiddiGo de commencer ou relire le paiement d'une course. Ces routes
appartiennent au contrat DiddiGo, pas au contrat DiddiPay. DiddiGo doit notamment exposer une route
de creation et une route de lecture, par exemple :

```text
POST /rides/{ride_id}/payment
GET  /rides/{ride_id}/payment
```

Les noms exacts doivent suivre le routeur existant de DiddiGo. Le frontend ne recoit jamais
`DIDDIPAY_SERVICE_KEY`, `DIDDIPAY_CALLBACK_SECRET` ou une cle Paystack.

### B. Backend DiddiGo vers DiddiPay

```text
Backend DiddiGo -> https://pay-api-staging.diddifree.com/payfund/v1
```

| Methode | Route DiddiPay | Usage DiddiGo |
|---|---|---|
| `POST` | `/payment-intents` | creer et initialiser le paiement d'une course |
| `GET` | `/payment-intents/{id}` | relire le statut normalise |
| `GET` | `/payment-intents` | lister les paiements appartenant a DiddiGo |
| `POST` | `/payment-intents/{id}/cancel` | annuler avant envoi irreversible au PSP |
| `POST` | `/payment-intents/{id}/refunds` | demander un remboursement idempotent |
| `GET` | `/payment-intents/{id}/financial-summary` | lire capture, frais, remboursements et settlement |

Headers backend obligatoires :

```http
X-Client-ID: diddigo
X-Service-Key: <DIDDIPAY_SERVICE_KEY>
```

Pour une creation ou un remboursement :

```http
Idempotency-Key: diddigo:ride:{ride_id}:collection:v1
```

DiddiFreeID authentifie l'utilisateur aupres de DiddiGo. Ensuite, le backend DiddiGo controle que
l'utilisateur a le droit de payer cette course et appelle DiddiPay avec son identite de **service**.
Le JWT utilisateur ne remplace pas `X-Service-Key` sur les routes `/payment-intents`.

### C. Navigateur vers Paystack, puis retour vers DiddiGo

DiddiPay renvoie une `next_action.url`. Le frontend ouvre cette URL Paystack. Apres le checkout,
Paystack redirige le navigateur vers le `callback_url` fourni lors de la creation :

```text
https://go-staging.diddifree.com/rides/{ride_id}/payment-return
```

Ce retour sert uniquement a reprendre l'UX. Il ne prouve jamais le succes. Le frontend relit le
statut du backend DiddiGo.

### D. Paystack vers DiddiPay

Configurer dans le dashboard Paystack :

```text
POST https://pay-api-staging.diddifree.com/payfund/v1/payments/webhooks/paystack
```

Paystack signe le corps avec `x-paystack-signature`. DiddiPay verifie la signature, deduplique
l'evenement et met a jour le `PaymentIntent`. Cette route n'est pas appelee par DiddiGo.

### E. DiddiPay vers DiddiGo

DiddiGo doit implementer et publier :

```text
POST https://go-api-staging.diddifree.com/internal/webhooks/diddipay
```

DiddiPay envoie :

```http
Content-Type: application/json
X-DiddiPay-Event-ID: <uuid evenement>
X-DiddiPay-Signature: <HMAC-SHA256 hex du corps brut>
```

DiddiGo verifie avec `DIDDIPAY_CALLBACK_SECRET`, deduplique `X-DiddiPay-Event-ID`, compare
`payment_intent_id`, `business_reference`, `amount` et `currency`, puis met a jour la course dans
une seule transaction SQL.

## 5. Sequence complete d'un paiement DiddiGo

```text
1. Frontend -> DiddiGo
   Demande de paiement de la course.

2. DiddiGo
   Recalcule le montant et verifie les droits utilisateur.

3. DiddiGo -> DiddiPay
   POST /payment-intents avec X-Client-ID, X-Service-Key et Idempotency-Key.

4. DiddiPay -> Paystack
   Initialise la transaction externe.

5. DiddiPay -> DiddiGo -> Frontend
   Retourne next_action.url sans exposer les secrets.

6. Frontend -> Paystack
   L'utilisateur termine le checkout.

7. Paystack -> DiddiPay
   POST /payments/webhooks/paystack signe.

8. DiddiPay
   Deduplique, finalise le PaymentIntent et ecrit l'evenement dans l'outbox.

9. Worker DiddiPay -> DiddiGo
   POST /internal/webhooks/diddipay signe.

10. DiddiGo
    Deduplique, verifie le montant, marque le paiement et la course dans une transaction.

11. Frontend -> DiddiGo
    GET du statut local de la course. Le frontend ne demande pas le statut directement a Paystack.
```

## 6. Ce qui ne doit jamais etre place dans le frontend

| Donnee | Backend autorise | Frontend autorise |
|---|---:|---:|
| `PAYSTACK_SECRET_KEY` | DiddiPay | non |
| `DIDDIPAY_SERVICE_KEY` | DiddiGo | non |
| `DIDDIPAY_CALLBACK_SECRET` | DiddiGo | non |
| signature callback complete dans les logs | verification DiddiGo seulement | non |
| `next_action.url` | DiddiGo peut la relayer | oui |
| statut local du paiement | DiddiGo | oui |

## 7. Verification rapide apres configuration

### DiddiPay

Confirmer les noms des clients sans imprimer les secrets :

```bash
docker exec payfund-app python -c "from payfund_app.core.config import get_settings; s=get_settings(); print({'processor': s.payment_processor_mode, 'paystack': bool(s.paystack_secret_key), 'service_clients': sorted(s.payment_service_key_map), 'callback_clients': sorted(s.payment_callback_targets)})"
```

Attendu : processeur `paystack`, cle presente, et `diddigo` dans les deux listes.

### DiddiGo

Dans le conteneur DiddiGo, verifier uniquement la presence :

```bash
python -c "import os; names=['DIDDIPAY_BASE_URL','DIDDIPAY_CLIENT_ID','DIDDIPAY_SERVICE_KEY','DIDDIPAY_CALLBACK_SECRET']; print({name: bool(os.getenv(name)) for name in names})"
```

Les quatre valeurs doivent etre `True`.

### Route callback DiddiGo

Sans signature, la route doit refuser la requete avec `401` ou `403` :

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST https://go-api-staging.diddifree.com/internal/webhooks/diddipay \
  -H 'Content-Type: application/json' \
  --data '{}'
```

- `401/403` : route presente et protection active ;
- `404` : route non implementee ou mauvais proxy ;
- `2xx` : faille, car un callback non signe a ete accepte ;
- `5xx` : route presente mais implementation/configuration a corriger.

### Authentification DiddiGo vers DiddiPay

Depuis le conteneur DiddiGo, un appel `GET /payment-intents` avec les deux headers doit retourner
`200`. Sans `X-Service-Key`, il doit retourner `401`. Ne jamais ajouter `-v` ou imprimer les headers
dans les logs CI lorsque la vraie cle est utilisee.

## 8. Jobs necessaires des deux cotes

### DiddiPay

```bash
python -m payfund_app.ops relay-payment-events --limit 100
```

Ce job livre les callbacks DiddiPay vers DiddiGo. Sans lui, le webhook Paystack peut finaliser
DiddiPay sans que DiddiGo soit notifie rapidement.

### DiddiGo

DiddiGo doit relire periodiquement ses paiements non finaux avec :

```text
GET /payfund/v1/payment-intents/{payment_intent_id}
```

Le callback apporte la rapidite. La relecture DiddiGo apporte la completude si le callback est
retarde ou temporairement impossible.

## 9. Diagnostic par symptome

| Symptome | Cause probable | Verification |
|---|---|---|
| DiddiGo recoit `401` de DiddiPay | service key ou client id differents | comparer les deux cotes sans logger le secret |
| DiddiGo retourne `401` au callback | callback secret different ou corps transforme | verifier le HMAC sur le corps brut |
| DiddiGo retourne `404` au callback | route absente ou proxy incorrect | verifier le routeur et le domaine `go-api-staging` |
| DiddiPay reussit mais DiddiGo reste pending | relay non planifie ou callback en retry | lancer `payment-events-status` et `relay-payment-events` |
| navigateur revient sur une page success mais course pending | normal avant confirmation serveur | relire DiddiGo, ne pas faire confiance au retour navigateur |
| Paystack affiche webhook `401` | secret/signature Paystack incorrect | verifier le secret charge uniquement cote DiddiPay |
| deux paiements sont crees pour une course | idempotency key regeneree pendant un retry | stocker et reutiliser la meme cle et le meme corps |

