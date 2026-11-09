"""Alembic environment: migrates the platform database from the table metadata."""

from alembic import context
from sqlalchemy import URL, create_engine

from guildhall.platform import accounts, dedupe, outbox, scheduler
from guildhall.platform.database import DatabaseSettings, metadata
from guildhall.platform.logs import LogSettings, configure_logging
from guildhall.plugins import CATALOGUE

TABLE_MODULES = (accounts, dedupe, outbox, scheduler)
"""Modules whose tables migrations manage; importing them registers the tables."""

TARGET_METADATA = [metadata, *(plugin().tables for plugin in CATALOGUE.values())]
"""The platform's tables and every catalogued plug-in's, enabled or not, so the
schema never depends on configuration (docs/adr/0029)."""


def database_url() -> URL:
    """The configured database, unless the caller passed `url` in the attributes."""
    url = context.config.attributes.get("url")
    return url if isinstance(url, URL) else DatabaseSettings().url()


def run_migrations_offline() -> None:
    """Print the migration SQL instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=database_url().render_as_string(hide_password=True),
        target_metadata=TARGET_METADATA,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the database in one transaction."""
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=TARGET_METADATA)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Log each step in the platform's format when run as the `alembic` command;
    # callers of the Python API (the test fixtures) keep their own logging.
    if context.config.cmd_opts is not None:
        configure_logging(LogSettings())
    run_migrations_online()
