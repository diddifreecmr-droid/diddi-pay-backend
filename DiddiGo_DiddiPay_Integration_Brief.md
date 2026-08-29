# DiddiGo - Brief d'integration DiddiPay

**Version :** 1.0  
**Public :** equipe backend DiddiGo, equipe mobile/web DiddiGo, DevOps et QA  
**Contrat executable DiddiPay :** `/payfund/v1/openapi.json`  
**Swagger DiddiPay :** `/payfund/v1/docs`

## 1. Objectif

DiddiGo utilise DiddiPay pour encaisser les paiements numeriques d'une course sans dependre
directement de Paystack. Aujourd'hui, DiddiPay choisit Paystack. Demain, il pourra choisir Wave,
Orange Money ou un autre PSP sans modifier le coeur metier de DiddiGo.

Le flux cible est :

```text
passager -> application DiddiGo -> backend DiddiGo -> DiddiPay -> Paystack
                                                    <- webhook Paystack
                              <- callback signe DiddiPay
```

Le paiement cash n'entre pas dans ce flux. Une course payee en especes reste geree et tracee par
DiddiGo et ne cree pas de `PaymentIntent` DiddiPay.

## 2. Responsabilites

| Service | Responsabilite et source de verite |
|---|---|
| DiddiFreeID | identite, authentification et identifiant global de l'utilisateur |
| DiddiGo | course, passager, chauffeur, prix final, mode de paiement et statut metier de la course |
| DiddiPay | `PaymentIntent`, idempotence, choix du PSP, tentatives, webhook, statut financier et reconciliation |
| Paystack | execution actuelle du rail de paiement externe |

Invariants :

- DiddiGo calcule le prix en base et n'accepte jamais le montant du frontend comme autoritaire.
- DiddiPay ne modifie jamais directement une course.
- DiddiGo ne lit jamais la base DiddiPay.
- Une redirection Paystack ou une page `success` ne prouve jamais que le paiement a reussi.
- Seul un `PaymentIntent.status == "succeeded"` confirme l'encaissement.
- Le paiement cash ne doit jamais etre simule par un PaymentIntent numerique.

## 3. Configuration des environnements

### Dans DiddiPay

```env
PAYMENT_SERVICE_KEYS=diddigo:<service-key-diddigo>,diddifund:<service-key-diddifund>
PAYMENT_CALLBACK_TARGETS={"diddigo":{"url":"https://go-api-staging.diddifree.com/internal/webhooks/diddipay","secret":"<callback-secret-diddigo>"}}
PAYMENT_PROCESSOR_MODE=paystack
PAYSTACK_SECRET_KEY=<cle-secrete-paystack-de-l-environnement>
PAYSTACK_WEBHOOK_SECRET=<secret-utilise-pour-verifier-la-signature-paystack>
```

### Dans DiddiGo

```env
DIDDIPAY_BASE_URL=https://pay-api-staging.diddifree.com/payfund/v1
DIDDIPAY_CLIENT_ID=diddigo
DIDDIPAY_SERVICE_KEY=<meme-service-key-diddigo-configuree-dans-diddipay>
DIDDIPAY_CALLBACK_SECRET=<meme-callback-secret-diddigo-configure-dans-diddipay>
DIDDIPAY_HTTP_TIMEOUT_SECONDS=15
```

Les deux secrets ont des directions opposees :

- `DIDDIPAY_SERVICE_KEY` permet a DiddiGo de s'authentifier aupres de DiddiPay ;
- `DIDDIPAY_CALLBACK_SECRET` permet a DiddiGo d'authentifier un callback recu de DiddiPay.

Ils doivent etre longs, aleatoires, differents entre eux et differents pour local, staging et
production. Ils restent exclusivement dans les backends et le gestionnaire de secrets. Ils ne
doivent jamais etre livres dans l'application mobile, envoyes au navigateur, logs ou commits.

Dans l'adaptateur actuel, `PAYSTACK_WEBHOOK_SECRET` retombe sur `PAYSTACK_SECRET_KEY` lorsqu'il est
vide. Si cette variable est definie explicitement, sa valeur doit correspondre au secret avec lequel
Paystack signe reellement ses webhooks ; une valeur aleatoire independante ferait echouer toutes les
signatures.

## 4. Donnees a ajouter dans DiddiGo

DiddiGo doit posseder une table de liaison, par exemple `ride_payments` :

| Champ | Regle |
|---|---|
| `id` | UUID interne DiddiGo |
| `ride_id` | FK vers la course |
| `payment_intent_id` | UUID DiddiPay, unique lorsqu'il est renseigne |
| `business_reference` | reference metier stable, unique |
| `idempotency_key` | cle de creation stable, unique |
| `method` | `cash` ou `digital` |
| `amount` | entier XOF calcule par DiddiGo |
| `currency` | `XOF` pour le MVP |
| `status` | statut local normalise |
| `paid_at` | nullable, renseigne seulement apres succes confirme |
| `created_at`, `updated_at` | timestamps techniques |

Statuts locaux recommandes pour un paiement numerique :

```text
pending -> requires_action -> processing -> succeeded
                                      \-> failed
pending -> cancelled
succeeded -> partially_refunded -> refunded
```

Ajouter aussi une table `diddipay_callback_inbox` :

| Champ | Regle |
|---|---|
| `event_id` | UUID, cle primaire ou contrainte unique |
| `event_type` | type de l'evenement |
| `payload` | JSON recu, sans secret |
| `received_at` | date de reception |
| `processed_at` | date de traitement |

La contrainte unique sur `event_id` rend le callback idempotent.

## 5. API que DiddiGo expose a son frontend

Les noms exacts restent un choix DiddiGo. Le minimum recommande est :

```text
POST /rides/{ride_id}/payments/digital
GET  /rides/{ride_id}/payment
```

`POST /rides/{ride_id}/payments/digital` doit :

1. verifier le JWT DiddiFreeID ;
2. verifier que l'utilisateur est le passager de la course ;
3. verifier que la course peut etre payee ;
4. relire le prix final depuis la base DiddiGo ;
5. creer ou relire la ligne `ride_payments` ;
6. appeler DiddiPay depuis le backend ;
7. conserver la reponse ;
8. retourner uniquement la vue normalisee utile au frontend.

Exemple de requete frontend vers DiddiGo :

```json
{
  "channel": "mobile_money",
  "network": "orange"
}
```

Le frontend ne transmet ni le prix, ni `payer_user_id`, ni `payee_user_id`, ni une service key.
DiddiGo tire ces valeurs de son propre contexte authentifie et de sa base.

Exemple de reponse DiddiGo vers le frontend :

```json
{
  "ride_id": "6986b0be-e205-41b7-b57e-4a52e9625385",
  "payment_intent_id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84",
  "status": "requires_action",
  "amount": 5000,
  "currency": "XOF",
  "next_action": {
    "type": "redirect",
    "url": "https://checkout.paystack.com/example",
    "instructions": null,
    "expires_at": null
  }
}
```

## 6. Creer un PaymentIntent dans DiddiPay

### Requete

```http
POST /payfund/v1/payment-intents HTTP/1.1
Content-Type: application/json
X-Client-ID: diddigo
X-Service-Key: <service-key-diddigo>
Idempotency-Key: diddigo:ride:6986b0be-e205-41b7-b57e-4a52e9625385:collection:v1
```

```json
{
  "business_reference": "diddigo:ride:6986b0be-e205-41b7-b57e-4a52e9625385",
  "amount": 5000,
  "currency": "XOF",
  "payer_user_id": "ca74efee-4073-47ad-bdbd-8da62fc87d10",
  "payee_user_id": "981a3188-544e-451e-a5ab-7c3b8ae8c0dd",
  "channel": "mobile_money",
  "network": "orange",
  "customer_email": "passager@example.com",
  "customer_phone": "+2250700000000",
  "callback_url": "https://go-staging.diddifree.com/rides/6986b0be-e205-41b7-b57e-4a52e9625385/payment-return",
  "description": "Paiement course DiddiGo",
  "metadata": {
    "ride_id": "6986b0be-e205-41b7-b57e-4a52e9625385"
  }
}
```

Notes importantes :

- le montant est un entier positif ; le MVP accepte seulement `XOF` ;
- en mode Paystack actuel, `customer_email` est obligatoire pour initialiser le checkout ;
- `channel` accepte actuellement `mobile_money` ou `card` ;
- `network` accepte `orange`, `wave` ou `mtn`, mais reste actuellement une preference informative :
  le checkout Paystack heberge peut laisser le payeur choisir un autre reseau disponible ;
- `callback_url` est l'URL de retour du navigateur apres le checkout, pas le callback serveur signe ;
- le callback serveur DiddiPay vers DiddiGo vient de `PAYMENT_CALLBACK_TARGETS` ;
- `metadata` ne doit contenir ni token, ni secret, ni PIN, ni donnee de carte.

### Cle d'idempotence

La cle doit etre creee et stockee avant l'appel reseau. Pour une meme operation metier, tous les
retries reutilisent exactement la meme cle et exactement le meme corps.

```text
diddigo:ride:{ride_id}:collection:v1
```

Si DiddiGo subit un timeout, il ne fabrique pas `v2`. Il rejoue `v1`. DiddiPay renvoie le meme
PaymentIntent si la premiere requete a atteint le serveur. Une meme cle avec un corps different
retourne `409 IDEMPOTENCY_CONFLICT`.

Une nouvelle version est reservee a une nouvelle tentative explicitement autorisee apres un echec
final, selon la politique produit DiddiGo.

### Reponse initiale

DiddiGo conserve au minimum `id`, `business_reference`, `amount`, `currency`, `status` et les
timestamps. Il transmet au frontend la derniere `next_action` utile sans exposer de donnee provider
interne.

Le frontend traite `next_action.type` de maniere generique :

| Type | Action frontend |
|---|---|
| `redirect` | ouvrir `url` dans un navigateur securise |
| `mobile_money_prompt` | demander une confirmation sur le telephone |
| `display_instructions` | afficher `instructions` |
| `await_confirmation` | afficher une attente et relire DiddiGo |
| `none` | aucune action utilisateur |

## 7. Retour checkout et confirmation

Au retour du checkout, le frontend appelle uniquement DiddiGo :

```text
GET /rides/{ride_id}/payment
```

DiddiGo retourne son statut local. Si le paiement est encore non final, le backend peut relire :

```http
GET /payfund/v1/payment-intents/{payment_intent_id}
X-Client-ID: diddigo
X-Service-Key: <service-key-diddigo>
```

Le frontend ne doit jamais appeler DiddiPay directement. Il ne doit jamais envoyer a DiddiGo un
parametre `success=true` pour faire passer la course a l'etat paye.

## 8. Callback signe DiddiPay vers DiddiGo

DiddiGo implemente :

```text
POST /internal/webhooks/diddipay
```

Exemple d'enveloppe actuelle :

```json
{
  "id": "e3474d21-15fe-43d1-916d-d151bcd78e0a",
  "type": "payment.succeeded",
  "occurred_at": "2026-08-15T10:00:04+00:00",
  "data": {
    "event_id": "charge.success:dpi_reference:success",
    "payment_intent_id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84",
    "business_reference": "diddigo:ride:6986b0be-e205-41b7-b57e-4a52e9625385",
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

Ordre de traitement obligatoire :

1. lire les octets bruts du corps ;
2. calculer `HMAC-SHA256(callback_secret, raw_body).hexdigest()` ;
3. comparer la signature en temps constant ;
4. parser le JSON seulement apres verification ;
5. verifier que le header `X-DiddiPay-Event-ID` est egal a `body.id` ;
6. ouvrir une transaction SQL ;
7. inserer `body.id` dans `diddipay_callback_inbox` avec contrainte unique ;
8. retrouver la course avec `business_reference` et `payment_intent_id` ;
9. verifier strictement `amount` et `currency` ;
10. marquer le paiement local `succeeded` et la course payee ;
11. inserer un evenement dans l'outbox DiddiGo si necessaire ;
12. commit puis retourner `204` ou un autre `2xx`.

Pseudo-code de verification :

```python
expected = hmac.new(
    settings.diddipay_callback_secret.encode("utf-8"),
    raw_body,
    hashlib.sha256,
).hexdigest()

if not hmac.compare_digest(received_signature, expected):
    raise InvalidSignature()
```

La livraison est **at least once**. Un evenement deja present dans l'inbox doit retourner `2xx`
sans rejouer la transition metier. Une signature invalide retourne `401` ou `403`. Une erreur
temporaire retourne `5xx` pour demander un retry.

Transaction recommandee :

```text
BEGIN
  INSERT diddipay_callback_inbox(event_id) ON CONFLICT -> duplicate
  UPDATE ride_payments SET status = 'succeeded', paid_at = now()
  UPDATE rides SET payment_status = 'paid'
  INSERT diddigo_outbox(event = 'ride.payment_confirmed')
COMMIT
```

## 9. Reconciliation et reprise apres panne

Le callback apporte la rapidite, mais il ne suffit pas seul. DiddiGo doit executer un job periodique
qui relit les paiements locaux non finaux :

```text
requires_action, processing ou statut incertain
```

Pour chaque ligne, le job appelle `GET /payment-intents/{id}`, verifie l'identifiant, la reference,
le montant et la devise, puis execute la meme fonction idempotente de transition que le callback.

Politique de depart recommandee :

- relire rapidement pendant les premieres minutes apres l'initialisation ;
- espacer ensuite les lectures avec backoff ;
- ne jamais creer automatiquement un second debit si l'etat est incertain ;
- alerter les ops lorsqu'un paiement reste non final au-dela du delai produit ;
- conserver des metriques sur les callbacks invalides, doublons et paiements bloques.

DiddiPay doit de son cote executer son relay d'outbox dans un worker ou un job recurrent :

```bash
python -m payfund_app.ops relay-payment-events --limit 100
```

Sans ce relay, le webhook Paystack peut finaliser DiddiPay, mais le callback ne sera pas livre a
DiddiGo. Le job de reconciliation DiddiGo reste alors le filet de securite.

## 10. Erreurs a traiter

Le format d'erreur DiddiPay est :

```json
{
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "Cette cle d'idempotence a deja ete utilisee avec une autre requete.",
    "details": null
  }
}
```

| HTTP | Code ou situation | Comportement DiddiGo |
|---|---|---|
| `401` | credentials S2S invalides | ne pas retry en boucle, alerter la configuration |
| `404` | `PAYMENT_INTENT_NOT_FOUND` | verifier le mapping et le bon `client_id` |
| `409` | `IDEMPOTENCY_CONFLICT` | incident de payload ou de cle, ne pas changer la cle silencieusement |
| `409` | `PAYMENT_OPERATION_CONFLICT` | respecter l'etat courant, ne pas forcer l'annulation |
| `422` | `IDEMPOTENCY_KEY_REQUIRED` | corriger le client DiddiGo |
| `422` | `PAYMENT_METHOD_UNAVAILABLE` | proposer un moyen supporte ou signaler l'indisponibilite |
| `5xx` | panne temporaire | retry avec backoff et meme cle/corps |
| timeout | resultat inconnu | retry avec meme cle/corps, jamais un nouveau debit |

## 11. Remboursement

Le remboursement part de DiddiGo apres validation de ses propres regles d'annulation :

```http
POST /payfund/v1/payment-intents/{payment_intent_id}/refunds
X-Client-ID: diddigo
X-Service-Key: <service-key-diddigo>
Idempotency-Key: diddigo:ride:{ride_id}:refund:{refund_operation_id}
Content-Type: application/json
```

```json
{
  "amount": 5000,
  "reason": "Course annulee"
}
```

Un remboursement `processing` n'est pas encore final. DiddiGo ne doit pas afficher `rembourse`
avant confirmation. Le montant cumule des remboursements ne peut pas depasser le montant capture.

Limite MVP actuelle : DiddiPay sait initialiser un remboursement Paystack et traiter une reponse
immediate finale, mais ne fournit pas encore de route `GET /refunds/{id}` ni de reconciliation
asynchrone des remboursements restes `processing`. Tant que ce complement n'est pas implemente et
teste, DiddiGo doit placer ces cas en revue ops et ne pas automatiser la transition finale de la
course sur la seule reponse `processing`.

## 12. Observabilite minimale

Chaque log DiddiGo lie au paiement doit permettre une correlation avec :

- `ride_id` ;
- `business_reference` ;
- `payment_intent_id` ;
- `idempotency_key` ou son empreinte non reversible ;
- `diddipay_event_id` pour un callback ;
- le statut avant et apres.

Ne jamais logger la service key, le callback secret, le JWT, une signature complete, un PIN ou des
donnees de carte. Ajouter des alertes sur les paiements non finaux trop anciens, les callbacks en
echec, les ecarts montant/devise et les erreurs d'authentification S2S.

## 13. Plan d'implementation DiddiGo

### Slice 1 - Client et persistance

- ajouter les variables d'environnement et leur validation au demarrage ;
- creer `ride_payments` avec contraintes uniques ;
- implementer un client HTTP DiddiPay avec timeout ;
- implementer `POST /rides/{id}/payments/digital` ;
- tester creation, replay idempotent, timeout et conflit.

### Slice 2 - Parcours frontend

- retourner la `next_action` normalisee ;
- implementer `GET /rides/{id}/payment` ;
- ouvrir les redirections sans interpreter leur resultat comme un succes ;
- verifier que les credentials S2S ne sont jamais exposes.

### Slice 3 - Callback fiable

- implementer `/internal/webhooks/diddipay` ;
- verifier le HMAC sur le corps brut ;
- ajouter l'inbox idempotente ;
- appliquer inbox + paiement + course + outbox dans une transaction ;
- tester signature invalide, doublon, montant incorrect et crash/retry.

### Slice 4 - Reconciliation et exploitation

- ajouter le job de relecture des paiements non finaux ;
- ajouter backoff, age maximal et alertes ;
- configurer le relay d'outbox DiddiPay ;
- produire les dashboards et le runbook d'incident.

### Slice 5 - Validation staging

- paiement Mobile Money sandbox initialise depuis une vraie course de test ;
- meme requete rejouee sans second PaymentIntent ;
- callback valide recu et traite une seule fois ;
- callback duplique sans double transition ;
- callback invalide refuse ;
- retour checkout sans webhook ne marque pas la course payee ;
- reconciliation corrige un callback volontairement bloque ;
- isolation verifiee : la cle DiddiFund ne peut pas lire un paiement DiddiGo ;
- paiement cash verifie sans aucun appel DiddiPay.
- remboursement `processing` conserve comme non final et signale aux ops.

## 14. Definition of Done

L'integration est prete lorsque :

- le montant vient uniquement de DiddiGo ;
- les secrets sont uniquement cote serveur ;
- l'idempotence resiste aux timeouts et aux doubles clics ;
- le frontend reste provider-agnostic ;
- seule une confirmation DiddiPay fait passer la course a `paid` ;
- le callback est signe, deduplique et applique atomiquement ;
- la reconciliation couvre un callback absent ;
- le cash ne passe pas par DiddiPay ;
- les tests automatises et le parcours staging complet passent.
