# Lancement staging : procédure DiddiPay 1.1.1

Cette procédure prépare un déploiement Docker Standalone via Portainer. Elle n'a pas été exécutée sur le serveur. Staging a été confirmé par le responsable. L'import du fichier de variables ne suffit pas : il faut aussi charger le Compose d'observabilité et préparer la collecte centrale.

## 1. Fichier à compléter — responsable DiddiPay

Fichier local à importer : `observabilite.env`, à la racine du dépôt. Il est ignoré par Git pour protéger les futurs secrets. Les variables métier existantes de la stack doivent être conservées : ce fichier ne remplace pas toute sa configuration.

Valeurs préremplies :

| Variable | Valeur à mettre | Statut |
| --- | --- | --- |
| OBSERVABILITY_RELEASE_SHA | SHA complet déjà prérempli dans le fichier local `observabilite.env` | Révision préparée avec code et preuves 1.1.1. Doit correspondre au checkout/image réellement construit. |
| OBSERVABILITY_ENVIRONMENT | staging | Confirmé. |
| OBSERVABILITY_DEPLOYMENT_ID | diddipay-staging-01 | Nom proposé pour cette installation ; transmettre tel quel à la centrale. Si elle a déjà attribué un ID différent, remplacer par cet ID partout. |
| OBSERVABILITY_LOG_VOLUME_NAME | diddipay-observability-logs-staging-01 | Nom proposé pour le nouveau volume ; transmettre tel quel à la centrale. Ce n'est pas une affirmation qu'il existe déjà. |

Neuf valeurs restent vides. **Attendre les réponses avant l'import final et le déploiement** :

| Variable | Fournisseur | Valeur exacte attendue |
| --- | --- | --- |
| OBSERVABILITY_LOG_GID | Infrastructure + DiddiObserve | Entier : GID partagé autorisant Alloy à lire les fichiers. L'UID producteur est déjà fixé à 10001 ; ce n'est pas automatiquement le GID d'Alloy. |
| DIDDIOBSERVE_NETWORK | Infrastructure | Nom exact d'un réseau Docker externe existant sur l'hôte DiddiPay, accessible aux collecteurs/à la passerelle autorisée. Ne pas copier le réseau d'ID ou Send. |
| METRICS_ALLOWED_CIDRS | Infrastructure | Liste séparée par virgules des IP/CIDR des pairs socket qui scraperont les deux processus. Ne pas inclure le proxy public ; ne pas mettre 0.0.0.0/0. |
| API_METRICS_TOKEN | DiddiObserve | Secret Bearer dédié à l'entrée API staging, au moins 32 caractères ; même valeur dans le fichier secret du scrape central. |
| WORKER_METRICS_TOKEN | DiddiObserve | Autre secret Bearer dédié au worker staging, au moins 32 caractères. |
| API_OTLP_TRACES_ENDPOINT | DiddiObserve | URL HTTPS complète et testée, route `/otlp/<id-registre-api>/v1/traces`. Fournir domaine et ID réels. |
| WORKER_OTLP_TRACES_ENDPOINT | DiddiObserve | URL HTTPS complète et testée, route `/otlp/<id-registre-worker>/v1/traces`. |
| API_OTLP_HEADERS | DiddiObserve | Ligne complète `authorization=Basic ` suivie du Base64 des identifiants API attribués par la centrale ; pas le token Bearer de métriques. |
| WORKER_OTLP_HEADERS | DiddiObserve | Même format, avec les identifiants distincts du worker. |

Les documents centraux fournis n'attribuent pas encore ces valeurs à DiddiPay. Aucun domaine d'ingestion réel ne peut être déduit de `ONBOARDING-READY.md`. Ne pas utiliser les identifiants d'un autre module, ni ajouter une CA privée : le profil OTLP retenu utilise HTTPS public et la confiance système standard.

## 2. Demande prête à transmettre — attendre DiddiObserve/infrastructure

> Préparer le raccordement staging de `diddipay` (namespace `diddi`), déploiement proposé `diddipay-staging-01`. Créer/confirmer deux IDs de registre, composants `api` et `worker`. Fournir les neuf valeurs du tableau ci-dessus via le canal de secrets approprié. Confirmer que l'Alloy destinataire et DiddiPay partagent le même hôte Docker. Le volume producteur proposé est `diddipay-observability-logs-staging-01`. Fichiers actifs : `/var/log/diddipay/observability-api.jsonl` et `/var/log/diddipay/observability-worker.jsonl`. Monter le volume en lecture seule dans Alloy et collecter uniquement ces deux fichiers sous leurs identités respectives ; pas de glob `*.log`, pas d'archives, pas de socket Docker, pas d'OTLP logs en parallèle. Persister les positions de lecture côté Alloy. Préparer les scrapes protégés pour API port 8000 et worker port 9001, chemin `/internal/metrics`. Confirmer le transport de scrape (passerelle HTTPS ou HTTP privé isolé explicitement accepté), les cibles exactes et le chemin d'ingress utilisateur avant suppression des ports hôte. Transmettre les preuves d'activation après déploiement.

Cette demande est à transmettre par le responsable ; elle n'a pas été envoyée automatiquement.

Si Alloy est sur un autre hôte : **arrêter cette procédure**. Les volumes nommés locaux ne sont pas partagés entre hôtes. Il faut un raccordement réseau différent et une adaptation validée, pas créer un volume vide sur le serveur central.

## 3. Avant Portainer — responsable dépôt/infrastructure

1. Publier/revoir la branche locale `codex/observability-v1-1-1` dans le dépôt Git accessible à Portainer, ou intégrer les changements à la branche de déploiement. La livraison locale ne constitue pas un push. Le dépôt Git configuré dans la stack reste le dépôt DiddiPay existant.
2. Si la révision construite diffère de celle préremplie dans le fichier, remplacer `OBSERVABILITY_RELEASE_SHA` par le SHA complet du checkout réellement construit (`git rev-parse HEAD`), même si la différence ne concerne que la documentation. Ne pas seulement changer l'étiquette d'une ancienne image.
3. Confirmer Docker Standalone. Ce profil n'est pas prêt pour Swarm : `build`, `depends_on` et la fusion Compose doivent être traités autrement dans ce cas.
4. Confirmer que la version Compose utilisée par Portainer accepte `ports: !reset []`. Ne pas enlever cette directive pour contourner une erreur : elle supprime la publication hôte de l'API. Si elle n'est pas supportée, préparer un Compose fusionné équivalent pour revue.
5. Faire raccorder l'ingress à un réseau contenant `app:8000` avant de retirer l'ancien accès par port hôte. **Si le proxy actuel vise IP-hôte:port-publié, ne pas déployer cet overlay avant correction de son chemin.** L'adresse du proxy actuel n'a pas été vérifiée dans cette mission.
6. Sur le serveur de recette, valider la construction et l'exécution UID 10001/GID confirmé : migrations DB, heartbeat worker et écriture du volume. Le Docker local de cette mission était indisponible : ces vérifications ne sont pas déjà faites.

## 4. Préparer la stack Portainer — responsable DiddiPay

Dans la stack Git DiddiPay existante, conserver toutes les variables métier et le nom de stack actuel. Ne pas créer une deuxième stack avec les mêmes `container_name`.

- Repository reference : branche/revision effectivement publiée et approuvée ; si la branche proposée est conservée, `refs/heads/codex/observability-v1-1-1`.
- Compose path : `docker-compose.yml`.
- Additional paths → Add file : `docker-compose.diddiobserve.yml`.
- Ne pas ajouter `docker-compose.observability.yml` (ancien monitoring local).
- Environment variables → **Load variables from .env file** : sélectionner le fichier local `observabilite.env` complété.
- Contrôler visuellement que les 13 variables du fichier sont présentes et que les neuf champs initialement vides sont remplis. Conserver les variables métier existantes. Ne pas ajouter `env_file: observabilite.env` au Compose : Portainer reçoit les valeurs par import, ce fichier reste local et ignoré par Git.

Portainer documente les chemins additionnels et l'import ici : https://docs.portainer.io/user/docker/stacks/add ; édition des stacks Git : https://docs.portainer.io/user/docker/stacks/edit. Selon la version et le mode de création de la stack existante, ces réglages peuvent nécessiter son gestionnaire Git plutôt que l'éditeur YAML.

**Arrêt pour revue avant Update/Deploy** : valeurs centrales fournies, publication Git disponible, ingress vérifié, configuration fusionnée acceptée, sauvegarde/retour arrière de recette préparés. Aucun clic de déploiement n'est autorisé implicitement par la préparation de ces fichiers.

## 5. Après le déploiement autorisé — vérifications immédiates

1. `observability-log-init` doit finir avec code 0 ; API et worker doivent rester actifs. Une erreur de GID, volume ou migration est à corriger avant activation centrale.
2. Dans la console Portainer de `payfund-app`, lancer cette commande de lecture ; répéter dans le conteneur du service `payment-worker` :

```sh
python -c 'import os; p=os.environ["OBSERVABILITY_LOG_FILE"]; print("uid/gid:",os.getuid(),os.getgid()); print("file:",p); print("read/write:",os.access(p,os.R_OK),os.access(p,os.W_OK)); print("bytes:",os.path.getsize(p))'
```

Attendu : UID 10001, GID confirmé, `read/write: True True`, chemin API ou worker attendu, taille non nulle après les premiers événements du processus principal. Si le fichier est absent, inspecter les logs de démarrage et le montage ; s'il reste vide après activité, contrôler le compteur `file_write_failed`. Ne pas lancer un second logger sur le même fichier : le verrou exclusif le refuserait. Vérifier séparément qu'Alloy peut lire ces fichiers avec son propre utilisateur, en montage lecture seule.

3. Dans la console de l'API :

```sh
python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/payfund/v1/health",timeout=4).status); print(urllib.request.urlopen("http://127.0.0.1:8000/payfund/v1/ready",timeout=4).status)'
```

Attendu : 200/200 si les dépendances critiques sont disponibles ; un 503 readiness doit être expliqué et corrigé. Le collecteur n'est pas une dépendance de readiness.

4. DiddiObserve réalise les scrapes depuis les IP autorisées avec chacun des secrets : HTTP 200 et séries `diddi_*`. Sans secret/secret incorrect : refus, jamais 200. Une requête locale avec token peut être refusée par le CIDR : c'est normal si localhost ne fait pas partie des pairs autorisés. Ne pas élargir le CIDR pour masquer un problème réseau.
5. DiddiObserve vérifie `up=1` pour les deux cibles, arrivée des nouveaux logs API/worker dans Loki, traces dans Tempo et navigation Grafana log → trace. Faire un parcours synthétique API → outbox → worker → service de recette, avec échec/reprise, sans transaction réelle.
6. En recette, vérifier rotation pendant collecte et redémarrage producteur/Alloy sans relecture d'archives ; mesurer pertes/doublons, vérifier les positions persistantes et le montage lecture seule. Rejouer la panne collecteur sous charge synthétique, puis vérifier reprise, alertes, rétention et sauvegardes. Ces points terminent les critères applicables ; ils ne sont pas couverts par l'import `.env`.

L'état reste `inventoried` avant preuve d'ingestion ; `connected` seulement après arrivée des signaux ; aucune conformité complète avant toutes les preuves applicables C01–C12. Les validations locales existantes sont dans `VALIDATION-v1.1.1.md`.
