# DiddiPay - Contrat API

**Version du contrat :** 3.1 - MVP orchestrateur de paiements

**Date :** 2026-08-15

**Base URL :** `/payfund/v1`

**Format :** JSON

**Devise MVP :** `XOF`
**Public cible :** backends DiddiGo, DiddiFund et futurs modules DiddiFree

Le document OpenAPI expose a `/payfund/v1/openapi.json` est le contrat HTTP executable. Swagger UI
est disponible a `/payfund/v1/docs`. Toutes les routes, tous les headers, tous les corps de requete
et toutes les reponses JSON exposees par DiddiPay doivent y apparaitre.

Ce document complete OpenAPI avec les responsabilites, les invariants financiers et les cycles de
vie qui ne peuvent pas etre exprimes uniquement par un schema.

## 1. Positionnement

DiddiPay est l'orchestrateur de paiements de la plateforme DiddiFree.

DiddiPay possede :

- le `PaymentIntent` ;
- les tentatives de paiement et leur statut normalise ;
- l'idempotence ;
- le routage vers les processeurs de paiement ;
- la reception et la deduplication des webhooks ;
- la reconciliation avec le processeur ;
- les notifications fiables vers les modules ;
- les remboursements et le suivi du settlement lorsqu'ils seront actives.

DiddiPay ne possede pas la course, l'investissement, le pret ou la commande. Le module appelant
reste la source de verite de son objet metier :

- DiddiGo possede la course ;
- DiddiFund possede l'investissement ou le pret ;
- les futurs modules possedent leurs commandes et prestations.

Paystack est le processeur externe actif du MVP. Demain, un adaptateur Orange Money, Wave, MTN MoMo
ou un autre PSP pourra etre ajoute sans modifier le contrat des modules. Un futur DiddiWallet sera
un moyen de paiement supplementaire ; il ne redefinit pas DiddiPay.

Les anciennes routes `/wallet/*` restent temporairement disponibles pour compatibilite. Elles ne
constituent plus le coeur du nouveau contrat DiddiPay et ne doivent pas etre utilisees pour une
nouvelle integration module-to-module.

Dans OpenAPI, toutes ces routes portent le tag `wallet-legacy` et `deprecated: true`. Cela signale
la direction produit sans les desactiver : elles restent necessaires aux soldes existants et a
certains flux historiques DiddiFund. Le workflow produit et les criteres de choix sont documentes
dans `docs/wallet-legacy-product-workflow.md`.

## 2. Authentification service-to-service

Les routes `/payment-intents` sont appelees par le backend du module, jamais directement par une
application mobile ou web.

Headers requis :

| Header | Requis | Description |
|---|---:|---|
| `X-Client-ID` | oui | Identifiant stable du module, par exemple `diddigo` ou `diddifund` |
| `Authorization` | oui | `Bearer <service_token>` emis par DiddiFreeID pour l'audience `diddipay` |
| `Idempotency-Key` | creation | Cle unique de l'operation metier |

Exemple de configuration backend :

```env
PAYMENT_SERVICE_CLIENT_IDS=diddigo-staging,diddisend-staging,diddifood-staging
PAYMENT_SERVICE_AUDIENCE=diddipay
PAYMENT_SERVICE_KEY_FALLBACK_ENABLED=false
```

Regles de securite :

- le jeton de service ne doit jamais etre embarque dans Flutter, JavaScript ou une application cliente ;
- chaque module obtient son propre jeton court aupres de DiddiFreeID ;
- DiddiPay verifie localement signature RS256, `iss`, `aud`, `client_id`, statut et scope ;
- `X-Service-Key` est tolere uniquement pendant la migration staging lorsque
  `PAYMENT_SERVICE_KEY_FALLBACK_ENABLED=true` ;
- un module ne peut lire que ses propres `PaymentIntent` ;
- DiddiFreeID authentifie l'utilisateur aupres du module ; le module autorise l'action metier puis
  appelle DiddiPay avec son identite de service.

## 3. Modele monetaire

- `amount` est un entier strictement positif en unite mineure.
- Pour le MVP XOF, `5000` represente `5 000 XOF`.
- Cette convention est celle du contrat DiddiPay. L'adaptateur Paystack convertit en interne
  `5000 XOF` vers `500000` pour l'API Paystack, puis reconvertit et valide le montant des retours.
  Les modules clients ne doivent jamais effectuer cette multiplication eux-memes.
- `currency` vaut actuellement `XOF`.
- Un `PaymentIntent` est mono-devise et son montant ne change pas apres creation.
- Le statut DiddiPay est la source de verite normalisee ; le statut Paystack reste un detail
  d'infrastructure non expose aux clients.

## 4. Creer un PaymentIntent

### `POST /payment-intents`

Cree une intention et initialise une tentative chez le processeur selectionne.

Headers :

```http
X-Client-ID: diddigo
Authorization: Bearer <service-token-diddifreeid>
Idempotency-Key: ride:42:collection:v1
Content-Type: application/json
```

Corps :

```json
{
  "business_reference": "ride:42",
  "processor": "paystack",
  "amount": 5000,
  "currency": "XOF",
  "payer_user_id": "7c7df66d-7345-4aa7-b818-31cd91955d5b",
  "payee_user_id": "80ed38ce-1814-4b40-99f8-1ca7e65bea90",
  "channel": "mobile_money",
  "network": "orange",
  "customer_email": "client@example.com",
  "customer_phone": "+2250700000000",
  "return_target": "app",
  "description": "Course DiddiGo 42",
  "metadata": {
    "ride_id": "42"
  }
}
```

Champs :

| Champ | Requis | Regle |
|---|---:|---|
| `business_reference` | oui | Reference stable de l'objet dans le module, 1 a 128 caracteres |
| `processor` | non | Processeur explicite (`paystack`, `pawapay`, `cinetpay`, `jeko`, `sandbox`) ; sinon `PAYMENT_PROCESSOR_MODE` |
| `amount` | oui | Entier strictement positif |
| `currency` | oui | `XOF` pour le MVP |
| `payer_user_id` | non | UUID DiddiFreeID du payeur |
| `payee_user_id` | non | UUID DiddiFreeID du beneficiaire si connu |
| `channel` | non | `mobile_money` ou `card` |
| `network` | non | `orange`, `wave` ou `mtn` ; contrainte de compatibilite du processeur choisi |
| `customer_email` | Paystack | Requis par le checkout Paystack actuel |
| `customer_phone` | non | Telephone normalise, maximum 32 caracteres |
| `return_target` | PawaPay mobile money | Nom d'une destination de retour configuree cote serveur, par exemple `app` |
| `description` | non | Libelle lisible, maximum 255 caracteres |
| `metadata` | non | Contexte metier JSON non sensible |

`processor` est absent dans la plupart des appels : DiddiPay utilise alors strictement la valeur
de `PAYMENT_PROCESSOR_MODE`. Si le module envoie `processor`, cette valeur devient le choix
explicite pour cette requete, a condition que le processeur soit configure et supporte la devise,
le sens, le canal et le reseau demandes. `network` ne selectionne jamais un autre processeur.
En cas d'incompatibilite, DiddiPay renvoie `PAYMENT_METHOD_UNAVAILABLE` sans appeler un PSP de
substitution.

Le module ne fournit jamais une URL arbitraire. Les cibles sont configurees dans
`PAYMENT_CHECKOUT_RETURN_TARGETS` avec une cle `<client_id>:<return_target>`. Le retour navigateur
n'est pas une preuve de paiement : seul le statut DiddiPay, alimente par webhook ou reconciliation,
fait foi. `callback_url` reste temporairement documente comme deprecie pour la migration staging et
doit etre desactive en production avec `PAYMENT_CALLBACK_URL_FALLBACK_ENABLED=false`.

Lorsque le processeur choisi est PawaPay et que `channel=mobile_money` avec `network` dans
`mtn`/`orange`/`moov`, l'un des deux (`return_target` ou `callback_url`) est desormais obligatoire :
PawaPay route ces collections via sa Payment Page hebergee, qui exige une URL de retour reelle.
Omettre les deux ne bloque pas la
creation (toujours `201`) mais fait echouer la tentative immediatement : intent `status=failed`,
`attempts[0].failure_code=PROCESSOR_REQUEST_REJECTED`, message `callback_url is required by
pawapay's Payment Page`. Paystack reste insensible a cette contrainte (son propre retour par defaut
s'applique si aucun des deux n'est fourni).

Reponse `201 Created` :

```json
{
  "id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84",
  "client_id": "diddigo",
  "processor": "paystack",
  "business_reference": "ride:42",
  "amount": 5000,
  "currency": "XOF",
  "status": "requires_action",
  "provider_status": "initialized",
  "failure_code": null,
  "failure_message": null,
  "payer_user_id": "7c7df66d-7345-4aa7-b818-31cd91955d5b",
  "payee_user_id": "80ed38ce-1814-4b40-99f8-1ca7e65bea90",
  "description": "Course DiddiGo 42",
  "metadata": {
    "ride_id": "42"
  },
  "refunded_amount": 0,
  "next_action": {
    "type": "redirect",
    "url": "https://checkout.paystack.com/example",
    "instructions": null,
    "expires_at": null
  },
  "attempts": [
    {
      "id": "b0198fb9-d36d-4395-9225-76c686739264",
      "processor": "paystack",
      "status": "requires_action",
      "channel": "mobile_money",
      "network": "orange",
      "provider_status": "initialized",
      "next_action": {
        "type": "redirect",
        "url": "https://checkout.paystack.com/example",
        "instructions": null,
        "expires_at": null
      },
      "failure_code": null,
      "failure_message": null,
      "created_at": "2026-08-15T10:00:00Z",
      "updated_at": "2026-08-15T10:00:00Z"
    }
  ],
  "created_at": "2026-08-15T10:00:00Z",
  "updated_at": "2026-08-15T10:00:00Z"
}
```

`provider_status`, `failure_code` et `failure_message` au niveau du `PaymentIntent` reprennent
la derniere tentative. Ils permettent au module appelant de traiter directement un echec sans
parcourir `attempts[]`; la meme information reste disponible dans la tentative pour l'audit.
`processor` indique le connecteur effectivement selectionne pour la derniere tentative. Ce champ
est informatif et ne doit pas etre utilise par un module metier pour choisir un parcours : le
module appelle DiddiPay via le contrat PaymentIntent, quelle que soit l'implementation PSP.
Une initialisation provider rejetee reste une creation HTTP `201` : le `PaymentIntent` est bien
cree, mais sa reponse porte `status=failed` et les details d'echec. Une enveloppe HTTP `error`
est reservee aux erreurs de contrat DiddiPay (authentification, validation, idempotence, etc.).

Si `PAYMENT_PROCESSOR_MODE=cinetpay` est selectionne sans `CINETPAY_API_KEY` et
`CINETPAY_API_PASSWORD`, DiddiPay renvoie `503 PROCESSOR_NOT_CONFIGURED` avec la liste des noms
de variables manquantes. Aucun appel CinetPay n'est alors tente.

### Idempotence

La cle doit etre derivee de l'action metier et conservee lors d'un retry reseau. Une nouvelle cle ne
doit pas etre generee tant que l'utilisateur repete exactement la meme tentative metier.

- meme cle et meme requete : le meme `PaymentIntent` est retourne, sans nouveau debit ;
- meme cle et requete differente : `409 IDEMPOTENCY_CONFLICT` ;
- cle absente : `422 IDEMPOTENCY_KEY_REQUIRED`.

Le module doit stocker `payment_intent_id`, `business_reference` et la cle d'idempotence avec son
objet metier.

### Inventaire HTTP du coeur DiddiPay

Toutes les requetes ci-dessous sont exposees dans Swagger, corps et headers compris :

| Methode | Route | Appelant | Usage |
|---|---|---|---|
| `POST` | `/payment-intents` | backend module | creer et initialiser un paiement |
| `GET` | `/payment-intents` | backend module | lister les paiements du module |
| `GET` | `/payment-intents/{intent_id}` | backend module | lire un paiement et ses tentatives |
| `POST` | `/payment-intents/{intent_id}/verify` | backend module autorise | verifier un paiement d'un autre module, avec bornage d'owner |
| `POST` | `/payment-intents/{intent_id}/cancel` | backend module | annuler une intention encore locale |
| `POST` | `/payment-intents/{intent_id}/refunds` | backend module | demander un remboursement idempotent |
| `GET` | `/payment-intents/{intent_id}/financial-summary` | backend module | lire capture, frais, settlement et reste attendu |
| `POST` | `/payments/webhooks/paystack` | Paystack | confirmer un evenement provider signe |
| `POST` | `/payments/webhooks/cinetpay` | CinetPay | verifier une notification de paiement puis confirmer l'intention |
| `POST` | `/webhooks/pawapay/payouts` | PawaPay | confirmer un payout, idempotent et verifie par API provider |

Ces chemins sont relatifs a `/payfund/v1`. Les anciennes routes `/wallet/*` sont documentees dans
le contrat legacy et ne font pas partie du nouveau coeur PaymentIntent.

## 5. Consulter les paiements

### `GET /payment-intents/{intent_id}`

Retourne le `PaymentIntent` et ses tentatives. Le `X-Client-ID` appelant doit en etre proprietaire.
Un identifiant inconnu ou appartenant a un autre module retourne `404 PAYMENT_INTENT_NOT_FOUND`.

### `POST /payment-intents/{intent_id}/verify`

Cette route est reservee a la verification inter-service. Elle ne donne pas a DiddiSend un acces
general aux paiements DiddiShop et elle n'accepte pas l'ancien header `X-Service-Key` : seul un
JWT S2S DiddiFreeID est accepte.

Headers :

```http
Authorization: Bearer <service-token-diddifreeid>
X-Client-ID: diddisend-staging
```

Le token doit porter `aud=diddipay`, `sub=service:diddisend` et le scope
`diddipay:payment-intents:verify`. DiddiPay
applique ensuite une allowlist serveur `PAYMENT_INTENT_VERIFY_OWNER_CLIENTS` qui associe le
client verifier aux clients proprietaires autorises. Par exemple :

```env
PAYMENT_INTENT_VERIFY_OWNER_CLIENTS={"diddisend-staging":["diddishop-staging-diddipay","diddifood-staging"]}
```

En staging, DiddiSend peut donc vérifier uniquement les intents créés par DiddiShop ou DiddiFood.
Cette liste est une décision d'autorisation DiddiPay, pas une autorisation générale de lecture.
Un token avec `sub=service:pilotage` n'est pas accepté sur cette route, même s'il porte le même
scope. Le `client_id` du token doit aussi être identique à `X-Client-ID`.

Corps :

```json
{
  "amount": 12000,
  "currency": "XOF",
  "business_reference": "diddish:order:order-123"
}
```

DiddiPay retourne `200` uniquement si l'intent appartient a un owner autorise, est exactement
`succeeded`, est en `XOF`, et correspond au montant et a la `business_reference` fournis :

```json
{
  "verified": true,
  "payment_intent_id": "uuid",
  "owner_client_id": "diddishop-staging-diddipay",
  "business_reference": "diddish:order:order-123",
  "amount": 12000,
  "currency": "XOF",
  "status": "succeeded"
}
```

Un intent inconnu ou appartenant a un owner non autorise retourne `404 PAYMENT_INTENT_NOT_FOUND`
afin de ne pas divulguer son existence. Un statut, montant ou reference incompatible retourne
`409 PAYMENT_INTENT_NOT_VERIFIED`. Aucun secret callback DiddiShop/DiddiSend n'est partage pour
cette verification : l'autorisation repose sur le JWT S2S, son identite DiddiSend, le scope et
l'allowlist DiddiPay. La route est en lecture seule et peut être appelée plusieurs fois avec les
mêmes valeurs sans modifier le PaymentIntent.

### `GET /payment-intents?limit=50`

Retourne les derniers paiements du module :

```json
{
  "data": []
}
```

`limit` accepte une valeur de `1` a `100`. Cette route sert a l'exploitation et au MVP ; une
pagination par curseur sera ajoutee avant les volumes de production eleves.

## 6. Annuler une intention

### `POST /payment-intents/{intent_id}/cancel`

Annule uniquement une tentative locale encore `pending` et jamais envoyee a un processeur. Une
fois la tentative initialisee chez Paystack, DiddiPay refuse l'annulation locale avec
`409 PAYMENT_OPERATION_CONFLICT` afin de ne pas afficher un faux etat final.

Cette route ne remplace pas un remboursement.

## 6.1 Rembourser un paiement

### `POST /payment-intents/{intent_id}/refunds`

Headers S2S habituels plus `Idempotency-Key`. Corps :

```json
{
  "amount": 2500,
  "reason": "Service annule"
}
```

Le paiement doit appartenir au module appelant et avoir reussi. DiddiPay verrouille l'intention,
additionne les remboursements `pending`, `processing` et `succeeded`, puis refuse toute demande qui
depasserait le montant capture. La cle idempotente protege contre un double remboursement lors d'un
retry reseau.

Reponse `201` :

```json
{
  "id": "2e288093-9eb4-4b87-b9e8-01e75631e2ab",
  "payment_intent_id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84",
  "amount": 2500,
  "currency": "XOF",
  "status": "processing",
  "provider_status": "processing",
  "created_at": "2026-08-15T14:00:00Z",
  "updated_at": "2026-08-15T14:00:00Z"
}
```

Paystack accepte `transaction`, `amount`, `currency` et les notes de remboursement. Une reponse
`processing` n'est pas encore un remboursement final. DiddiPay ne passe l'intention a
`partially_refunded` ou `refunded` qu'apres un resultat `succeeded`.

## 7. Statuts normalises

### PaymentIntent

| Statut | Sens |
|---|---|
| `requires_action` | Le payeur doit executer `next_action` |
| `processing` | Paiement en cours ou confirmation provider attendue |
| `succeeded` | Paiement confirme par DiddiPay |
| `failed` | Tentative terminee en echec |
| `cancelled` | Intention annulee avant traitement externe |
| `partially_refunded` | Une partie du montant a ete remboursee |
| `refunded` | Le montant capture a ete integralement rembourse |

### PaymentAttempt

Une tentative peut etre `pending`, `requires_action`, `processing`, `succeeded`, `failed`,
`cancelled` ou `unknown`.

`unknown` est un etat de securite important : DiddiPay ne sait pas encore si le processeur a accepte
l'operation. Le module ne doit ni conclure a un echec ni relancer un nouveau debit. DiddiPay doit
reconcilier cette tentative.

## 8. NextAction

Le module ne doit jamais coder une logique specifique a Paystack. Il transmet au frontend la
structure normalisee `next_action` :

Dans la reponse `PaymentIntent`, `next_action` top-level represente l'action courante a executer.
Elle est derivee de la derniere tentative et evite aux modules consommateurs de parcourir
`attempts[]` pour trouver le checkout actif. `attempts[]` reste disponible pour l'audit technique.

| Type | Comportement client |
|---|---|
| `redirect` | Ouvrir `url` dans un navigateur securise ou une WebView conforme |
| `mobile_money_prompt` | Informer l'utilisateur de confirmer sur son telephone |
| `display_instructions` | Afficher `instructions` |
| `await_confirmation` | Afficher un etat d'attente et rafraichir le statut |
| `none` | Aucune action utilisateur |

L'URL de retour navigateur ne prouve jamais le succes. Seul `status=succeeded`, obtenu depuis le
backend du module apres confirmation DiddiPay, autorise la livraison du service.

## 9. Webhook Paystack

### `POST /payments/webhooks/paystack`

Cette route est appelee uniquement par Paystack. Elle ne requiert pas les headers S2S des modules.
DiddiPay verifie `X-Paystack-Signature` sur le corps brut avant tout traitement.

Swagger documente un corps JSON provider extensible, car Paystack peut ajouter des champs. Le
backend verifie toujours la signature sur les octets bruts recus et non sur un JSON reserialize.

Reponse :

```json
{
  "status": "processed",
  "event_key": "charge.success:dpi_reference:success",
  "payment_intent_id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84"
}
```

Valeurs possibles de `status` : `processed`, `duplicate`, `ignored`, `failed`.

Invariants :

- chaque evenement provider est conserve et deduplique ;
- le montant et la devise sont compares a l'intention avant succes ;
- les donnees sensibles du payload provider ne sont pas conservees ;
- un webhook duplique ne produit pas une seconde transition metier ;
- la reconciliation couvre les webhooks manquants et les appels provider incertains.

### `POST /payments/webhooks/cinetpay`

CinetPay envoie une notification contenant notamment `notify_token`,
`merchant_transaction_id` et `transaction_id`. Cette notification est un signal et non une
preuve de paiement. DiddiPay compare l'empreinte du `notify_token` conservee lors de
l'initialisation, puis appelle l'API CinetPay de verification avant d'appliquer le statut,
le montant et la devise canoniques.

La valeur a configurer dans CinetPay est :

```ini
CINETPAY_WEBHOOK_URL=https://pay-api-staging.diddifree.com/payfund/v1/payments/webhooks/cinetpay
```

Les doublons sont dedupliques par `provider_events`. Une notification pour une tentative
ancienne sans `notify_token` persistee est rejetee et reste recuperable par reconciliation.
Les remboursements ne sont pas branches sur cet endpoint : l'API CinetPay v1 integree ne
fournit pas d'operation de remboursement.

### Callback vers le module proprietaire

Apres une transition financiere a notifier, DiddiPay livre une enveloppe signee a l'URL configuree
pour le `client_id` :

```json
{
  "id": "e3474d21-15fe-43d1-916d-d151bcd78e0a",
  "type": "payment.succeeded",
  "occurred_at": "2026-08-15T10:00:04+00:00",
  "data": {
    "event_id": "charge.success:dpi_reference:success",
    "payment_intent_id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84",
    "business_reference": "ride:42",
    "amount": 5000,
    "currency": "XOF",
    "status": "succeeded"
  }
}
```

Headers :

```http
Content-Type: application/json
X-DiddiPay-Event-ID: e3474d21-15fe-43d1-916d-d151bcd78e0a
X-DiddiPay-Signature: <hmac-sha256-hex-du-corps-brut>
```

Le secret HMAC est distinct de `X-Service-Key`. Le destinataire doit verifier la signature sur les
octets bruts avant de parser le JSON, imposer une contrainte unique sur `id`, traiter la mise a jour
metier et l'insertion de l'evenement dans une meme transaction, puis retourner un code `2xx`.

La livraison est **at least once** : un meme `id` peut etre recu plusieurs fois. Un doublon valide
et deja traite doit retourner `2xx` sans rejouer les effets metier. Un timeout ou une reponse non
`2xx` provoque un retry avec backoff exponentiel, puis une dead letter apres le nombre maximal de
tentatives.

Configuration DiddiPay :

```env
PAYMENT_CALLBACK_TARGETS={"diddigo":{"url":"https://go-staging.diddifree.com/internal/webhooks/diddipay","secret":"replace-with-a-long-random-secret"}}
```

Commande de relay a executer dans un worker ou job interne :

```bash
python -m payfund_app.ops relay-payment-events --limit 100
```

Le relay claim chaque evenement en base avant l'appel HTTP. Le statut `delivering` possede une
lease de cinq minutes : un autre worker ne peut pas livrer simultanement le meme evenement, mais un
claim abandonne par un processus mort redevient eligible apres expiration. Un event en echec revient
en `pending` avec backoff ; apres dix echecs il passe en `dead_letter`.

Commande de supervision :

```bash
python -m payfund_app.ops payment-events-status
```

Elle retourne les compteurs `pending`, `delivering`, `delivered` et `dead_letter`, avec un code de
sortie non nul si une dead letter exige une intervention.

## 10. Cycle de vie

1. Le frontend demande au backend du module de payer son objet metier.
2. Le module verifie le JWT DiddiFreeID, les roles locaux, le montant et l'etat de l'objet.
3. Le module cree un `PaymentIntent` avec une cle d'idempotence stable.
4. DiddiPay choisit un processeur et renvoie un `next_action` provider-neutral.
5. Le frontend execute cette action.
6. Paystack notifie DiddiPay de maniere asynchrone.
7. DiddiPay verifie, deduplique et met a jour son statut.
8. DiddiPay notifie durablement le module ; le module traite l'evenement de maniere idempotente.
9. Le frontend lit l'etat de la course, de l'investissement ou de la commande depuis le module.
10. La reconciliation repare un callback absent ou un resultat incertain.

## 11. Erreurs

Envelope standard :

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Message lisible",
    "details": null
  }
}
```

Erreurs principales :

| HTTP | Code | Signification |
|---:|---|---|
| `401` | `UNAUTHENTICATED` | Identifiants de service invalides |
| `404` | `PAYMENT_INTENT_NOT_FOUND` | Intention absente ou non visible par ce module |
| `409` | `IDEMPOTENCY_CONFLICT` | Cle reutilisee avec une autre requete |
| `409` | `PAYMENT_OPERATION_CONFLICT` | Operation impossible dans l'etat courant |
| `422` | `IDEMPOTENCY_KEY_REQUIRED` | Header d'idempotence absent |
| `422` | `PAYMENT_METHOD_UNAVAILABLE` | Aucun adaptateur ne couvre la combinaison demandee |
| `422` | validation FastAPI | Corps, UUID, enum ou limite invalide |

## 12. Sante et exploitation

- `GET /health` : processus API vivant ;
- `GET /ready` : dependances necessaires disponibles ;
- le demarrage Docker execute les migrations Alembic avant Uvicorn ;
- la cle Paystack reste uniquement dans les secrets backend ;
- les logs ne doivent contenir ni cle service, ni cle Paystack, ni donnees de carte, ni OTP, ni PIN.
- les callbacks externes configures doivent utiliser HTTPS ; HTTP n'est permis que pour les noms
  internes Docker et localhost ;
- les logs de cycle de vie sont structures en JSON et references par `payment_intent_id`,
  `business_reference` ou `event_key`, jamais par un secret.

### Sous-ledger financier et settlement

Chaque capture confirmee produit un journal double entree immuable : debit de
`processor_receivable:<provider>` et credit de `module_payable:<client_id>`. Les frais PSP, les
remboursements et les settlements ont leurs propres journaux idempotents. Chaque journal contient
exactement un debit et un credit du meme montant et de la meme devise.

Le module proprietaire peut consulter :

```http
GET /payment-intents/{intent_id}/financial-summary
X-Client-ID: diddigo
Authorization: Bearer <service-token-diddifreeid>
```

La reponse distingue `gross_captured`, `refunded`, `processor_fees`, `net_expected`, `settled` et
`outstanding`. Un paiement `succeeded` signifie que le client a paye ; `outstanding > 0` signifie
que le rapprochement vers le compte bancaire DiddiFree n'est pas encore complet.

En attendant l'import automatique des rapports de settlement Paystack, les ops peuvent enregistrer
un versement rapproche avec :

```bash
python -m payfund_app.ops record-payment-settlement \
  <payment_intent_id> <amount> <settlement_reference>
```

La commande refuse tout montant superieur au net encore attendu et journalise l'operation.

## 13. Capacites MVP et limites actuelles

Disponible :

- collections XOF ;
- checkout Paystack par carte ou Mobile Money selon disponibilite du compte marchand ;
- sandbox local sans cle Paystack ;
- webhooks signes, deduplication et reconciliation ;
- isolation des paiements par module ;
- outbox transactionnelle pour les evenements de succes ;
- remboursements provider-neutral routes vers Paystack ;
- sous-ledger double entree pour captures, frais, remboursements et settlements manuels ;
- vue financiere par paiement et commandes ops de supervision ;
- payout/retrait via le nouveau coeur orchestrateur, agnostique au rail — voir le contrat
  dedie [`docs/diddipay-payouts-contract-v1.md`](docs/diddipay-payouts-contract-v1.md) pour
  le detail et les limites actuelles (seul `mobile_money` mtn/orange/moov est reellement
  cable aujourd'hui, pas Wave).

Pas encore a considerer comme disponible tant que les sprints correspondants ne sont pas livres :

- import automatique et rapprochement global des rapports de settlement Paystack ;
- adaptateurs directs (hors agrégateur) Orange Money, Wave ou MTN MoMo pour les collections ;
- wallet comme moyen de paiement du nouvel orchestrateur.

Le retrait du wallet historique `/payfund/v1/wallet/withdraw` est distinct du futur payout du
coeur PaymentIntent. Sous `PAYMENT_GATEWAY_MODE=paystack`, il retourne
`422 WITHDRAWAL_NOT_SUPPORTED` sans réserver les fonds : l'adaptateur Paystack wallet ne sait
initialiser que les dépôts. Les modes `stub`, `sandbox_orange_money` et `sandbox_wave` simulent
le retrait et ne constituent pas une preuve de versement externe.

Un adaptateur `PAYMENT_GATEWAY_MODE=pawapay` existe (`wallet/infra/pawapay_gateway.py`) et prend
en charge dépôt **et** retrait réels via PawaPay Mobile Money (MTN, Orange, Moov selon le pays du
numéro — Bénin, Burkina Faso, Côte d'Ivoire, Sénégal aujourd'hui) : la limitation
`WITHDRAWAL_NOT_SUPPORTED` ci-dessus ne s'applique pas à ce mode. Code prêt et testé, **pas encore
activé en production** — le réglage `PAYMENT_GATEWAY_MODE` déployé reste celui configuré dans
Portainer, à changer explicitement pour bénéficier de PawaPay.

Chaque transaction wallet persiste désormais le processeur qui l'a traitée
(`Transaction.gateway_mode`, nullable — `NULL` sur les lignes antérieures à ce champ signifie
`paystack`, seul mode ayant jamais tourné en production). La réconciliation manuelle
(`POST /payfund/v1/wallet/ops/wallet/reconcile/{transaction_id}`) résout donc le bon adaptateur
par transaction et fonctionne pour n'importe quel processeur enregistré, dépôt ou retrait — plus
seulement Paystack en dépôt. L'ancien chemin
`POST /payfund/v1/wallet/ops/paystack/reconcile/{transaction_id}` reste disponible, comportement
identique, pour ne pas casser un appelant existant.
