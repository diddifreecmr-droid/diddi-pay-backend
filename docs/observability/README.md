# DIDDI Observability 2.0.0 — diddipay-fund

État : **inventoried**, ingestion centrale non vérifiée. Aucun déploiement exécuté. Cette version remplace le raccordement fichier 1.1.1. Logs et traces utilisent exclusivement OTLP HTTP/protobuf vers les routes publiques authentifiées ; aucun volume partagé, socket Docker, réseau commun avec DiddiObserve ou CA privée n'est requis. La console locale reste disponible et conserve sa rotation Docker.

## Variables standard effectivement lues

| Variable conteneur | Application / priorité |
| --- | --- |
| OTEL_SERVICE_NAME | `observability_service_name`, alias historique OBSERVABILITY_SERVICE_NAME ; valeur canonique diddipay |
| ENVIRONMENT | `observability_environment`, prioritaire sur OBSERVABILITY_ENVIRONMENT ; ne change pas DEPLOYMENT_ENVIRONMENT métier |
| DEPLOYMENT_ID | `observability_deployment_id`, prioritaire sur OBSERVABILITY_DEPLOYMENT_ID |
| OTEL_EXPORTER_OTLP_PROTOCOL | http/protobuf uniquement ; grpc refusé |
| OTEL_EXPORTER_OTLP_ENDPOINT | Base traces, prioritaire sur TRACING_OTLP_ENDPOINT ; ajout unique /v1/traces |
| OTEL_EXPORTER_OTLP_TRACES_ENDPOINT | URL complète prioritaire sur la base et sur TRACING_OTLP_TRACES_ENDPOINT ; utilisée telle quelle |
| OTEL_EXPORTER_OTLP_LOGS_ENDPOINT | URL complète obligatoire si logs activés ; utilisée telle quelle |
| OTEL_EXPORTER_OTLP_HEADERS | Authentification OTLP dédiée, interprétée par le SDK ; jamais token métriques |
| OTEL_LOGS_ENABLED | false : aucun exporteur logs ; true : handler JSON OTLP activé |
| METRICS_BEARER_TOKEN | Secret existant, prioritaire sur l'alias METRICS_TOKEN ; aucune rotation |

OBSERVABILITY_RELEASE_SHA conserve le SHA réellement construit ; OBSERVABILITY_COMPONENT vaut api/worker selon le conteneur. L'instance est générée par processus. Schéma JSON toujours 1.0.0 ; contrat implémenté 2.0.0, sans déclaration de conformité centrale.

TRACING_ENABLED=false interdit les traces ; sinon une base ou URL spécifique non vide est nécessaire. Le réglage applicatif par défaut est true, mais le Compose prépare explicitement false tant que les routes centrales ne sont pas confirmées. Aucune destination localhost implicite. Pas de commutateur global OTEL_ENABLED. OBSERVABILITY_ENABLED ne contrôle que la fonction applicative emit(), pas les messages des bibliothèques ; les interrupteurs de transport ci-dessus sont déterminants.

## Correspondance Portainer → Compose → conteneurs

Le Compose accepte les variables communes directement. Pour préserver des paramètres déjà installés, il accepte aussi :

| Portainer existant | Conteneur API | Conteneur worker |
| --- | --- | --- |
| API_OTLP_LOGS_ENDPOINT / WORKER_OTLP_LOGS_ENDPOINT | OTEL_EXPORTER_OTLP_LOGS_ENDPOINT depuis API_ | depuis WORKER_ |
| API_OTLP_TRACES_ENDPOINT / WORKER_OTLP_TRACES_ENDPOINT | OTEL_EXPORTER_OTLP_TRACES_ENDPOINT depuis API_ | depuis WORKER_ |
| API_OTLP_HEADERS / WORKER_OTLP_HEADERS | OTEL_EXPORTER_OTLP_HEADERS depuis API_ | depuis WORKER_ |
| API_METRICS_TOKEN / WORKER_METRICS_TOKEN | METRICS_BEARER_TOKEN depuis API_ | depuis WORKER_ |

Pour chaque signal, un préfixe non vide prime sur la valeur commune. Sans préfixe, le Compose reprend la variable standard ; pour les métriques il reprend ensuite METRICS_TOKEN historique. Une variable commune vide n'efface donc pas un token historique non vide. Ne pas importer de ligne de secret vide dans Portainer ; conserver les valeurs déjà présentes. Les aliases OTLP historiques hérités du Compose de base sont vidés pour éviter une destination concurrente. Supprimer/reconcilier les anciens overrides API_/WORKER_ d'URL s'ils contredisent la remise 2.0.0, sans modifier leurs secrets implicitement.

## Métriques : cibles et CIDR

| Composant | Écoute dans le module | Publication / cible |
| --- | --- | --- |
| API, service app | 0.0.0.0:8000/internal/metrics | Le mapping existant `${APP_BIND_HOST:-0.0.0.0}:${APP_PORT:-48213}:8000` reste inchangé. Sur le réseau propre du module : http://app:8000/internal/metrics. Utiliser la cible réellement installée, pas supposer que le port par défaut est utilisé. |
| worker, service payment-worker | 0.0.0.0:9001/internal/metrics | http://payment-worker:9001/internal/metrics depuis le réseau propre au module. Aucun port hôte publié. Une passerelle ou un routage dédié est nécessaire pour un collecteur externe ; expose:9001 ne publie rien sur l'hôte. |

Les noms DNS app/payment-worker ne sont pas supposés résolus sur le réseau central. L'export sortant des logs/traces ne rend pas le worker accessible au scrape. La cible externe exacte du worker doit être fournie par infrastructure ; ce dépôt n'invente ni IP, ni port hôte libre, ni réseau commun. La fiche centrale du 19 novembre indique `service-diddipay` UP : c'est une déclaration centrale datée, pas une preuve locale du scrape distinct des deux composants.

METRICS_ALLOWED_CIDRS est une liste IPv4/IPv6 séparée par virgules. Non vide : l'API compare `request.client.host` et le worker `self.client_address[0]` à cette liste avant le contrôle Bearer. Le lancement Uvicorn livré utilise `--no-proxy-headers` : X-Forwarded-For ne peut pas réécrire le pair socket. Tout autre lanceur Uvicorn doit conserver ce réglage. Cela signifie aussi que l'application ne reconstruit plus IP/schéma d'origine depuis ces en-têtes ; contrôler les éventuelles redirections derrière le proxy en recette.

CIDR vide : pas de restriction IP supplémentaire, mais Bearer obligatoire. Pair non autorisé : 401 ; token absent/faux : 401 ; token non configuré : 503 pour un pair autorisé ; bon token et pair autorisé : 200. API désactivée via METRICS_ENABLED=false : 404 ; worker désactivé : 503. Derrière une passerelle, autoriser son IP socket réellement observée, puis filtrer les sources externes à la passerelle. Ne pas se fier à X-Forwarded-For ni élargir à 0.0.0.0/0 pour masquer une erreur.

## Logs, résilience et migration

Le même formatter nettoie les logs applicatifs et bibliothèques avant mise en file. Le handler OTLP partagé est attaché au logger payfund (propagate=false) et à la racine : un seul export par événement. La configuration est idempotente et réinstallée après Alembic ; tests après configuration Uvicorn pour API et worker. Texte ordinaire et messages structurés sont couverts. Les diagnostics opentelemetry/urllib3/requests restent uniquement locaux pour éviter la récursion d'export.

Corps OTLP : enveloppe JSON complète 1.0.0 limitée à 64 KiB ; ressource commune, timestamp source et véritables trace_id/span_id conservés. File SDK logs 256 événements, lots 64, délai 1 seconde, export/retries bornés à 5 secondes et arrêt borné à 5 secondes. Capacité brute des corps en file : 16 MiB, hors surcoût SDK. Pertes queue_full/export_failed/collector_rejected/invalid_response/serialization dans diddi_telemetry_dropped_total. Crash ou deadline d'arrêt peut perdre des événements en mémoire ; aucune promesse de livraison exactement une fois. Traces : instrumentation et file bornée existantes conservées.

OBSERVABILITY_LOG_FILE est vide dans le profil central. Le handler fichier historique reste optionnel pour dépannage local, jamais comme transport central. Faire désactiver toute collecte fichier/stdout existante avant activation générale OTLP ; vérifier un canari isolé, documenter fenêtre de bascule/pertes/doublons. Ne pas supprimer automatiquement les anciens volumes ou archives.

## Valeurs connues et activation

Remise publique du 19 novembre : ENVIRONMENT=staging, DEPLOYMENT_ID=diddipay-staging ; base https://logs-otel.diddifree.com/otlp/diddipay ; logs /v1/logs et traces /v1/traces sur cette base. Les routes sont préparées mais non déclarées déployées/vérifiées. Secret OTLP : remise privée centrale, non copié dans ce dépôt. Conserver les secrets métriques existants.

Fichier local ignoré : observabilite.env. Modèle public : deploy/diddiobserve/producer.env.example. Les exports restent désactivés jusqu'à confirmation des routes. Voir [LANCEMENT-STAGING.md](LANCEMENT-STAGING.md). Manifeste : deploy/diddiobserve/manifest.v1.json, version 2.0.0. Les références historiques 1.0/1.1.1 sont des preuves anciennes, pas les consignes de raccordement actuelles.

## Validations encore nécessaires

Réception réelle Loki/Tempo, corrélation Grafana, deux cibles métriques UP, contrôle d'accès via le routage réel, parcours HTTP/asynchrone de bout en bout, panne collecteur cinq minutes pour les deux signaux sous charge représentative, arrêt/reprise, rétention/backups/alertes. Aucun critère C01–C12 entier n'est non applicable ; sous-profils absents inchangés : WebSocket, scheduler/cron et consommateur Redis Stream.

Runbook : si ingestion absente vérifier activation, présence des paramètres sans afficher secrets, DNS/TLS, authentification et compteurs de perte. Une panne collecteur ne doit pas rendre readiness négative. Worker stale : vérifier processus, heartbeat, DB/Redis et backlog sans recréer d'opération métier. Rollback : restaurer image/config approuvées et une seule chaîne centrale ; garder les colonnes outbox compatibles et ne pas rejouer les archives implicitement.
