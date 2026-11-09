"""Alembic environment: migrates the platform database from the table metadata."""

from alembic import context
from sqlalchemy import URL, create_engine

from guildhall.platform import dedupe, outbox
from guildhall.platform.database import DatabaseSettings, metadata

TABLE_MODULES = (dedupe, outbox)
"""Modules whose tables migrations manage; importing them registers the tables."""


def database_url() -> URL:
    """The configured database, unless the caller passed `url` in the attributes."""
    url = context.config.attributes.get("url")
    return url if isinstance(url, URL) else DatabaseSettings().url()


def run_migrations_offline() -> None:
    """Print the migration SQL instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=database_url().render_as_string(hide_password=True),
        target_metadata=metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the database in one transaction."""
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
