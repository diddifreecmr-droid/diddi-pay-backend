# Rapport de validation — DIDDI Observability 1.0.0

Rapport historique du 13 novembre. Pour les adaptations et limites actuelles du protocole 1.1.1, consulter [VALIDATION-v1.1.1.md](VALIDATION-v1.1.1.md). Les résultats ci-dessous ne constituent pas une nouvelle exécution sur la révision 1.1.1.

Date : 13 novembre 2026, UTC. Environnement : Windows/Python 3.11, PostgreSQL 16 dans un conteneur local temporaire exclusivement dédié aux tests. Aucun accès à DiddiObserve, aucun déploiement, aucune transaction réelle et aucune notification d'alerte.

Révision initiale : `8f49eda54ebdb0a1663ef228b0c2e75b35369a20`. La révision d'implémentation validée est inscrite dans `evidence/revision.json`. Ce rapport ne représente pas la version actuellement déployée.

## Résultats locaux

- Suite complète : résultat exact et avertissements dans `evidence/pytest.txt`. Elle couvre aussi les contrats métier existants, les signatures de callbacks, l'idempotence, les leases, les API/OpenAPI et la cohérence Alembic/modèles.
- Contrat v1 : `tests/test_observability_v1.py`, avec deux applications HTTP synthétiques, liens asynchrones et deux tentatives sur une vraie outbox PostgreSQL de test.
- Panne OTLP : `evidence/outage.json`, scénario réellement exécuté pendant plus de 300 secondes. Le récepteur retourne 503 puis 200. La file atteint au maximum 2 048 spans ; les pertes sont comptées et de nouveaux lots sont reçus après rétablissement. La mesure mémoire `tracemalloc` inclut le banc de test ; ce n'est pas une mesure RSS ni une charge de paiement représentative.
- Configuration : `evidence/configuration.json`. Parsing des trois templates YAML et du Compose combiné avec des valeurs synthétiques ; ports hôte API/worker absents, rotation vérifiée. Aucune requête réseau de collecte réelle. Les expressions PromQL ne sont pas certifiées par ce parsing ; `promtool`/tests de règles et activation centrale restent à réaliser.
- Ruff sur les fichiers d'instrumentation et scripts concernés, compilation Python et `git diff --check` : réussis. Les avertissements pytest existants concernent la transition FastAPI/httpx et le champ callback historique déprécié.

## Matrice C01–C12

| Critère | Preuve locale | Ce qui reste à vérifier |
| --- | --- | --- |
| C01 | Manifeste 1.0.0 ; même ressource logs/OTel/info ; instance par démarrage ; label OCI et SHA configurable | Propriétaire/contact, valeurs d'installation, image et SHA réellement déployés ; cohérence des labels ajoutés par collecte |
| C02 | JSON, UTC, niveaux, contexte absent omis, 64 KiB, troncature et échec de sérialisation ; formatter commun bibliothèques/Uvicorn/Alembic et exceptions processus/threads | Capture exhaustive des logs de démarrage du conteneur déployé ; les commandes CLI manuelles historiques ne sont pas déclarées comme signal v1 |
| C03 | Secrets synthétiques imbriqués, JSON/repr d'erreur, URLs signées, credentials et données personnelles courantes retirés ; attributs de traces autorisés avant export ; labels sans IDs | Revue de tout nouveau texte libre ; contrôle des attributs et en-têtes dans un parcours réel de recette ; permissions sur les stockages |
| C04 | Succès, 404, 500, histogrammes classiques ; 100 identifiants et 100 routes inconnues ; méthode OTHER ; durée couvrant le corps du stream | Budget total de 20 000 séries par cible à mesurer sous trafic représentatif ; une cible par processus à vérifier en déploiement |
| C05 | API et worker refusent secret absent/invalide ; CIDR de pair direct et X-Forwarded-For non fiable ; profil Compose sans ports hôte | TLS privé, pare-feu, ingress exclu des CIDR et accès public direct réellement impossible ; rotation des secrets |
| C06 | Deux applications HTTP locales : parent serveur → client → serveur ; mêmes trace/request IDs dans les logs ; contexte malformé ignoré ; deux requêtes distinctes et nettoyage vérifiés | Parcours entre deux services DIDDI réellement déployés, sampling à la frontière publique et ingestion |
| C07 | Contexte persisté dans outbox sans changer payload/signature ; nouvelle trace liée par tentative ; même événement livré après reprise ; aucune livraison supplémentaire ; nettoyage du contexte | Consommateurs externes Redis Stream/PubSub et callbacks : coordination des métadonnées et vérification jusqu'au stockage central |
| C08 | Liveness indépendante ; readiness 200/200-degraded/503 ; décisions DB/Redis ; sonde bloquée bornée et contrôle de concurrence | Coupure réelle des dépendances de recette et interprétation par orchestrateur/sondeur central |
| C09 | Cycle succès/erreur, durée, heartbeat après succès seulement, contrôle du fichier de santé ; métriques outbox dans worker et heartbeat du subscriber | Détection de blocage et backlog dans la collecte centrale ; seuils métier de backlog à définir |
| C10 | 5 minutes réelles de 503 local ; opérations synthétiques possibles ; file bornée ; pertes et récupération mesurées ; Retry-After, rejet partiel OTLP et chemin SDK testés | Même scénario avec charge métier synthétique représentative, RSS/CPU et infrastructure de recette ; aucune preuve de reprise centrale à ce stade |
| C11 | Aucune preuve centrale. Tests locaux de corrélation seulement | Arrivée Loki/stockage traces/Prometheus, consultation Grafana, liens logs↔traces et affichage no-data ; **bloquant** |
| C12 | Rotation Docker préparée/parsée, templates alertes et runbook fournis ; aucune notification envoyée | Rétention effective, backups/exports, dimensionnement, tests de règles PromQL, routage/déduplication et attribution nominative ; **bloquant** |

## Non-applicabilité et limites

Aucun critère entier n'est déclaré non applicable : ce module possède une API, des outboxes et des workers. Seuls certains sous-profils ne s'appliquent pas : pas de scheduler/cron déployé (donc pas d'échéance suivante à inventer), pas de WebSocket, pas de consommateur Redis Stream dans ce dépôt. Les consommateurs Stream externes sont hors périmètre ; l'outbox de publication est mesurée ici. Le conteneur de documentation statique relève du profil web-edge, sans traces métier inventées ; DB et Redis restent des composants infrastructure.

Les spans DB/cache sont recommandés par le protocole mais ne sont pas ajoutés ici : le périmètre livré couvre les HTTP automatiques, publication outbox, consommation/reprises et cycles worker, ainsi que les métriques de providers existantes. Les dépendances et leurs limites de couverture sont déclarées. Les logs libres ne peuvent garantir par regex la détection de toute donnée personnelle imaginable ; la revue à la source reste nécessaire.

Le profil historique de monitoring présent dans ce dépôt n'est pas déclaré conforme v1 : il conserve ses politiques historiques de rétention/SLO. Il ne doit pas être activé en parallèle de DiddiObserve. Ses références de métriques et parsing du timestamp ont été migrés pour éviter une rupture silencieuse des dashboards existants.

## Exceptions à suivre

| Périmètre / motif | Responsable à confirmer | Mesure compensatoire | Réexamen |
| --- | --- | --- | --- |
| Ingestion, parcours distribué, confidentialité centrale et réseau non accessibles pendant cette mission | DiddiObserve + DiddiPay | État `inventoried`, aucun label de conformité, tests synthétiques et endpoints fermés sans secret | 13 décembre 2026, avant tout passage à conformant-v1 |
| Rétention, backups, SLO, contact opérationnel et routage non fournis | DiddiObserve + propriétaire métier DiddiPay | Templates sans secrets, rotation locale préparée, aucun test de notification | 13 décembre 2026 |
| Spans DB/cache et sorties des CLI manuelles hors profil déployé | DiddiPay | Readiness réelle, traces HTTP/outbox, logs JSON des API/workers, métriques providers et queues ; ne pas annoncer une couverture DB exhaustive | 13 décembre 2026 |

Ces lignes ne constituent pas une approbation d'exception par la plateforme. L'état reste **inventoried** jusqu'à preuve d'ingestion, puis au mieux **connected** tant qu'un critère applicable reste ouvert.
