# DiddiPay - contrat Payouts v1

Ce document s'adresse aux autres modules (DiddiGo, DiddiSend, DiddiFood, et tout futur
module) qui doivent verser de l'argent a un tiers (restaurant, chauffeur, coursier, etc.)
via le coeur PaymentIntent de DiddiPay. Il decrit le contrat stable que ces modules doivent
respecter - rien ici ne depend du prestataire (PSP) que DiddiPay utilise reellement en
coulisse, et c'est deliberement le cas (voir "Agnostique au rail" ci-dessous).

Ce contrat est distinct du retrait du wallet historique (`/payfund/v1/wallet/withdraw`,
voir `DiddiPay_Contrat_API.md` §13) : deux chemins separes, ne pas les confondre.

## Authentification

Identique au reste du coeur PaymentIntent (`DiddiPay_Contrat_API.md` §2) :

```http
Authorization: Bearer <DiddiFreeID service token>
X-Client-ID: <client id>
```

Audience `diddipay`, scopes `diddipay:payouts:write` (creation) et
`diddipay:payouts:read` (consultation).

## Creer un payout

```http
POST /payfund/v1/payouts
Idempotency-Key: <cle stable par versement>
```

```json
{
  "business_reference": "diddifood:order:order-42:restaurant-payout:v1",
  "beneficiary_reference": "restaurant:rest-7",
  "beneficiary_destination": {
    "type": "mobile_money",
    "phone": "+2250700000789",
    "network": "mtn"
  },
  "amount": 4500,
  "currency": "XOF",
  "metadata": {}
}
```

- `beneficiary_reference` : identifiant metier opaque, defini et interprete uniquement par
  le module appelant (ex. un UUID de restaurant). DiddiPay ne l'interprete jamais.
- `beneficiary_destination` : **ou va l'argent**. Voir section suivante - c'est la partie
  agnostique au rail.
- `Idempotency-Key` + le corps de la requete forment une empreinte : un replay identique
  renvoie le meme payout (`200`), un replay avec un corps different renvoie
  `409 IDEMPOTENCY_CONFLICT`.

Reponse `201` :

```json
{
  "id": "...",
  "client_id": "diddifood",
  "business_reference": "...",
  "beneficiary_reference": "restaurant:rest-7",
  "beneficiary_destination": {"type": "mobile_money", "phone": "+2250700000789", "network": "mtn"},
  "amount": 4500,
  "currency": "XOF",
  "status": "processing",
  "processor": "pawapay",
  "provider_status": "accepted",
  "failure_code": null,
  "metadata": {},
  "created_at": "...",
  "updated_at": "..."
}
```

`processor` est informatif (observabilite, debug) - **aucun appelant ne doit jamais router
ou valider sur cette valeur**. Elle peut changer d'un payout a l'autre, meme pour deux
appels identiques, sans que le contrat ci-dessus change.

## `beneficiary_destination` - agnostique au rail

C'est le coeur du contrat : `beneficiary_destination` est un objet avec un champ `type`
discriminant. Un seul type existe aujourd'hui :

```json
{"type": "mobile_money", "phone": "+2250700000789", "network": "mtn"}
```

`network` est une chaine libre (`mtn`, `orange`, `moov`, ...) - DiddiPay ne la valide pas a
ce niveau, elle est interpretee par le processeur reellement choisi en interne.

**Ce qui est garanti rester stable :**

- La forme `{"type": ..., ...champs propres au type}` ne change pas quand DiddiPay change
  de PSP en interne, en ajoute un second, ou retire un PSP existant.
- Un nouveau type de destination (ex. `bank_transfer` plus tard) s'ajoutera comme une
  nouvelle variante du meme champ - **aucun module appelant existant n'aura besoin de
  changer son code** pour un type qu'il n'utilise pas.
- Les modules ne doivent jamais coder en dur un nom de PSP (`pawapay`, `paystack`, etc.)
  nulle part dans leur logique d'appel. Si un jour un module a besoin de savoir si un rail
  particulier est disponible, ce sera via une capacite exposee par DiddiPay (a definir si
  le besoin se presente), pas en inspectant `processor`.

**Pourquoi ce choix** : en interne, DiddiPay route chaque payout vers un processeur reel en
fonction de `type`/`network`, via une mise en correspondance de capacites (quel processeur
sait traiter quel type/reseau), pas via un routage code en dur par module appelant. Ajouter
un processeur = ajouter une classe et sa declaration de capacites, cote DiddiPay uniquement.

## Ce qui n'est PAS encore vrai (limites actuelles)

A lire attentivement avant d'integrer un vrai flux de paiement reel - ne pas supposer que
"l'API existe" veut dire "l'argent part reellement" :

- **`beneficiary_destination` est obligatoire pour un vrai versement.** Sans lui (ou en
  local/test), le payout echoue proprement en `422 PAYOUT_METHOD_UNAVAILABLE` plutot que
  de simuler un succes. Un module qui n'envoie jamais ce champ n'aura jamais de payout reel
  - c'est voulu, pas un bug a contourner.
- **Seul `type=mobile_money` avec `network` dans `{mtn, orange, moov}` est reellement
  cable aujourd'hui**, via PawaPay. `wave` n'est pas disponible - ni cote compte PawaPay
  (pas encore active), ni cote integration (flux d'autorisation different, pas encore
  construit).
- **Le dispatch reussi ne garantit pas que l'argent parte.** Le processeur choisi doit
  avoir un solde reel disponible pour le pays concerne - ce solde est independant de la
  facon dont l'argent a ete collecte (une collecte carte via un autre PSP ne finance pas
  automatiquement ce solde). C'est un sujet de tresorerie operationnelle, hors du controle
  de l'appelant et de DiddiPay cote code.
- **Pas de webhook de confirmation de payout aujourd'hui.** Le statut final s'obtient par
  polling (`GET /payfund/v1/payouts/{id}`) ou attend la reconciliation automatique
  periodique.
- **Pas de remboursement de payout** (annuler un versement deja parti) - non modelise.

## Statuts

`pending` -> `processing` -> `succeeded` | `failed`. `disputed` existe pour un futur usage
(litige post-versement), non emis aujourd'hui.

## Consulter un payout

```http
GET /payfund/v1/payouts/{payout_id}
GET /payfund/v1/payouts?business_reference=...
```

Memes regles d'isolation par `client_id` que le reste du coeur PaymentIntent.
