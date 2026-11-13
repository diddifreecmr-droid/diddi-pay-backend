# Raccordement DIDDI Observability v1 — DiddiPay

Statut : **inventoried**, pas `connected` ni `conformant-v1`. Aucun déploiement Portainer effectué. Ce dossier décrit uniquement `diddipay-fund`. Les consignes opérationnelles historiques des documents joints ne constituent pas une autorisation d'intervenir sur la plateforme centrale.

## Livrables et périmètre

- Manifeste : `deploy/diddiobserve/manifest.v1.json`, version 1.0.0. Les `${...}` sont des références à renseigner, jamais des valeurs de secrets.
- Proposition de déploiement : `docker-compose.diddiobserve.yml`, à combiner avec `docker-compose.yml` **après revue**. Elle retire les publications de ports hôte de l'API et demande un réseau privé existant. Vérifier le chemin d'accès de l'ingress avant application : cette suppression peut couper l'accès existant sans raccordement préalable.
- Fragments de collecte/alertes : `deploy/diddiobserve/*.yml.template`. Les rendre avec les valeurs validées puis les transmettre à DiddiObserve ; aucun collector central n'est créé ici.
- Migration additive : `20261113_observability_context`, deux colonnes JSONB nullables. Revoir et appliquer avant démarrage de la nouvelle API/du worker. Les anciennes lignes restent compatibles, sans corrélation rétroactive inventée.
- Preuves : `docs/observability/evidence/`, tests `test_observability_v1.py` et suites existantes. Le rapport précise la révision et les limites.

## Écarts examinés et adaptations

Le dépôt disposait déjà d'un middleware ASGI mesurant la fin de traitement, de compteurs/histogrammes, d'un endpoint Bearer fermé sans secret, de nettoyage récursif, de traces FastAPI/httpx et d'un fichier de heartbeat. Ces éléments sont réutilisés.

Les logs utilisent maintenant l'enveloppe 1.0.0, UTC `Z`, huit attributs de ressource et des attributs séparés. Les identifiants absents sont omis. Les bibliothèques, Uvicorn et Alembic passent par le formatter JSON ; les lignes d'accès brutes ne transportent plus URLs/IP. Les lignes sont limitées à 64 KiB, les données imbriquées sont bornées et les erreurs de sérialisation comptées sans exception métier. Le nettoyage est testé avec secrets synthétiques ; il ne constitue pas une preuve exhaustive pour toute nouvelle chaîne libre ajoutée à l'avenir. Éviter tout nouveau log de corps HTTP, objet utilisateur ou exception de provider contenant des données brutes.

L'identité est partagée entre logs, ressource OTel et `diddi_build_info`. `service.name=diddipay`, composant API ou worker, instance PID+UUID renouvelée au démarrage. `local` reste un alias de configuration pour `development`. Le déploiement doit fournir un SHA immuable (ni `unknown`, ni `latest`, ni un placeholder) et un identifiant d'installation ; la proposition Compose les exige mais leur valeur doit être revue. L'image reçoit le label OCI de révision via `VCS_REF`.

Les métriques HTTP canoniques remplacent leurs anciens noms `diddipay_http_*`. Les dashboards/règles locaux du dépôt sont adaptés. Les métriques métier existantes sont conservées ; les familles de dépendances sont alimentées au même point que les appels providers existants. Pas de double instrumentation des appels HTTP. Méthodes hors catalogue → `OTHER`, route inconnue → `/_unmatched`, routes connues → template FastAPI. Aucun identifiant individuel dans les labels. Une cible correspond à **un processus** ; ne pas ajouter `uvicorn --workers` ou `WEB_CONCURRENCY` sans agrégation Prometheus multiprocess revue. Ne pas scraper plusieurs registres derrière un répartiteur aléatoire.

Le worker expose son propre `/internal/metrics` sur 9001, sans publication hôte. Jobs et durée portent sur un cycle complet de maintenance. Le heartbeat n'avance qu'après succès ; les files exposent attente, messages loués, dead letters et âge du plus ancien. Le subscriber Redis embarqué dans l'API expose son heartbeat après poll/dispatch terminé, y compris à vide. Les erreurs de collecte des files ne doivent pas empêcher les cycles métier suivants. Les CLI manuelles ne sont pas des tâches planifiées déployées : leur sortie console historique n'est pas déclarée comme signal conforme ni durable.

Liveness ne contacte aucune dépendance. Readiness fait des lectures PostgreSQL et un PING Redis, avec une seule vérification simultanée et réponse avant 3 s. Aucune réussite passée n'est mise en cache. DB indisponible ou schéma absent → 503 ; Redis absent → `degraded` en callbacks, 503 en transport Redis Stream. Ce choix s'applique au rôle essentiel d'orchestrateur de paiement ; le bus wallet historique est une capacité dégradée explicitement distincte. Les providers et DiddiObserve ne retirent pas l'API du trafic.

## Traces, confidentialité et asynchrone

OTLP HTTP/protobuf utilise soit une base générique (ajout unique de `/v1/traces`), soit un endpoint spécifique utilisé tel quel. Aucune adresse par défaut. TLS vérifié, authentification par `OTEL_EXPORTER_OTLP_HEADERS`, certificat optionnel via `OTEL_EXPORTER_OTLP_CERTIFICATE`. Une base finissant déjà par `/v1/traces` est refusée pour éviter les chemins doubles. Le mode HTTP nécessite l'opt-in explicite `TRACING_ALLOW_INSECURE_LOCAL` ; il reste désactivé dans la proposition centrale.

Exporter asynchrone, 2 048 spans en file, lots de 256, délai 5 s, export borné à 5 s et arrêt à 5 s. Le SDK OTLP gère les reprises bornées avec backoff/jitter ; aucun stockage producteur illimité. Les pertes de file et d'export sont comptées. La comptabilisation de saturation s'appuie sur l'interne SDK 1.44, épinglé à cette série et couvert par test ; toute mise à niveau exige de rejouer ce test. Les pertes sont définitives, la reprise prouve l'envoi de nouvelles données, pas la restitution des spans perdus.

Avant la file d'export, une liste autorisée conserve méthode, route, statut et métadonnées messaging. URLs, IP, messages/stack d'exception, noms libres de spans et descriptions libres de statut sont supprimés. Les événements bruts d'exception ne sont pas exportés. Le budget de spans et d'attributs borne la structure ; la consommation réelle doit être mesurée sous charge représentative. Un budget provisoire de 128 MiB par processus pour la télémétrie est déclaré, pas une limite RSS garantie.

Le sampler respecte le parent et échantillonne 10 % des nouvelles traces ; régler `TRACING_SAMPLE_RATE=1` pour les validations. L'ingress public doit contrôler le sampling entrant pour qu'un client ne force pas une collecte illimitée. Cette protection reste à vérifier avec l'équipe centrale.

Le contexte W3C est stocké dans `telemetry_context`, séparément du payload signé. Chaque tentative différée crée sa propre trace de consommation et un **lien** vers la publication. Les appels callbacks propagent leur contexte HTTP et un request ID validé. Les métadonnées Redis Stream sont des champs distincts de `event` ; Redis PubSub utilise `telemetry_context`. Les consommateurs externes doivent confirmer ces champs et leurs liens avant C07 de bout en bout. La télémétrie ne change ni les IDs d'événements, ni les signatures, ni l'idempotence/lease, ni le nombre de tentatives métier.

## Valeurs à fournir et revue de déploiement

L'équipe centrale et le responsable DiddiPay doivent fournir :

1. Environnement, installation, SHA/image immuable, contact opérationnel et propriétaire des alertes.
2. Nom du réseau privé, adresses des deux cibles de scrape et CIDR **des pairs socket** autorisés. `X-Forwarded-For` n'est jamais utilisé pour autoriser la collecte. Exclure les proxies publics de ces CIDR ; refuser `/internal/metrics` à l'ingress public.
3. Secret Bearer propre au service/environnement (32 caractères minimum), son chemin côté Prometheus et sa rotation. Sans token, le producteur refuse la collecte. Le profil historique sans CIDR reste authentifié mais n'est pas une preuve d'isolation réseau.
4. URL OTLP exacte, en-têtes d'authentification issus du gestionnaire de secrets et CA éventuelle. Les monter/injecter à l'exécution ; ne pas les enregistrer dans Git, dans un manifeste rendu, ou dans les preuves de validation.
5. Passerelle TLS privée pour les ports HTTP internes 8000/9001, ou approbation documentée d'un transport local réellement isolé. Les templates utilisent HTTPS avec vérification de certificat. Les serveurs Python n'assurent pas eux-mêmes la terminaison TLS.
6. Chaîne unique de collecte Docker, labels de cible, politique de sampling à la frontière publique, stockage de traces et liens Grafana logs ↔ traces.
7. Rétention, expiration des backups/exports, capacité mesurée, runbook publié, routage/déduplication des alertes et SLO approuvé. Les anciens exemples SLO locaux ne sont pas une décision métier v1.

La rotation Docker proposée est 10 MiB × 5 fichiers par conteneur. Logs : 14 j staging/30 j production ; traces : 7/14 j ; métriques : 30/90 j. Ces rétentions sont des obligations **centrales à configurer**, pas une configuration appliquée par ce dépôt. Ne pas activer simultanément la collecte Docker centrale et le miroir fichier/Alloy historique. Ne pas appliquer l'overlay `docker-compose.observability.yml` avec le nouveau profil.

## Validation et passage à connected

Exécuter les tests avec une base PostgreSQL **de test isolée** ; `tests/conftest.py` migre et nettoie cette base. Définir explicitement `TEST_DATABASE_URL`, `DEPLOYMENT_ENVIRONMENT=test`, `OBSERVABILITY_ENVIRONMENT=test`, `TRACING_ENABLED=false`. Lancer `python -m pytest -q`. Le scénario `python -m scripts.validate_observability_outage` dure cinq minutes et n'appelle que son récepteur OTLP local et une opération HTTP synthétique en mémoire.

Sur une recette autorisée après revue/déploiement : vérifier le refus sans token, avec token invalide et depuis le réseau public, puis scraper chaque processus depuis le réseau autorisé. Couper DB/Redis de recette, observer liveness/readiness et les heartbeats. Faire un parcours synthétique API → outbox → worker → second service, avec échec/reprise, vérifier les liens et l'absence de double effet métier. Couper le collector pendant cinq minutes sous charge métier synthétique puis le rétablir.

Enfin conserver des preuves horodatées de réception au stockage, consultation Grafana, logs → trace → logs, comportement `no data`, séries actives, rétention et alertes. Aucun test de notification n'est autorisé implicitement par ce document. `connected` nécessite une ingestion démontrée ; `conformant-v1` exige tous les critères applicables sur la révision effectivement déployée.

## Runbook opérationnel

- **Collecte absente** : conserver l'état `unknown`, vérifier découverte, DNS, réseau, TLS, expiration du token et réponse de `/internal/metrics`. Ne pas déduire zéro erreur d'une absence de séries.
- **Worker stale** : vérifier processus, dernier heartbeat, DB/Redis, timeout provider et âge de l'outbox. Préserver les leases et IDs ; ne pas recréer une intention pour débloquer l'observabilité.
- **Readiness 503** : vérifier migration et accès DB, puis Redis si transport Stream. Le détail n'est pas public ; utiliser les logs autorisés. Ne pas retirer le service pour une panne DiddiObserve.
- **Pertes de télémétrie** : examiner saturation, indisponibilité et authentification OTLP. Réduire le sampling après décision versionnée, jamais augmenter indéfiniment les files. Reprendre la collecte sans modifier les transactions.
- **Rollback** : rétablir l'ancienne image/configuration après revue, conserver les colonnes nullables (compatibles avec l'ancien code). Restaurer les noms de métriques/dashboards ensemble ; garantir une seule chaîne de collecte. Ne pas exécuter de downgrade destructif pendant la livraison de messages.

L'affectation nominative, l'URL publiée de ce runbook et l'astreinte restent à renseigner dans le manifeste rendu.
