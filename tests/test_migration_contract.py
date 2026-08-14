"""Keep SQLAlchemy metadata and the deployed Alembic schema in lockstep."""

from alembic import command
from alembic.config import Config

def test_alembic_has_no_implicit_upgrade_operations(engine):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))

    command.check(config)
