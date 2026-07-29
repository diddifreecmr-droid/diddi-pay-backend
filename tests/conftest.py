"""Fixtures de test : base PostgreSQL dédiée, migrée par Alembic."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://payfund:payfund@localhost:5434/payfund_test",
)
# Doit être posé avant tout import de `payfund_app.core.database` (engine créé à l'import).
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from payfund_app.core.database import get_session  # noqa: E402
from payfund_app.core.security import CurrentUser  # noqa: E402
from payfund_app.main import app  # noqa: E402
from payfund_app.modules.wallet.application.ledger import LedgerService  # noqa: E402
from payfund_app.modules.wallet.domain.entities import AccountType  # noqa: E402
from payfund_app.modules.wallet.domain.money import Money  # noqa: E402
from payfund_app.modules.wallet.infra.repositories import (  # noqa: E402
    AccountRepository,
    GatewayAccountRepository,
    UserPhoneRepository,
)
from payfund_app.modules.wallet.presentation.deps import get_current_user  # noqa: E402
from payfund_app.shared_kernel.events.bus import InMemoryEventBus, set_bus  # noqa: E402

TABLES = [
    "fund.loan_status_history",
    "fund.repayment_schedule",
    "fund.loans",
    "fund.investments",
    "fund.campaigns",
    "wallet.currency_conversions",
    "wallet.exchange_rates",
    "wallet.ledger_entries",
    "wallet.transactions",
    "wallet.gateway_accounts",
    "wallet.user_phones",
    "wallet.accounts",
]


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DATABASE_URL, future=True)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS wallet CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS fund CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")

    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def bus() -> InMemoryEventBus:
    memory_bus = InMemoryEventBus()
    set_bus(memory_bus)
    return memory_bus


class _Auth:
    """Permet à un test de changer d'utilisateur courant en cours de scénario."""

    def __init__(self) -> None:
        self.user = CurrentUser(uuid.uuid4(), "user", "active")

    def as_user(self, user_id: uuid.UUID) -> None:
        self.user = CurrentUser(user_id, "user", "active")


@pytest.fixture
def auth() -> _Auth:
    return _Auth()


@pytest.fixture
def client(session: Session, auth: _Auth, bus: InMemoryEventBus) -> Iterator[TestClient]:
    def _session_override() -> Iterator[Session]:
        yield session
        session.commit()

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_current_user] = lambda: auth.user
    # Pas de `with TestClient(...)` : on n'exécute pas le lifespan, donc pas de thread Redis.
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- Aides de scénario -------------------------------------------------------


@pytest.fixture
def make_user(session: Session):
    """Crée un utilisateur wallet : compte actif + téléphone indexé."""

    def _make(phone: str | None = None) -> tuple[uuid.UUID, uuid.UUID]:
        user_id = uuid.uuid4()
        account = AccountRepository(session).create(
            user_id=user_id, account_type=AccountType.USER
        )
        if phone:
            UserPhoneRepository(session).upsert(user_id, phone)
        session.commit()
        return user_id, account.id

    return _make


@pytest.fixture
def fund_account(session: Session):
    """Approvisionne un compte depuis un compte suspense de passerelle.

    C'est exactement le mouvement d'un dépôt Mobile Money décrit au §2 de l'architecture : crédit
    de l'utilisateur, débit du compte suspense (qui passe en négatif jusqu'à réconciliation).
    """
    def _fund(account_id: uuid.UUID, amount: int, provider: str = "orange_money") -> None:
        accounts = AccountRepository(session)
        gateways = GatewayAccountRepository(session)
        suspense_id = gateways.account_id_for(provider)
        if suspense_id is None:
            suspense = accounts.create(
                user_id=None,
                account_type=AccountType.TECHNICAL,
                reference=f"gateway:{provider}",
                allows_negative_balance=True,
            )
            gateways.register(provider, suspense.id)
            suspense_id = suspense.id
        LedgerService(session).transfer(
            source_account_id=suspense_id,
            destination_account_id=account_id,
            montant=Money(amount),
            type_="deposit",
            reference=f"test:seed:{provider}",
            idempotency_key=f"test-seed-{uuid.uuid4()}",
            origin_module="wallet",
        )
        session.commit()

    return _fund
