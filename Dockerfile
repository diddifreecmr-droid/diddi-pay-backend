FROM python:3.11-slim

ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF
ENV OBSERVABILITY_RELEASE_SHA=$VCS_REF

WORKDIR /app

# psycopg[binary] et pyjwt[crypto] embarquent des wheels compilées : aucun paquet de build
# système n'est nécessaire ici.
COPY pyproject.toml ./
COPY payfund_app ./payfund_app
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Git checkouts on Windows may materialize this shell script as CRLF. Normalize it in the
# Linux image so Docker starts reliably from both developer machines and CI/Portainer.
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

# Port interne fixe : seul le port hôte (APP_PORT, dans docker-compose.yml) doit être choisi en
# fonction de ce qui est déjà pris sur le serveur — le port interne au réseau Docker n'entre
# jamais en conflit avec les autres piles.
EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
