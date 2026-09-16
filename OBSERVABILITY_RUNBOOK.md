# DiddiPay: runbook d'incident

Ce document accompagne les alertes OBS-6. Il ne remplace ni les journaux comptables ni la
verification aupres du PSP. Horodatage des incidents et preuves en UTC.

## Invariants financiers

- Ne jamais marquer un paiement `succeeded` parce que le navigateur est revenu du PSP ou qu'un
  dashboard affiche une hausse. Seul un webhook authentifie ou une reconciliation avec le PSP
  peut confirmer le paiement.
- Ne jamais recreer une intention avec une nouvelle cle d'idempotence pour contourner un timeout.
  Rechercher d'abord l'intention et la reference provider existantes.
- Ne jamais modifier ou supprimer une entree du ledger. Toute correction passe par une operation
  compensatrice auditable, suivant la procedure metier validee.
- Ne pas copier dans l'incident les tokens, PIN, payloads webhook bruts ou donnees personnelles.
  Utiliser les identifiants techniques expurges et les acces restreints.

## Triage initial

1. Noter l'environnement, l'heure UTC, la version/commit deploye, l'alerte et son debut.
2. Relever `request_id`, `trace_id`, `payment_intent_id` et `business_reference` si disponibles,
   sans les utiliser comme labels Prometheus ou les publier hors canal securise.
3. Verifier la disponibilite de l'application, de Postgres, du PSP, de Prometheus et du scraper.
   `up{job="diddipay"} == 0` signifie absence de telemetrie, pas absence d'incident.
4. Comparer le dashboard **DiddiPay SLO** aux logs JSON dans Loki et aux traces Tempo. Examiner un echantillon
   de transactions par la lecture ops et les evenements provider, sans modifier leur statut.
5. Designer un responsable de l'incident, enregistrer chaque action et annoncer l'impact connu
   aux modules consommateurs. Une notification Alertmanager n'est pas une preuve de paiement.

## Availability Budget Burn

Objectif provisoire: 99,9 % des requetes HTTP sans `5xx`, mesure sur 30 jours. Les `4xx` ne sont
pas des erreurs serveur. Le burn rate critique utilise 5 minutes et 1 heure a 14,4x; le warning
utilise 30 minutes et 6 heures a 6x. Ces seuils sont des hypotheses de depart a recalibrer avec
le trafic et l'impact reel.

1. Verifier que Prometheus collecte encore la cible et que l'alerte porte sur un volume de
   requetes significatif. Un faible trafic rend le ratio instable.
2. Segmenter les `5xx` par route et regarder la premiere erreur horodatee. Verifier aussi
   migrations, saturation Postgres, connexions, latence PSP et dernier deploiement.
3. Si un deploiement est suspect, suivre la procedure de rollback de la stack en conservant les
   migrations et les donnees. Ne jamais restaurer une base ancienne pour corriger des paiements.
4. Apres stabilisation, verifier les PaymentIntents `processing` ou incertains via la
   reconciliation. Ne pas les rejouer aveuglement.

## API Latency

Le seuil initial est p95 > 1 seconde pendant 15 minutes. Examiner d'abord les routes lentes,
le pool Postgres et les appels PSP. Une latence provider n'implique pas un echec financier:
laisser l'intention en etat incertain et utiliser la reconciliation prevue.

## Webhook Failures

Le ratio `failed` > 1 % sur 15 minutes indique un traitement interne en erreur. Les signatures
`rejected` sont suivies separement et peuvent signaler une mauvaise configuration ou une attaque.

1. Verifier la signature, l'horodatage, la disponibilite DB et les evenements provider persistants.
2. Controler les tentatives de livraison/retry du PSP et les doublons. Le traitement doit rester
   idempotent; ne jamais contourner la verification de signature.
3. Si des webhooks manquent, utiliser la reconciliation par reference provider. Comparer les
   montants, devises et comptes avant toute transition finale.

## Module Callback Failures

Le ratio `retried|unavailable` > 5 % sur 15 minutes concerne la notification aux modules. Le
paiement PSP peut etre reussi alors que DiddiGo/DiddiFund n'a pas encore recu son callback.

1. Verifier l'etat de l'outbox (`pending`, `delivering`, `dead_letter`) et la connectivite du module.
2. Verifier la configuration HTTPS, la signature et les delais de reponse du receiver.
3. Relancer uniquement par le mecanisme idempotent d'outbox documente, avec la meme identite
   d'evenement. Ne jamais creer un deuxieme paiement pour corriger une notification.
4. Confirmer l'accuse de reception du module et la convergence de son etat metier.

## Provider Transport Errors

Une erreur reseau ou un timeout ne prouve pas que le PSP n'a pas preleve. Chercher la reference
existante et interroger le PSP par la reconciliation. Tant que le resultat n'est pas prouve, garder
l'intention en statut non final et informer le module appelant de l'incertitude.

## Outbox Dead Letters

Une dead letter est un evenement de paiement non livre au module. Examiner le dernier code de
reponse, corriger la cause, puis rejouer la livraison avec le meme ID d'evenement. Verifier
l'idempotence du receiver avant et apres la reprise. Escalader si le module a deja applique l'effet
metier sans avoir acquitte le callback.

## Cloture

Confirmer que les alertes sont revenues a la normale, que la reconciliation n'a plus de cas
incertains lies a l'incident, que l'outbox a converge et qu'aucune transaction n'a ete creditee ou
debitee deux fois. Archiver chronologie, impact, preuves expurgees, actions et tests de non-regression.
