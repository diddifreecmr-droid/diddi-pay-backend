# Brief S2S DiddiPay pour les equipes backend

Public : DiddiGo, DiddiFund, DiddiFiles et futurs modules DiddiFree. Ce brief decrit
l'integration **actuelle** de l'API PaymentIntent. Un S2S entre deux autres services doit
definir son propre contrat, ses droits et ses secrets ; `PAYMENT_SERVICE_KEYS` n'est pas une
identite universelle pour toute la plateforme.

## 1. Qui authentifie qui ?

```text
Utilisateur -> backend du module -> DiddiPay -> PSP (Paystack aujourd'hui)
                                <- callback signe DiddiPay
```

1. DiddiFreeID authentifie l'utilisateur. Le backend du module verifie ensuite ses roles locaux,
   les droits sur l'objet metier et le montant calcule dans sa propre base.
2. Le backend du module appelle DiddiPay avec son `client_id` et sa cle S2S. Le JWT utilisateur
   ne remplace pas cette cle ; le frontend ne la voit jamais.
3. DiddiPay cree et suit le PaymentIntent. Le PSP notifie DiddiPay. DiddiPay envoie un callback
   signe au module, qui met a jour son objet metier apres verification.

Le retour du navigateur depuis Paystack sert seulement a reprendre l'interface. Il ne confirme
pas le paiement. Le statut `succeeded` du PaymentIntent, recu par callback ou relu via l'API,
fait foi pour le module.

## 2. Deux secrets independants par module et par environnement

| Sens | Secret | Configure dans DiddiPay | Configure dans le module |
|---|---|---|---|
| Module -> DiddiPay | Cle S2S A | `PAYMENT_SERVICE_KEYS` | `DIDDIPAY_SERVICE_KEY` |
| DiddiPay -> module | Secret HMAC B | `PAYMENT_CALLBACK_TARGETS[client_id].secret` | `DIDDIPAY_CALLBACK_SECRET` |

Chaque module utilise un `client_id` stable, par exemple `diddigo`, `diddifund` ou
`diddifiles`. Chaque environnement (local, staging, production) a ses propres secrets longs,
aleatoires et distincts. Les stocker dans le gestionnaire de secrets de chaque stack ; ne pas
les committer, les journaliser ou les envoyer dans Flutter/JavaScript. HTTPS est requis pour les
appels externes. Un reseau prive ou des regles firewall peuvent reduire l'exposition, sans
remplacer l'authentification applicative.

Configuration illustrative dans la stack **DiddiPay** :

```env
PAYMENT_SERVICE_KEYS=diddigo:<cle-go-A>,diddifund:<cle-fund-A>
PAYMENT_CALLBACK_TARGETS={"diddigo":{"url":"https://go-staging.diddifree.com/internal/webhooks/diddipay","secret":"<secret-go-B>"},"diddifund":{"url":"http://app:8000/payfund/v1/fund/payments/webhooks/diddipay","secret":"<secret-fund-B>"}}
```

Configuration illustrative dans la stack backend **DiddiGo** :

```env
DIDDIPAY_BASE_URL=https://pay-api-staging.diddifree.com/payfund/v1
DIDDIPAY_CLIENT_ID=diddigo
DIDDIPAY_SERVICE_KEY=<cle-go-A>
DIDDIPAY_CALLBACK_SECRET=<secret-go-B>
DIDDIPAY_HTTP_TIMEOUT_SECONDS=15
```

Les noms des variables cote module sont une convention d'integration. Les noms
`PAYMENT_SERVICE_KEYS` et `PAYMENT_CALLBACK_TARGETS` sont ceux effectivement lus par DiddiPay.
Dans ce depot, le recepteur DiddiFund utilise
`DIDDIFUND_DIDDIPAY_CALLBACK_SECRET=<secret-fund-B>`.

`PAYMENT_CALLBACK_TARGETS` est un objet JSON **sur une ligne**, indexe par `client_id`.
La cible externe doit etre en HTTPS ; chaque secret de callback a au moins 16 caracteres.
Une entree dans `PAYMENT_SERVICE_KEYS` seule permet les appels S2S, mais ne configure pas
la livraison du callback. Sans cible, l'evenement reste a traiter par les operations.

## 3. Appel du backend module a DiddiPay

Pour creer un paiement :

```http
POST /payfund/v1/payment-intents HTTP/1.1
Content-Type: application/json
X-Client-ID: diddigo
X-Service-Key: <cle-go-A>
Idempotency-Key: diddigo:ride:<ride_id>:collection:v1
```

Le corps contient `business_reference`, `amount`, `currency=XOF`, le canal et les autres
champs documentes dans `DiddiPay_Contrat_API.md` et Swagger. Le montant et les identifiants
metier viennent du backend du module, pas du frontend. Conserver `business_reference`,
`Idempotency-Key` et `payment_intent_id` dans la base du module. Apres un timeout, rejouer
**la meme cle et le meme corps**. Une autre somme avec la meme cle renvoie un conflit.

La meme paire `X-Client-ID` / `X-Service-Key` protege les lectures du PaymentIntent,
l'annulation, le remboursement et le resume financier. DiddiPay renvoie `401` si la paire
est invalide. Un module ne peut pas lire le PaymentIntent d'un autre module (`404`).

Le backend module expose au frontend ses propres routes de creation/lecture de paiement et
ne lui renvoie que le statut et la `next_action` utiles. La service key reste cote serveur.

## 4. Callback signe de DiddiPay vers le module

Chaque module expose une route backend, par exemple
`POST /internal/webhooks/diddipay`. DiddiPay envoie le corps JSON et les headers
`X-DiddiPay-Event-ID` et `X-DiddiPay-Signature`. La signature est
`HMAC-SHA256(secret-B, corps_brut).hexdigest()`.

Le recepteur doit :

1. Lire les octets bruts du corps, recalculer la signature et comparer en temps constant.
2. Verifier que le header event ID correspond a `body.id`.
3. Dans une seule transaction SQL, enregistrer l'event ID avec contrainte unique et verifier
   `payment_intent_id`, `business_reference`, `amount` et `currency` contre son paiement local.
4. Appliquer la transition metier uniquement si l'evenement est valide, puis retourner `2xx`.
   Un doublon deja traite retourne aussi `2xx`, sans nouvel effet.

DiddiPay livre au moins une fois : un timeout ou une reponse non `2xx` provoque des retries,
puis une dead letter apres dix echecs. Le job DiddiPay
`python -m payfund_app.ops relay-payment-events --limit 100` doit tourner regulierement.
Le module doit aussi relire periodiquement ses paiements non finaux avec
`GET /payfund/v1/payment-intents/{id}` ; le callback peut etre retarde ou manque.

## 5. Ajouter un nouveau module

1. Choisir un `client_id` stable et reserver des secrets A et B propres au module et a
   l'environnement cible. Ne pas partager ceux de DiddiGo ou DiddiFund.
2. Ajouter `client_id:<cle-A>` a `PAYMENT_SERVICE_KEYS` dans la stack DiddiPay. Ajouter la
   cible HTTPS et `<secret-B>` a `PAYMENT_CALLBACK_TARGETS` si des evenements doivent etre recus.
3. Configurer l'URL de DiddiPay, `client_id`, `<cle-A>` et `<secret-B>` dans la stack backend
   du nouveau module. Deployer son client HTTP, sa table de liaison paiements, sa route callback
   et son job de reconciliation.
4. En staging, tester l'appel authentifie, le refus sans cle, l'isolation entre modules,
   l'idempotence, la signature invalide, le doublon et un callback retarde. Verifier les
   statuts metier et le solde comptable attendus avant la production.

Ne copier ni la cle Paystack ni un secret DiddiGo dans le nouveau module. Un module comme
DiddiFiles n'a besoin de cette configuration que s'il initie effectivement des paiements.

## 6. Verification et limites actuelles

Dans le conteneur DiddiPay, verifier les **noms** des clients sans afficher les secrets :

```bash
python -c "from payfund_app.core.config import get_settings; s=get_settings(); print({'service_clients':sorted(s.payment_service_key_map),'callback_clients':sorted(s.payment_callback_targets)})"
```

Depuis le conteneur du module, `GET /payfund/v1/payment-intents` avec les deux headers S2S
doit repondre `200` ; sans cle, `401`. Un callback sans signature doit etre refuse.
Ne pas lancer une creation de paiement reel pour un simple test de connectivite.

Aujourd'hui DiddiPay compare une cle statique par `client_id` depuis l'environnement. Il n'y
a pas encore de rotation sans interruption, de scopes fins par route, ni de mTLS. Pour remplacer
une cle, coordonner les deux deploiements et surveiller les `401` ; une evolution du mecanisme
sera necessaire pour une rotation progressive sans coupure.

References : `DiddiPay_Contrat_API.md`, `DiddiPay_DiddiGo_Env_Routes_Matrix.md`,
`DiddiPay_Backend_Integration_Brief.md` et `/payfund/v1/openapi.json`.
