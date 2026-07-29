#!/bin/sh
set -e

# Instance unique, pas de déploiement à plusieurs répliques ici : exécuter les migrations au
# démarrage du conteneur est sûr et évite d'oublier cette étape à chaque redéploiement sur le
# VPS. À revoir si ce service est un jour répliqué (course entre plusieurs conteneurs migrant
# en même temps).
alembic upgrade head

exec uvicorn payfund_app.main:app --host 0.0.0.0 --port 8000
