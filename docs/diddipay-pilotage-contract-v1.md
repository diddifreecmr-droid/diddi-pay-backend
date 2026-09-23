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

## Compatibilite

`GET /payfund/v1/internal/v1/payment-summary` reste disponible temporairement pour l'ancien
collecteur. Les nouvelles integrations utilisent exclusivement le contrat `pilotage.v1`.
