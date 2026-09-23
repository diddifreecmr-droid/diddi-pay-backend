# Recette d'integration DiddiAdmin vers DiddiPay v1

## Separation des responsabilites

- Le navigateur authentifie l'operateur avec son JWT humain aupres du BFF DiddiAdmin.
- Le BFF verifie les permissions humaines et obtient un JWT de service court aupres de DiddiFreeID.
- DiddiPay valide le service, le scope et, pour une commande, le contexte operateur transmis par le BFF.
- DiddiPay reste seul proprietaire des PaymentIntents, callbacks, settlements et regles financieres.
- Pilotage lit des agregats; il ne commande aucune mutation et ne lit jamais la base DiddiPay.

Le navigateur ne recoit jamais le `client_secret`, le token S2S, une cle Paystack ou une cle de
service historique.

## Provisionnement DiddiFreeID

Creer deux clients distincts afin de limiter l'impact d'une fuite :

| Client | Audience | Scopes DiddiPay |
| --- | --- | --- |
| `backoffice-<env>-diddipay` | `diddipay` | `diddipay:operations:read`, `diddipay:operations:write` |
| `pilotage-<env>-diddipay` | `diddipay` | `diddipay:payment-summary:read` |

Les secrets clients restent dans le coffre de secrets de DiddiAdmin/Pilotage. DiddiPay ne stocke
que les identifiants autorises via `BACKOFFICE_CLIENT_IDS` et `PAYMENT_SUMMARY_CLIENT_ID`.

## Variables DiddiPay

```dotenv
DIDDIFREEID_JWKS_URL=https://auth-staging.diddifree.com/identity/v1/.well-known/jwks.json
DIDDIFREEID_ISSUER=diddifree-id
BACKOFFICE_AUDIENCE=diddipay
BACKOFFICE_CLIENT_IDS=backoffice-staging-diddipay
BACKOFFICE_READ_SCOPE=diddipay:operations:read
BACKOFFICE_COMMAND_SCOPE=diddipay:operations:write
PAYMENT_SUMMARY_AUDIENCE=diddipay
PAYMENT_SUMMARY_CLIENT_ID=pilotage-staging-diddipay
PAYMENT_SUMMARY_SCOPE=diddipay:payment-summary:read
PAYMENT_SUMMARY_LEGACY_SCOPE=
```

Une liste Backoffice vide ferme toutes les routes Backoffice. Le scope historique Pilotage doit
etre vide une fois la migration terminee.

## Appel de lecture Backoffice

```http
GET /payfund/v1/internal/backoffice/payments?page=1&page_size=20
Authorization: Bearer <service_token>
X-Client-ID: backoffice-staging-diddipay
X-Request-ID: <correlation_id>
```

Le BFF reutilise un token seulement jusqu'a quelques secondes avant son expiration et renouvelle le
token en cas de `401`. Un `403` signifie que l'identite est connue mais n'a pas le bon client ou
scope; renouveler le meme token ne corrigera pas le provisionnement.

## Appel de commande Backoffice

```http
POST /payfund/v1/internal/backoffice/payments/{payment_intent_id}/callbacks/{event_id}/retry
Authorization: Bearer <service_token>
X-Client-ID: backoffice-staging-diddipay
X-Request-ID: <correlation_id>
X-Backoffice-Actor: <operator_user_id>
X-Backoffice-Command-Id: <command_id>
Idempotency-Key: <idempotency_key>
Content-Type: application/json

{
  "contract_version": "backoffice.v1",
  "reason": "Destination DiddiGo corrigee et verifiee",
  "idempotency_key": "<same_idempotency_key>"
}
```

Le BFF genere `command_id` et `idempotency_key`; le navigateur ne les impose pas comme identite de
confiance. Sur timeout, il rejoue exactement la meme commande et peut lire son resultat avec
`GET /payfund/v1/internal/backoffice/commands/{command_id}`. Il ne genere une nouvelle cle que pour
une nouvelle decision humaine.

## Appels Pilotage

```http
GET /payfund/v1/internal/pilotage/daily-summary?date=2026-09-23
GET /payfund/v1/internal/pilotage/health-summary
Authorization: Bearer <pilotage_service_token>
X-Client-ID: pilotage-staging-diddipay
X-Request-ID: <correlation_id>
```

Pilotage conserve la derniere reponse valide avec son `calculated_at`. `unavailable` ou une erreur
reseau ne devient jamais une ligne de zeros. Les montants sont des entiers XOF.

## Recette staging minimale

1. Un appel sans token retourne `401` et un `request_id` correle a `X-Request-ID`.
2. Un token Backoffice sans scope ecriture lit les paiements mais recoit `403` sur une commande.
3. Un client non autorise recoit `403`, meme avec un scope correct.
4. Un retry sans acteur, command id ou idempotency key retourne `400 BACKOFFICE_HEADERS_REQUIRED`.
5. Une divergence entre la cle du corps et l'en-tete retourne `400 IDEMPOTENCY_KEY_MISMATCH`.
6. Deux appels strictement identiques retournent le meme `service_audit_id` et un seul effet.
7. La meme cle avec un autre montant, motif ou cible retourne `409 IDEMPOTENCY_CONFLICT`.
8. Un callback qui n'est pas en dead letter retourne `409 CALLBACK_NOT_RETRYABLE`.
9. Un settlement superieur au montant outstanding retourne `422 SETTLEMENT_AMOUNT_INVALID`.
10. Le resume Pilotage affiche `unavailable` et des compteurs `null` si sa source est illisible.

## Exploitation et rotation

- Conserver le `X-Request-ID`, le `command_id` et le `service_audit_id` dans les logs DiddiAdmin.
- Alerter sur les `401/403`, les dead letters et le statut Pilotage `unavailable`.
- Faire tourner un secret client en ajoutant le nouveau secret, en deployant les consommateurs,
  puis en revoquant l'ancien; aucune cle Paystack n'est concernee par cette rotation S2S.
- Ne jamais recopier les payloads provider, tokens ou PII dans les tickets et logs d'operations.
