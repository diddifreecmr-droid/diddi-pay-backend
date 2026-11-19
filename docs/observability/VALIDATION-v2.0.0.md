# Validation locale 2.0.0 — 19 novembre 2026

Périmètre : diddipay-fund seulement ; aucune modification de plateforme ou de secrets métriques, aucun déploiement. État `inventoried`, aucune conformité complète déclarée.

## Résultats

- `evidence/pytest-v2.0.0.txt` : 33 tests réussis, 1 désélectionné, 1 avertissement existant. Suites v2, v1 et fichiers historiques ; le test PostgreSQL durable est exclu faute de base isolée disponible. Les 501 tests historiques ne sont pas une nouvelle exécution sur cette révision.
- Nouveaux tests : variables standard/priorités, rejet de grpc, branche applicative et texte bibliothèque après configuration Uvicorn (API et worker séparément), exactement un export par événement, nettoyage et corrélation, désactivation sans exporter, absence de récursion des diagnostics, véritable POST protobuf au chemin logs exact, réponse d'échec suivie de récupération, comptage des pertes, saturation de la file et arrêt borné. Le test de chemin traces réel existant est rejoué.
- `evidence/configuration-v2.0.0.json` : Compose rendu avec des valeurs synthétiques, trois scénarios de secrets existants (METRICS_TOKEN, METRICS_BEARER_TOKEN, API_/WORKER_) préservés exactement ; correspondance des noms standard dans les deux conteneurs ; aucun volume de logs/réseau externe requis ; port API existant conservé, worker non publié sur l'hôte.
- Ruff sur les fichiers Python concernés, compilation et `git diff --check` réussis.

## Limites et critères

C01–C05 : identité/configuration, JSON nettoyé et endpoint métriques testés localement ; le routage réel et les paramètres des processus déployés restent à vérifier. Le lanceur livré désactive les proxy headers Uvicorn pour que le CIDR contrôle le pair socket ; les autres lanceurs doivent respecter ce réglage.

C06/C07 : parenté HTTP et liens asynchrones locaux couverts par la suite ; parcours distribué déployé et persistance PostgreSQL à revalider. C08/C09 : tests de santé/heartbeat rejoués, infrastructure réelle non testée.

C10 : file logs saturée, export en échec/reprise, arrêt borné localement testés. Ce n'est pas une nouvelle panne de cinq minutes des deux signaux sous charge représentative ; RSS/CPU réels à mesurer. Les pertes/doublons de la bascule fichier → OTLP sont à vérifier avec la centrale.

C11 : aucune réception Loki/Tempo ni consultation Grafana prouvée. C12 : files/lots/arrêt locaux couverts, rétention/backups/règles/alertes et fin de collecte historique restent centraux.

Aucun critère entier non applicable. Sous-profils absents inchangés : WebSocket, scheduler/cron, consommateur Redis Stream. L'export stdout/fichier central de l'ancien profil n'est plus conforme au protocole 2.0.0 ; les copies locales facultatives ne sont pas collectées.

## Activation

La fiche centrale publique du 19 novembre fournit le service diddipay, staging, déploiement diddipay-staging, et les routes sous https://logs-otel.diddifree.com/otlp/diddipay. Elle indique la configuration préparée, non déployée, et un scrape `service-diddipay` UP ; cela ne prouve pas la collecte distincte de notre worker. Les exports du fichier local restent désactivés en attendant confirmation des routes. Aucun accès au secret OTLP privé ni appel vers ces routes n'a été réalisé pendant ces validations.

La révision Git et les références de preuves sont enregistrées dans `evidence/revision-v2.0.0.json`. Pour le raccordement actuel, suivre README.md et LANCEMENT-STAGING.md ; les rapports 1.0/1.1.1 sont historiques.
