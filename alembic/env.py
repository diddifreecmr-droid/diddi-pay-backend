from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from payfund_app.core.config import get_settings
from payfund_app.core.database import Base

# Import des modèles pour peupler Base.metadata (autogenerate).
from payfund_app.modules.fund.infra import models as fund_models  # noqa: F401
from payfund_app.modules.payments.infra import models as payment_models  # noqa: F401
from payfund_app.modules.wallet.infra import models as wallet_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


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
