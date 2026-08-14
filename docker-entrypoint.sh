#!/bin/sh
set -e

# Instance unique, pas de déploiement à plusieurs répliques ici : exécuter les migrations au
# démarrage du conteneur est sûr et évite d'oublier cette étape à chaque redéploiement sur le
# VPS. À revoir si ce service est un jour répliqué (course entre plusieurs conteneurs migrant
# en même temps).
python - <<'PY'
from sqlalchemy import create_engine, inspect
import os
import subprocess

database_url = os.environ["DATABASE_URL"]
engine = create_engine(database_url, future=True)
with engine.connect() as conn:
    inspector = inspect(conn)
    has_wallet_accounts = inspector.has_table("accounts", schema="wallet")
    has_version_table = inspector.has_table("alembic_version", schema=None)

if has_wallet_accounts and not has_version_table:
    subprocess.check_call(["alembic", "stamp", "head"])
else:
    subprocess.check_call(["alembic", "upgrade", "head"])
PY

exec uvicorn payfund_app.main:app --host 0.0.0.0 --port 8000
