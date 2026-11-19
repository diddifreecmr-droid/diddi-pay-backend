# Validation du raccordement producteur 1.1.1

Date : 17 novembre 2026. Périmètre : diddipay-fund uniquement. État : **inventoried** ; aucun déploiement, aucune ingestion centrale démontrée.

## Modifications

Écriture JSONL asynchrone bornée, nettoyage et contexte capturés avant mise en file, même écrivain pour application et bibliothèques. Rotation 10 MiB + 5 archives par composant ; verrou exclusif OS, noms fixes API/worker, récupération d'une ligne partielle après crash, pertes mesurées et arrêt borné. Configuration Alembic réinstalle le logger de démarrage. Schéma JSON inchangé 1.0.0, version du protocole 1.1.1.

Compose préparé : volume nommé explicite, UID applicatif non-root 10001 et GID partagé à fournir, initialisation limitée au répertoire de logs, fichiers distincts API/worker, secrets Bearer et endpoints/en-têtes Basic OTLP distincts par entrée de registre. Le manifeste déclare droits, budgets, reprise, fichiers actifs, archives et collecte centrale unique. Pas de modification de règles ou transactions de paiement.

## Preuves locales de cette révision

- `evidence/pytest-v1.1.1.txt` : **26 tests réussis, 1 désélectionné**, un avertissement FastAPI/Starlette existant. Commande : `python -m pytest tests/test_observability_files.py tests/test_observability_v1.py -k 'not durable_outbox' -q`, environnement de test avec URL DB synthétique explicite.
- 7 nouveaux cas fichiers : rotation avec 100 événements par cycle et redémarrage, limites d'archives/disque, erreurs EACCES/ENOSPC injectées avec récupération, saturation/écriture bloquée et arrêt borné, verrou exclusif, récupération d'une ligne incomplète, logs application/bibliothèque nettoyés avec corrélation et réinitialisation du logger.
- `docker compose --env-file <fichier temporaire synthétique> -f docker-compose.yml -f docker-compose.diddiobserve.yml config --quiet` : code 0. Aucun secret réel dans ce rendu de validation.
- Contrôle Python/Ruff et `git diff --check` consignés dans `evidence/revision-v1.1.1.json` avec la révision Git.

Docker Engine indisponible localement : ni construction/lancement de conteneurs, ni PostgreSQL isolé, ni droits POSIX réels validés. Le cas outbox nécessitant PostgreSQL est désélectionné. Les 501 tests et la panne OTLP de 300 secondes du rapport 1.0.0 sont **des preuves historiques**, pas une nouvelle exécution complète sur cette révision. Les défauts disque sont injectés ; le test de verrou tourne sous Windows. Le contrôle de lecture Alloy, son état de positions et la concurrence sous Linux restent nécessaires en recette.

## C01–C12 : état et étapes restantes

| Critère | État de cette livraison |
| --- | --- |
| C01 | Manifeste 1.1.1 et identité locale vérifiés ; registre, contacts, hôte et SHA déployé à confirmer. |
| C02 | JSON application/bibliothèque, UTC, nettoyage et contexte testés ; capture exhaustive du démarrage du conteneur et migrations à vérifier. |
| C03 | Tests synthétiques de nettoyage réussis ; droits et confidentialité jusqu'au stockage central à vérifier. |
| C04 | Tests HTTP/cardinalité/histogrammes rejoués ; capacité réelle à mesurer. |
| C05 | Refus de scrape/CIDR API-worker rejoués ; accès réseau et secrets par composant à valider sur la plateforme. |
| C06 | Parenté HTTP et corrélation locales rejouées ; parcours distribué DIDDI central manquant. |
| C07 | Liens et reprise en mémoire rejoués ; persistance PostgreSQL seulement couverte par la preuve historique, parcours externe manquant. |
| C08 | Tests de décisions de santé et délais rejoués ; panne réelle en recette manquante. |
| C09 | Heartbeat/worker testés localement ; blocage/backlog/alertes dans la collecte centrale manquants. |
| C10 | Fichier lent, saturation, erreurs disque et reprise testés ; panne OTLP 5 min historique non rejouée, charge représentative et RSS à valider. |
| C11 | **Non validé** : Loki/Tempo/Prometheus, Grafana, corrélation et no-data requièrent DiddiObserve. |
| C12 | Rotation/reprise/limites locales testées ; **partiel** : droits réels UID/GID, montage Alloy lecture seule, rotation sous collecte et positions persistées, pertes/doublons, rétention/backups et alertes restent à vérifier. |

Aucun critère entier non applicable. Les exclusions déjà documentées restent : pas de scheduler/cron ni WebSocket ; pas de consommateur Redis Stream dans ce module. Pas de déclaration de conformité complète avant C11/C12 et le parcours de bout en bout.

Le raccordement est décrit dans `README.md` et `deploy/diddiobserve/producer.env.example`. L'équipe centrale doit confirmer les deux entrées de registre, URLs publiques HTTPS, secrets Basic/Bearer, cibles de scrape, réseau et groupe partagé. Le volume doit exister sur le même hôte qu'Alloy. Les chemins `.jsonl` doivent remplacer le motif historique `*.log` côté collecte, sans collecte des archives. Ces actions sont **à préparer/revoir avec la centrale**, non exécutées dans cette livraison.
