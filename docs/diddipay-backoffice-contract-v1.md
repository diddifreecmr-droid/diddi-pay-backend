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

## Manifest DiddiAdmin

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
  }
]
```

Les actions de mutation sont volontairement absentes de cette tranche. Elles utiliseront une
identite S2S distincte, `X-Backoffice-Actor`, `X-Backoffice-Command-Id`, une raison et une cle
d'idempotence verifiee dans le corps et l'en-tete.
