# DiddiPay - Runbook de migration PaymentIntent

**Version :** 1.0

**Date :** 2026-08-15

## 1. Objectif

Faire évoluer DiddiPay du wallet historique vers un orchestrateur de paiement sans supprimer des
soldes, sans réécrire l'historique financier et sans obliger les modules à connaître Paystack.

## 2. État de coexistence MVP

Deux périmètres vivent dans la même application et dans la même base PostgreSQL :

| Périmètre | Schéma | Usage |
|---|---|---|
| orchestrateur DiddiPay | `payments.*` | PaymentIntent, tentatives, webhooks, callbacks, remboursements et settlement |
| wallet legacy | `wallet.*` | solde, PIN, P2P, dépôt/retrait et ledger utilisateur historique |
| DiddiFund | `fund.*` | campagnes, investissements, prêts et ordres de paiement |

La coexistence est volontaire. Aucun déploiement MVP ne doit supprimer ou réinterpréter les
écritures `wallet.ledger_entries`.

## 3. Phases

### Phase A - MVP actuel

- toutes les nouvelles collectes module-to-module utilisent `/payment-intents` ;
- Paystack est sélectionné par l'adaptateur backend ;
- DiddiGo et DiddiFund conservent leur objet métier et reçoivent les événements signés ;
- les routes `/wallet/*` continuent de servir les écrans legacy ;
- aucun solde wallet n'est requis pour payer par Paystack.

### Phase B - Stabilisation

- exécuter le relay d'événements et la reconciliation dans des workers planifiés ;
- superviser les dead letters et les settlements non rapprochés ;
- comparer quotidiennement rapports Paystack, sous-ledger `payments.*` et compte bancaire ;
- migrer les autres modules vers le même contrat S2S.

### Phase C - PSP directs

- ajouter un adaptateur par PSP derrière les ports du module `payments` ;
- conserver les statuts et `next_action` normalisés ;
- certifier signature webhook, idempotence et reconciliation pour chaque adaptateur ;
- router progressivement le trafic sans modifier DiddiGo/DiddiFund.

### Phase D - DiddiWallet optionnel

- extraire ou renommer le wallet historique en DiddiWallet ;
- implémenter `payment_method=wallet` derrière PaymentIntent ;
- conserver le ledger historique et ses identifiants ;
- retirer les appels directs `/wallet/*` seulement après migration de tous les consommateurs.

## 4. Déploiement sans interruption

1. Sauvegarder PostgreSQL et vérifier qu'une restauration a déjà été testée.
2. Déployer les migrations additives avant d'activer les nouveaux appels.
3. Configurer une `X-Service-Key` distincte et un secret callback distinct par module.
4. Déployer le receiver callback du module avant d'activer sa cible dans DiddiPay.
5. Tester création, retour checkout, webhook, doublon, callback, retry et reconciliation en sandbox.
6. Activer le nouveau flux pour un faible pourcentage ou un seul module.
7. Surveiller erreurs provider, dead letters et settlements avant d'augmenter le trafic.

## 5. Retour arrière

- désactiver la création de nouveaux PaymentIntent côté module ;
- laisser les workers terminer ou réconcilier les intentions déjà initialisées ;
- ne jamais marquer un paiement échoué uniquement parce que l'ancienne version est redéployée ;
- ne jamais downgrader ou supprimer des tables financières contenant des données ;
- restaurer une base uniquement dans le cadre d'un plan d'incident qui traite aussi les paiements
  acceptés par le PSP après le point de sauvegarde.

## 6. Critères de retrait du wallet legacy

Le retrait des routes directes n'est autorisé que lorsque :

- aucun frontend ou backend actif ne les appelle encore ;
- les soldes et obligations envers les utilisateurs ont un propriétaire produit et comptable ;
- le parcours de migration ou remboursement des soldes est validé ;
- DiddiWallet derrière PaymentIntent couvre les usages conservés ;
- un audit de rapprochement confirme l'intégrité de l'historique ;
- une fenêtre de dépréciation versionnée a été communiquée.
