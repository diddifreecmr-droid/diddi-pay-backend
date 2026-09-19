# DiddiPay -> Pilotage : resume journalier (v1)

Statut : implementation locale ; recette staging et provisionnement du client S2S Pilotage restent a faire.

## Requete

`GET /payfund/v1/internal/v1/payment-summary?date=YYYY-MM-DD`

En-tetes : `Authorization: Bearer <service_jwt>` et `X-Client-ID: <client_id>`.
Jeton DiddiFreeID RS256 avec `iss=diddifree-id`, `aud=diddipay`,
`token_type=service`, `role=service`, `status=active`, `sub=service:pilotage`,
`client_id` correspondant a `X-Client-ID` et scope `payment-summary:read`.
L'identifiant exact du client Pilotage est configure dans `PAYMENT_SUMMARY_CLIENT_ID`.
Sans cette valeur, la route reste fermee. `401` : jeton absent/invalide ;
`403` : client ou scope non autorise.

## Reponse 200

```json
{
  "date": "2026-09-18",
  "timezone": "Africa/Abidjan",
  "currency": "XOF",
  "confirmed_payments_count": 1,
  "confirmed_payments_amount_xof": 50,
  "confirmed_refunds_count": 0,
  "confirmed_refunds_amount_xof": 0,
  "calculated_at": "2026-09-19T09:00:00Z",
  "source": "payments.financial_journals"
}
```

Les deux chiffres de paiement comptent les journaux `capture` crees dans
`[debut du jour, debut du lendemain)` a Abidjan. Les deux chiffres de
remboursement comptent les journaux `refund` dans le meme intervalle. Un
remboursement partiel est compte pour son montant effectif ; plusieurs
remboursements sont plusieurs evenements. Les echecs, annulations, doublons de
webhook, frais et settlements sont exclus. Montants bruts en XOF ; ne pas les
additionner a la valeur des courses ou livraisons. `calculated_at` est en UTC.

La date est celle de l'enregistrement durable de la confirmation dans DiddiPay,
pas la date de paiement affichee par Paystack. Une confirmation recue en retard
apparait le jour de sa reception. Journee vide : `200` avec quatre zeros.

## Limites et recette

Le chemin de reconciliation actuel ne cree pas de journal `capture`
(SCRUM-426). Les paiements recuperes uniquement par ce chemin sont donc absents
du resume jusqu'au correctif et a une reprise auditee des historiques. Ne pas
utiliser ce resume comme total financier exhaustif avant cette correction.

Verifier en staging un jour avec une capture connue, un remboursement partiel,
un doublon webhook et une journee vide. Comparer les references aux journaux
sans exposer ces references dans la reponse Pilotage. Pilotage rafraichit son
cache toutes les 30 secondes et marque les donnees perimees apres 60 secondes.
