"""Alembic environment: migrates the platform database from the table metadata."""

from alembic import context
from sqlalchemy import create_engine

import guildhall.platform.dedupe  # noqa: F401  (registers its tables on the metadata)
from guildhall.platform.database import DatabaseSettings, metadata


def run_migrations_offline() -> None:
    """Print the migration SQL instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=DatabaseSettings().url().render_as_string(hide_password=True),
        target_metadata=metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the database in one transaction."""
    engine = create_engine(DatabaseSettings().url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
