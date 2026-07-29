"""Engine et session SQLAlchemy.

Choix : SQLAlchemy **synchrone**. L'architecture (§0) fixe ~10 000 transactions/jour, soit
7/minute : l'asynchronisme n'apporte rien à ce volume, alors qu'il complique le partage d'une
même transaction DB entre `fund` et `wallet` — partage explicitement requis par l'architecture
(§5 : « transaction DB partagée possible si les deux écritures doivent être atomiques ensemble »).
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from payfund_app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    echo=_settings.sql_echo,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI : une session = une requête HTTP = une transaction métier."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
