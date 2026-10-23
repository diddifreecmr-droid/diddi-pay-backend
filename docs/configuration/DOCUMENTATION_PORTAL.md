# Portail documentaire

Le portail est une vue de la documentation versionnée du module, servie avec MkDocs en strict.

## Staging

Activer explicitement le portail avec :

```text
DOCS_PUBLIC_ENABLED=true
DOCS_PORT=48080
```

Le déploiement staging doit vérifier que la page d'accueil et `/healthz` répondent `200`.

Le service Compose fait partie de la stack du module :

```text
docker compose up -d --build docs
```

Pour un lancement documentaire local isolé :

```text
docker compose -f docker-compose.docs.yml -f docker-compose.docs.local.yml up --build
```

## Production

La valeur obligatoire est :

```text
DOCS_PUBLIC_ENABLED=false
```

Dans ce mode, aucune documentation publique ne doit être exposée et la route du portail doit
répondre `404`. L'API métier conserve son propre `/payfund/v1/docs` selon la politique d'accès
du déploiement.

La construction locale stricte est :

```text
mkdocs build --strict
```
