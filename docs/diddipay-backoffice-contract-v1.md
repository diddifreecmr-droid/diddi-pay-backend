# DiddiPay - contrat Backoffice v1

## Authentification

Le navigateur envoie son JWT humain uniquement au BFF DiddiAdmin. Le BFF appelle DiddiPay avec :

```http
Authorization: Bearer <DiddiFreeID service token>
X-Client-ID: <backoffice client id>
X-Request-ID: <correlation id>
```

Audience : `diddipay`. Scope de lecture : `diddipay:operations:read`. Les clients autorises sont
configures dans `BACKOFFICE_CLIENT_IDS`. Une liste vide ferme entierement la surface.

Les commandes utilisent le scope distinct `diddipay:operations:write` et exigent aussi :

```http
X-Backoffice-Actor: <DiddiFreeID user id de l'operateur>
X-Backoffice-Command-Id: <identifiant unique de commande>
Idempotency-Key: <cle identique a celle du corps JSON>
```

Le BFF DiddiAdmin construit ces en-tetes apres avoir authentifie l'operateur. Ils ne doivent jamais
etre acceptes directement depuis un navigateur par DiddiPay.

## File des paiements

```http
GET /payfund/v1/internal/backoffice/payments?page=1&page_size=20&status=processing
```

La pagination commence a 1 et `page_size` est borne entre 1 et 100. Le filtre `status` accepte les
statuts PaymentIntent documentes. La liste ne contient ni identifiant utilisateur, ni metadata
metier, ni payload provider.

## Detail financier

```http
GET /payfund/v1/internal/backoffice/payments/{payment_intent_id}
```

Le detail ajoute les tentatives provider et le resume financier : brut capture, remboursements,
frais PSP, net attendu, montant settle et outstanding. Les payloads webhook et les donnees client
restent exclus.

## Commandes auditees

### Remettre un callback en file

```http
POST /payfund/v1/internal/backoffice/payments/{payment_intent_id}/callbacks/{event_id}/retry
```

Cette commande accepte uniquement un evenement `dead_letter` appartenant au PaymentIntent. Elle le
repasse a `pending`; le worker de livraison effectue ensuite l'appel asynchrone normal.

### Enregistrer un settlement PSP

```http
POST /payfund/v1/internal/backoffice/payments/{payment_intent_id}/settlements
```

Le corps fournit `amount`, `settlement_reference`, `reason` et `idempotency_key`. DiddiPay refuse un
montant superieur a la creance provider encore ouverte. L'ecriture passe par le ledger en partie
double (`bank_cash` vers `processor_receivable:<processor>`).

### Consulter une commande

```http
GET /payfund/v1/internal/backoffice/commands/{command_id}
```

Chaque commande conserve localement l'identite du service, l'operateur, la raison, la cible,
l'empreinte de requete et le resultat. Un replay strictement identique retourne le meme audit sans
repeter l'effet; une reutilisation divergente retourne `409 IDEMPOTENCY_CONFLICT`.

## Manifest DiddiAdmin

Le manifeste machine-readable officiel est versionne dans
`docs/manifests/diddipay-backoffice-v1.json`. Le bloc ci-dessous en est la lecture humaine ;
le fichier JSON fait foi pour l'integration et les controles automatises.

```json
[
  {
    "module": "diddipay",
    "name": "list_payments",
    "permission": "read",
    "description": "Consulter la file operationnelle des PaymentIntents",
    "method": "GET",
    "path": "/payfund/v1/internal/backoffice/payments",
    "requires_reason": false,
    "requires_idempotency": false,
    "execution_mode": "interactive",
    "service_scope": "diddipay:operations:read"
  },
  {
    "module": "diddipay",
    "name": "get_payment_detail",
    "permission": "read",
    "description": "Inspecter les tentatives et effets financiers d'un PaymentIntent",
    "method": "GET",
    "path": "/payfund/v1/internal/backoffice/payments/{payment_intent_id}",
    "requires_reason": false,
    "requires_idempotency": false,
    "execution_mode": "interactive",
    "service_scope": "diddipay:operations:read"
  },
  {
    "module": "diddipay",
    "name": "retry_payment_callback",
    "permission": "write",
    "description": "Remettre en file un callback DiddiPay en dead letter",
    "method": "POST",
    "path": "/payfund/v1/internal/backoffice/payments/{payment_intent_id}/callbacks/{event_id}/retry",
    "requires_reason": true,
    "requires_idempotency": true,
    "execution_mode": "command",
    "service_scope": "diddipay:operations:write"
  },
  {
    "module": "diddipay",
    "name": "record_payment_settlement",
    "permission": "write",
    "description": "Enregistrer un versement PSP constate par les operations",
    "method": "POST",
    "path": "/payfund/v1/internal/backoffice/payments/{payment_intent_id}/settlements",
    "requires_reason": true,
    "requires_idempotency": true,
    "execution_mode": "command",
    "service_scope": "diddipay:operations:write"
  }
]
```
