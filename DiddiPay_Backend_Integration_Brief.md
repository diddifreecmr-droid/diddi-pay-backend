# DiddiPay Backend Integration Brief

Ce document s'adresse aux équipes backend des autres modules DiddiFree.
Il décrit comment intégrer DiddiPay proprement dans une architecture clean, sans dépendre du
frontend.

## 1. Rôle de DiddiPay

DiddiPay est le wallet système de DiddiFree.

Il est responsable de :
- l'état du wallet utilisateur ;
- les transferts P2P ;
- les paiements marchands ;
- les dépôts et retraits via des rails de paiement externes ;
- le ledger double entrée ;
- la réconciliation des providers ;
- les contrôles de sécurité transactionnelle comme le PIN et le step-up OTP.

DiddiPay n'est pas un orchestrateur de paiement générique.
Les rails externes comme Paystack, Wave, Orange Money ou MTN servent à créditer ou cash out le
wallet, mais la source de vérité reste toujours la transaction DiddiPay.

## 2. Ce que chaque module doit retenir

- DiddiFreeID gère l'identité centrale.
- DiddiPay gère l'argent et l'état comptable.
- Chaque module garde ses rôles métier.
- Un module ne doit jamais lire directement les tables `wallet.*` d'un autre service.
- Un module backend peut consommer le port `WalletServicePort` si l'intégration est in-process.

## 3. Intégration par module

### DiddiGo

DiddiGo peut demander un paiement marchand quand un trajet se termine ou lorsqu'un service annexe
doit être encaissé.

À utiliser :
- `POST /wallet/pay/merchant`
- `GET /wallet/transactions`
- `GET /wallet/transactions/{transaction_id}`

Règles :
- DiddiGo fournit le contexte métier via `origin_module` et `business_reference`.
- DiddiPay garde la responsabilité du ledger.
- DiddiGo ne doit pas créer un second état de solde local.

### DiddiFiles

DiddiFiles ne stocke pas de solde.
Il peut être utilisé pour KYC documentaire.

À utiliser :
- liaison de documents via les routes ops de DiddiPay ou les métadonnées KYC du wallet ;
- références de fichiers, pas blobs, dans DiddiPay.

Règles :
- le fichier vit dans DiddiFiles ;
- le wallet garde uniquement la référence du document ;
- aucune donnée sensible du fichier ne doit être recopiée dans le wallet.

### futurs modules

Tout nouveau module suit le même modèle :
- il garde ses règles métier ;
- il consomme le wallet pour encaisser ou débiter ;
- il ne duplique pas le solde ;
- il utilise les événements pour rester synchronisé ;
- il prévoit un rattrapage si un événement a été manqué.

## 4. Flux d'intégration recommandés

### Lecture du wallet

Pour afficher un solde ou un historique :
- appeler `GET /wallet/balance`;
- appeler ensuite `GET /wallet/transactions`;
- si besoin, `GET /wallet/transactions/{transaction_id}`.

La création du wallet est automatique au premier accès authentifié.
Si un cas support est découvert, l'équipe ops peut utiliser le backfill interne.

### Paiement marchand

Quand un module veut encaisser un utilisateur :
1. le module prépare son contexte métier ;
2. le backend appelle `POST /wallet/pay/merchant`;
3. DiddiPay écrit la transaction et le ledger ;
4. le module stocke seulement sa référence métier locale.

### Transfert sensible

Quand le montant dépasse le seuil sensible :
1. demander le step-up OTP ;
2. valider le PIN ;
3. rejouer le transfert avec `otp_code`.

Le module appelant ne doit pas inventer une logique parallèle de validation.

## 5. Sécurité

- Le JWT DiddiFreeID se vérifie localement via le JWKS.
- Les rôles métier restent dans le module propriétaire.
- Le PIN transactionnel est vérifié côté serveur.
- Les challenges OTP sont éphémères.
- Les routes ops doivent être protégées par une authentification admin ou service-to-service.

## 6. Événements à consommer

Les modules doivent s'abonner à :
- `user.registered`
- `user.updated`
- `user.role_changed`
- `user.suspended`

Cas d'usage :
- `user.registered` -> provisionner ou préparer l'état local du module ;
- `user.updated` -> invalider les caches ;
- `user.role_changed` -> activer une capacité métier ;
- `user.suspended` -> geler les actions sortantes ou bloquer l'accès.

## 7. Ce qu'un module ne doit pas faire

- ne pas créer son propre ledger d'argent ;
- ne pas lire les tables wallet directement ;
- ne pas supposer que `DiddiPay` est un simple service de transfert ;
- ne pas déduire les rôles métier à partir de DiddiFreeID ;
- ne pas contourner le step-up OTP pour les montants sensibles.

## 8. Erreurs à traiter côté module

Les modules backend doivent remonter les erreurs DiddiPay telles quelles au besoin :
- `PIN_REQUIRED`
- `INVALID_PIN`
- `STEP_UP_OTP_REQUIRED`
- `INVALID_STEP_UP_OTP`
- `STEP_UP_OTP_EXPIRED`
- `INSUFFICIENT_BALANCE`
- `RECIPIENT_NOT_FOUND`
- `MERCHANT_NOT_FOUND`

Le frontend peut ensuite afficher le message utile, mais la logique métier ne doit pas être
reconstruite côté client.

## 9. Si un module rejoint plus tard

Le module doit :
- consommer les événements à partir d'un backfill ;
- relire l'état local de ses objets métier ;
- traiter les événements de manière idempotente ;
- ne jamais supposer qu'il a tout reçu en temps réel.

## 10. Résumé

DiddiPay est la brique financière centrale.
Les autres modules lui délèguent l'exécution monétaire, mais gardent leur métier.
La bonne intégration consiste à :
- appeler DiddiPay pour les mouvements d'argent ;
- écouter les événements pour la synchronisation ;
- garder les rôles métier dans le module propriétaire ;
- utiliser DiddiFiles pour les preuves documentaires ;
- utiliser DiddiFreeID seulement pour l'identité centrale.
