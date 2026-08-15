# DiddiPay - Contrat API

**Version du contrat :** 3.0 - pivot orchestrateur de paiements

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

## 2. Authentification service-to-service

Les routes `/payment-intents` sont appelees par le backend du module, jamais directement par une
application mobile ou web.

Headers requis :

| Header | Requis | Description |
|---|---:|---|
| `X-Client-ID` | oui | Identifiant stable du module, par exemple `diddigo` ou `diddifund` |
| `X-Service-Key` | oui | Secret du module configure dans `PAYMENT_SERVICE_KEYS` |
| `Idempotency-Key` | creation | Cle unique de l'operation metier |

Exemple de configuration backend :

```env
PAYMENT_SERVICE_KEYS=diddigo:secret-distinct,diddifund:autre-secret-distinct
```

Regles de securite :

- `X-Service-Key` ne doit jamais etre embarque dans Flutter, JavaScript ou une application cliente ;
- chaque module possede une cle distincte et rotative ;
- un module ne peut lire que ses propres `PaymentIntent` ;
- DiddiFreeID authentifie l'utilisateur aupres du module ; le module autorise l'action metier puis
  appelle DiddiPay avec son identite de service.

## 3. Modele monetaire

- `amount` est un entier strictement positif en unite mineure.
- Pour le MVP XOF, `5000` represente `5 000 XOF`.
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
X-Service-Key: <secret-backend>
Idempotency-Key: ride:42:collection:v1
Content-Type: application/json
```

Corps :

```json
{
  "business_reference": "ride:42",
  "amount": 5000,
  "currency": "XOF",
  "payer_user_id": "7c7df66d-7345-4aa7-b818-31cd91955d5b",
  "payee_user_id": "80ed38ce-1814-4b40-99f8-1ca7e65bea90",
  "channel": "mobile_money",
  "network": "orange",
  "customer_email": "client@example.com",
  "customer_phone": "+2250700000000",
  "callback_url": "https://go.diddifree.com/payments/return",
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
| `amount` | oui | Entier strictement positif |
| `currency` | oui | `XOF` pour le MVP |
| `payer_user_id` | non | UUID DiddiFreeID du payeur |
| `payee_user_id` | non | UUID DiddiFreeID du beneficiaire si connu |
| `channel` | non | `mobile_money` ou `card` |
| `network` | non | `orange`, `wave` ou `mtn` ; indication de routage, pas garantie d'affichage PSP |
| `customer_email` | Paystack | Requis par le checkout Paystack actuel |
| `customer_phone` | non | Telephone normalise, maximum 32 caracteres |
| `callback_url` | non | URL de retour navigateur apres le checkout, pas une preuve de paiement |
| `description` | non | Libelle lisible, maximum 255 caracteres |
| `metadata` | non | Contexte metier JSON non sensible |

Reponse `201 Created` :

```json
{
  "id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84",
  "client_id": "diddigo",
  "business_reference": "ride:42",
  "amount": 5000,
  "currency": "XOF",
  "status": "requires_action",
  "payer_user_id": "7c7df66d-7345-4aa7-b818-31cd91955d5b",
  "payee_user_id": "80ed38ce-1814-4b40-99f8-1ca7e65bea90",
  "description": "Course DiddiGo 42",
  "metadata": {
    "ride_id": "42"
  },
  "refunded_amount": 0,
  "attempts": [
    {
      "id": "b0198fb9-d36d-4395-9225-76c686739264",
      "status": "requires_action",
      "channel": "mobile_money",
      "network": "orange",
      "next_action": {
        "type": "redirect",
        "url": "https://checkout.paystack.com/example",
        "instructions": null,
        "expires_at": null
      },
      "failure_code": null,
      "created_at": "2026-08-15T10:00:00Z",
      "updated_at": "2026-08-15T10:00:00Z"
    }
  ],
  "created_at": "2026-08-15T10:00:00Z",
  "updated_at": "2026-08-15T10:00:00Z"
}
```

### Idempotence

La cle doit etre derivee de l'action metier et conservee lors d'un retry reseau. Une nouvelle cle ne
doit pas etre generee tant que l'utilisateur repete exactement la meme tentative metier.

- meme cle et meme requete : le meme `PaymentIntent` est retourne, sans nouveau debit ;
- meme cle et requete differente : `409 IDEMPOTENCY_CONFLICT` ;
- cle absente : `422 IDEMPOTENCY_KEY_REQUIRED`.

Le module doit stocker `payment_intent_id`, `business_reference` et la cle d'idempotence avec son
objet metier.

## 5. Consulter les paiements

### `GET /payment-intents/{intent_id}`

Retourne le `PaymentIntent` et ses tentatives. Le `X-Client-ID` appelant doit en etre proprietaire.
Un identifiant inconnu ou appartenant a un autre module retourne `404 PAYMENT_INTENT_NOT_FOUND`.

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
PAYMENT_CALLBACK_TARGETS={"diddigo":{"url":"https://go-api.diddifree.com/internal/webhooks/diddipay","secret":"replace-with-a-long-random-secret"}}
```

Commande de relay a executer dans un worker ou job interne :

```bash
python -m payfund_app.ops relay-payment-events --limit 100
```

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

### Sous-ledger financier et settlement

Chaque capture confirmee produit un journal double entree immuable : debit de
`processor_receivable:<provider>` et credit de `module_payable:<client_id>`. Les frais PSP, les
remboursements et les settlements ont leurs propres journaux idempotents. Chaque journal contient
exactement un debit et un credit du meme montant et de la meme devise.

Le module proprietaire peut consulter :

```http
GET /payment-intents/{intent_id}/financial-summary
X-Client-ID: diddigo
X-Service-Key: <secret>
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
- outbox transactionnelle pour les evenements de succes.

Pas encore a considerer comme disponible tant que les sprints correspondants ne sont pas livres :

- payout/retrait via le nouveau coeur orchestrateur ;
- settlement comptable complet ;
- adaptateurs directs Orange Money, Wave ou MTN MoMo ;
- wallet comme moyen de paiement du nouvel orchestrateur.
