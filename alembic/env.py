from logging.config import fileConfig

from sqlalchemy import engine_from_config, inspect, pool, text

from alembic import context
from payfund_app.core.config import get_settings
from payfund_app.core.database import Base

# Import des modèles pour peupler Base.metadata (autogenerate).
from payfund_app.modules.fund.infra import models as fund_models  # noqa: F401
from payfund_app.modules.payments.infra import models as payment_models  # noqa: F401
from payfund_app.modules.wallet.infra import models as wallet_models  # noqa: F401

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)
    from payfund_app.shared_kernel.logging import configure_logging

    configure_logging()

target_metadata = Base.metadata


def _ensure_version_table_capacity(connection) -> None:
    """Keep Alembic's version table compatible with descriptive revision IDs.

    Alembic creates ``version_num`` as VARCHAR(32) by default. This project uses
    date-based revision names, some of which are longer than 32 characters. A
    fresh database would otherwise fail while upgrading to the current head.
    """
    inspector = inspect(connection)
    if not inspector.has_table("alembic_version"):
        connection.execute(
            text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(128) NOT NULL PRIMARY KEY"
                ")"
            )
        )
        return

    column = next(
        (
            item
            for item in inspector.get_columns("alembic_version")
            if item["name"] == "version_num"
        ),
        None,
    )
    length = getattr(column["type"], "length", None) if column else None
    if length is not None and length < 128:
        connection.execute(
            text(
                "ALTER TABLE alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(128)"
            )
        )


def include_object(obj, name, type_, reflected, compare_to):
    # Ne gérer que nos deux schémas applicatifs.
    if type_ == "table":
        return obj.schema in ("wallet", "fund", "payments")
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _ensure_version_table_capacity(connection)
        # The capacity check may create or alter the version table before
        # Alembic opens its transactional migration context.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
