# DiddiPay - contrat Pilotage v1

## Route

```http
GET /payfund/v1/internal/pilotage/daily-summary?date=2026-09-23
Authorization: Bearer <DiddiFreeID service token>
X-Client-ID: <pilotage client id>
X-Request-ID: <correlation id>
```

Le token doit viser `aud=diddipay` et porter le scope
`diddipay:payment-summary:read`. Le scope historique `payment-summary:read` est accepte uniquement
pendant la migration lorsqu'il est configure dans `PAYMENT_SUMMARY_LEGACY_SCOPE`.

La journee metier utilise `Africa/Abidjan` et l'intervalle semi-ouvert `[00:00, 00:00 suivant)`.
Les montants sont des francs XOF entiers. Le service retourne `is_final=false` pour la journee en
cours. Une correction comptable tardive peut modifier une journee historique.

## Ventilation comptable v1

```http
GET /payfund/v1/internal/pilotage/accounting-summary?date=2026-09-23
```

Cette route expose le contrat `pilotage.accounting-source.v1`. Elle groupe les journaux
financiers confirmes par service, processeur, type de flux et statut, sans exposer de nom,
telephone ou identifiant utilisateur.

Les services publies sont `diddigo`, `diddisend`, `diddifood`, `diddifund`, `diddifree` et
`unknown`. La valeur vient de `PaymentIntent.client_id` (ou du `client_id` du payout). Le
`flow_type` explicite de `metadata` est prioritaire. Pour les anciens appels, une reference
contenant `topup` est classee `wallet_topup`; les autres captures sont classees
`service_payment`. Une integration nouvelle doit toujours envoyer `metadata.flow_type`.

Les captures, remboursements, frais processeur et settlements restent dans des colonnes
distinctes. `net_expected_xof = gross_amount_xof - refund_amount_xof - processor_fees_xof` et
`outstanding_amount_xof = net_expected_xof - settled_amount_xof`. Un payout est une ligne
`flow_type=payout`: son montant est porte par `gross_amount_xof`, tandis que
`net_expected_xof` et `outstanding_amount_xof` valent zero car un payout ne cree pas de creance
sur le processeur.

`is_final` vaut `false` pour la journee courante en `Africa/Abidjan`. Une journee passee reste
recalculable: elle est finale au sens operationnel, mais une correction comptable historique
peut modifier sa reponse. `calculated_at` est l'instant UTC du calcul. Une indisponibilite SQL
est propagee comme erreur HTTP; elle n'est jamais convertie en agregats a zero. Une liste
`entries` vide signifie uniquement qu'aucun journal correspondant n'existe pour la journee.

Le jeton S2S doit avoir `aud=diddipay`, le scope `diddipay:payment-summary:read`,
`sub=service:pilotage`, et son `client_id` doit etre identique au header `X-Client-ID` ainsi
qu'au client Pilotage provisionne.

## Metriques

| Nom | Unite | Definition |
| --- | --- | --- |
| `confirmed_payments_count` | `count` | Nombre de journaux `capture` crees pendant la journee. |
| `confirmed_payments_amount_xof` | `XOF` | Somme brute des memes captures, avant remboursement et frais PSP. |
| `confirmed_refunds_count` | `count` | Nombre de journaux `refund` crees pendant la journee. |
| `confirmed_refunds_amount_xof` | `XOF` | Somme des remboursements confirmes pendant la journee. |
| `processor_fees_amount_xof` | `XOF` | Frais PSP comptabilises pendant la journee. |
| `net_expected_delta_xof` | `XOF` | Captures moins remboursements et frais de la journee. |
| `settlements_count` | `count` | Nombre d'ecritures de settlement comptabilisees pendant la journee. |
| `settlements_amount_xof` | `XOF` | Montant recu du PSP et rapproche pendant la journee. |
| `unsettled_receivable_delta_xof` | `XOF` | Delta journalier `net_expected - settlements`; ce n'est pas le stock total restant. |
| `payouts_count` | `count` | Nombre de payouts confirmes pendant la journee. |
| `payouts_amount_xof` | `XOF` | Montant des payouts confirmes pendant la journee. |

Les dates de capture, remboursement et settlement sont volontairement leurs propres dates
comptables. Un settlement peut donc produire un delta journalier negatif lorsqu'il regle une
capture d'une journee precedente.

## Fraicheur

`calculated_at` indique quand DiddiPay a calcule la reponse. Pilotage conserve la derniere valeur
valide et gere `fresh`, `stale` ou `unavailable`; une indisponibilite ne doit jamais devenir un
resume rempli de zeros. La fenetre V1 est de 60 secondes.

## Sante operationnelle

```http
GET /payfund/v1/internal/pilotage/health-summary
```

Cette route utilise la meme authentification et retourne uniquement le nombre de callbacks
`pending` et `dead_letter`. `status=degraded` signifie qu'au moins une dead letter exige une
intervention. Si la source ne peut pas etre lue, les compteurs valent `null` et le statut vaut
`unavailable`; ils ne valent jamais artificiellement zero.

## Compatibilite

`GET /payfund/v1/internal/v1/payment-summary` reste disponible temporairement pour l'ancien
collecteur. Les nouvelles integrations utilisent exclusivement le contrat `pilotage.v1`.

## Projection DiddiFree Pro

`GET /payfund/v1/internal/pilotage/capabilities` declare aussi la capability `diddipay/wallet`.
Pilotage peut l'utiliser pour savoir que DiddiPay publie un etat wallet vers DiddiFreeID, mais pas
pour inferer une autorisation financiere.

La projection vers `/pro/me` n'autorise aucune operation de paiement, lecture de solde, validation
PIN, transfert, retrait, payout, decision de risque ou decision de limite. Ces decisions restent
portees exclusivement par DiddiPay.
