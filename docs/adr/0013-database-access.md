# 0013. Database access with SQLAlchemy Core, psycopg 3 and Alembic

Status: Accepted (2026-11-08)

## Context

The platform's guarantees come down to specific statements running in one
transaction: a job's writes, its dedupe key and its outbox rows commit
together or not at all. The worker is asynchronous, while migration tooling
is synchronous. Schema changes must be versioned and reviewable. The spec
already named Alembic for migrations.

## Options considered

- **SQLAlchemy ORM:** least SQL to write, but the unit of work issues queries
  and flushes implicitly, which obscures exactly what ran in a transaction.
- **SQLAlchemy Core:** typed SQL expressions with explicit statements; table
  definitions double as the source for migrations. A large library of which
  only a part is used.
- **psycopg 3 with hand-written SQL:** smallest and fully transparent, but no
  schema-from-code, hand-written migration SQL, and untyped results.
- **asyncpg as the driver:** the fastest, but asynchronous only, so migrations
  need extra glue; this project does not need its speed.

## Decision

- Tables are SQLAlchemy Core `Table` objects on one `MetaData` with a naming
  convention, so every constraint has a predictable name
  (`pk_processed_jobs`).
- The driver is psycopg 3 (`postgresql+psycopg`), used asynchronously by the
  platform and synchronously by Alembic.
- Connection settings are a separate `DatabaseSettings` class reading
  `GUILDHALL_POSTGRES_*`, so migrations need no plug-in configuration. The
  password is a `SecretStr`; host, port, user and database default to the
  compose service.
- Alembic is configured in `pyproject.toml` (no `alembic.ini`). Migrations
  live in `migrations/versions/`, named with a date prefix, drafted with
  `--autogenerate` from the metadata and reviewed by hand. Its template uses
  the project's typing style so generated files pass lint.
- Applied migrations are never edited or deleted; changes go in new ones.

## Consequences

- Every statement a guarantee depends on is visible in code review.
- Schema changes arrive as migration files reviewed with the code that needs
  them.
- Alembic prints nothing on success until logging is configured; `alembic
  current` shows the database's revision.
- Plug-in tables (each plug-in owns its tables) will need a convention for
  their metadata and migrations, decided with the first plug-in that has
  tables.
