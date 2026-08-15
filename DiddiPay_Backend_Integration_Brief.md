# DiddiPay - Brief d'integration backend

**Version :** 3.0 - orchestrateur PaymentIntent

Ce document s'adresse aux equipes backend DiddiGo, DiddiFund et aux futurs modules DiddiFree. Les
schemas HTTP exacts restent dans Swagger et dans `DiddiPay_Contrat_API.md`.

## 1. Responsabilites

Le partage correct des responsabilites est le suivant :

| Service | Source de verite |
|---|---|
| DiddiFreeID | identite globale, authentification, statut du compte |
| DiddiGo | course, chauffeur/passager, prix final, livraison du trajet |
| DiddiFund | campagne, investissement, pret, echeancier |
| DiddiPay | PaymentIntent, tentative, idempotence, provider, webhook, reconciliation |
| Paystack | execution du rail externe actuel |

DiddiPay ne doit pas connaitre les transitions internes d'une course ou d'un investissement. Le
module ne doit pas conclure qu'un paiement a reussi a partir d'une redirection Paystack.

## 2. Frontiere reseau

L'appel a DiddiPay est strictement service-to-service :

```text
application cliente -> backend du module -> DiddiPay -> Paystack
```

Le backend du module :

1. verifie le JWT DiddiFreeID ;
2. verifie ses roles locaux et l'etat de son objet metier ;
3. calcule lui-meme le montant ;
4. appelle `/payfund/v1/payment-intents` avec `X-Client-ID`, `X-Service-Key` et
   `Idempotency-Key` ;
5. ne renvoie au frontend que les donnees utiles, notamment `next_action`.

`X-Service-Key` ne doit jamais traverser le backend du module vers Flutter ou le navigateur.

## 3. Integration DiddiGo

### Donnees a conserver cote DiddiGo

La table ou l'agregat de paiement de course doit au minimum conserver :

- `ride_id` ;
- `payment_intent_id` avec contrainte unique ;
- `business_reference`, par exemple `ride:42` ;
- `idempotency_key` avec contrainte unique ;
- `payment_status` normalise ;
- `amount` et `currency` attendus ;
- `paid_at` nullable ;
- les timestamps techniques.

DiddiGo ne stocke pas la reference interne Paystack et ne lit jamais les tables DiddiPay.

### Creation

Exemple de logique :

```python
def start_ride_payment(ride, authenticated_user):
    assert ride.passenger_id == authenticated_user.id
    assert ride.status == "awaiting_payment"

    idempotency_key = f"ride:{ride.id}:collection:v1"
    intent = diddipay.create_payment_intent(
        business_reference=f"ride:{ride.id}",
        amount=ride.final_price_xof,
        currency="XOF",
        payer_user_id=authenticated_user.id,
        payee_user_id=ride.driver_id,
        idempotency_key=idempotency_key,
        metadata={"ride_id": str(ride.id)},
    )
    save_payment_link(ride, intent, idempotency_key)
    return frontend_payment_view(intent)
```

Le prix envoye vient de la base DiddiGo. Ne jamais accepter comme autoritaire un montant calcule
ou modifie par le frontend.

### Reprise apres timeout

Si l'appel de creation expire, DiddiGo reutilise exactement la meme cle et le meme payload. Il ne
cree pas une nouvelle cle. DiddiPay retournera le meme `PaymentIntent` si la premiere requete avait
reussi.

Si DiddiGo a perdu la reponse avant de conserver l'identifiant, il peut rejouer la creation avec la
meme cle. C'est la raison pour laquelle la cle doit etre deterministe et stockee avec la course.

## 4. Endpoint callback a implementer dans DiddiGo

Endpoint recommande :

```text
POST /internal/webhooks/diddipay
```

Il recoit l'enveloppe documentee dans le contrat DiddiPay et les headers :

- `X-DiddiPay-Event-ID` ;
- `X-DiddiPay-Signature`.

Ordre obligatoire :

1. lire le corps brut ;
2. calculer `HMAC-SHA256(secret, raw_body).hexdigest()` ;
3. comparer en temps constant avec `X-DiddiPay-Signature` ;
4. parser le JSON uniquement apres verification ;
5. verifier que le header event id est identique a `body.id` ;
6. ouvrir une transaction SQL ;
7. inserer `body.id` dans une inbox avec contrainte unique ;
8. retrouver le paiement par `payment_intent_id` et `business_reference` ;
9. verifier `amount` et `currency` ;
10. marquer le paiement et la course comme payes ;
11. commit ;
12. retourner `204` ou un autre `2xx`.

Pseudo-code de verification :

```python
expected = hmac.new(callback_secret.encode(), raw_body, hashlib.sha256).hexdigest()
if not hmac.compare_digest(received_signature, expected):
    raise InvalidSignature()
```

Si l'`id` existe deja dans l'inbox, DiddiGo retourne `2xx` sans rejouer la transition. Cette
deduplication est obligatoire parce que DiddiPay garantit une livraison **at least once**, pas
exactement une fois.

## 5. Transaction inbox + metier

Le bon pattern DiddiGo est :

```text
BEGIN
  INSERT callback_inbox(event_id) ON CONFLICT -> duplicate
  UPDATE ride_payments SET status = 'succeeded'
  UPDATE rides SET status = 'paid'
  INSERT diddigo_outbox(event='ride.payment_confirmed')
COMMIT
```

Ainsi, un crash ne peut pas enregistrer l'evenement sans payer la course, ni payer la course sans
memoriser l'evenement. Si DiddiGo publie ensuite un evenement interne, sa propre outbox lui donne la
meme garantie.

## 6. Statuts et decisions DiddiGo

- `requires_action` : transmettre l'action au frontend.
- `processing` : attendre le callback ou relire DiddiPay.
- `succeeded` : autoriser la transition metier payee.
- `failed` : autoriser une nouvelle tentative explicite selon les regles DiddiGo.
- `unknown` sur une tentative : ne pas relancer automatiquement un debit.
- `refunded` : appliquer la politique d'annulation/remboursement de la course.

Le statut du paiement vient de DiddiPay. Le statut de la course vient de DiddiGo. Ils sont relies,
mais ce ne sont pas le meme agregat.

## 7. Reconciliation cote module

Les callbacks peuvent etre retardes. DiddiGo doit disposer d'un job qui relit periodiquement les
paiements locaux non finaux via `GET /payment-intents/{id}`.

Ce job :

- cible uniquement les paiements `requires_action`, `processing` ou incertains ;
- applique les memes validations montant/devise ;
- reutilise le meme service client ;
- execute la meme fonction idempotente de transition que le callback ;
- alerte les ops si un paiement reste incoherent trop longtemps.

Le callback donne la rapidite. La reconciliation donne la completude.

## 8. Configuration DiddiPay pour DiddiGo

Credentials d'appel :

```env
PAYMENT_SERVICE_KEYS=diddigo:<secret-appel-diddigo>,diddifund:<secret-appel-diddifund>
```

Destination callback :

```env
PAYMENT_CALLBACK_TARGETS={"diddigo":{"url":"https://go-api.diddifree.com/internal/webhooks/diddipay","secret":"<secret-hmac-callback-distinct>"}}
```

Les deux secrets ont des usages differents :

- service key : DiddiGo s'authentifie lorsqu'il appelle DiddiPay ;
- callback secret : DiddiGo authentifie les evenements envoyes par DiddiPay.

Ils doivent etre aleatoires, differents par environnement, stockes dans le gestionnaire de secrets
du deploiement et rotatifs.

Le relay DiddiPay s'execute dans un worker ou un job interne :

```bash
python -m payfund_app.ops relay-payment-events --limit 100
```

## 9. DiddiFund et futurs modules

Le meme pattern s'applique :

- DiddiFund utilise une `business_reference` stable liee a l'investissement ou au remboursement ;
- son backend conserve le `payment_intent_id` ;
- son callback possede une inbox unique ;
- ses transitions campagne/pret restent dans DiddiFund ;
- chaque module a ses propres service key, callback URL et callback secret.

DiddiFiles ne consomme normalement pas un paiement. Il reste le proprietaire des fichiers et peut
fournir des references KYC a un module financier sans stocker lui-meme un solde.

## 10. Wallet legacy

Les routes `/wallet/*` existent encore pendant la migration. Une nouvelle integration DiddiGo ne
doit plus utiliser `/wallet/pay/merchant` comme abstraction universelle.

Un futur DiddiWallet pourra etre branche derriere DiddiPay comme moyen de paiement. DiddiGo gardera
alors exactement le meme contrat `PaymentIntent`.

## 11. Checklist de revue DiddiGo

- Le montant est calcule cote DiddiGo.
- La cle d'idempotence est stable et unique par operation metier.
- `payment_intent_id` et l'event `id` ont des contraintes uniques.
- La signature est verifiee sur le corps brut et en temps constant.
- La transaction inbox + course + outbox est atomique.
- Un doublon retourne `2xx` sans second effet.
- Une redirection frontend ne marque jamais la course payee.
- `unknown` ne produit jamais un second debit automatique.
- Une reconciliation des paiements non finaux existe.
- Les secrets ne sont ni logs, ni commits, ni exposes au frontend.
