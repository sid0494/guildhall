# 0015. Integration tests run against the compose services

Status: Accepted (2026-11-08)

## Context

Some guarantees can only be shown against a real Postgres: unique
constraints, transactions and row locking. Tests need a database locally and
in CI, ideally the same one, and committing should not require Docker to be
running.

## Options considered

- **SQLite in place of Postgres:** no setup, but a different database with
  different locking and constraint behaviour, so passing tests prove little.
- **testcontainers:** each test session starts its own container, so `pytest`
  needs no preparation; but image pins are duplicated outside `compose.yaml`,
  it adds a dependency, and every run pays the container start-up.
- **The compose services, locally and in CI:** one definition of the services
  with pinned images, used everywhere.

## Decision

- Tests that need the compose services live in `tests/integration/`; a
  conftest hook marks every test there `integration`.
- A session fixture runs `alembic upgrade head` before the first integration
  test, so migrations are exercised on every run. Each test gets a connection
  in a transaction that is rolled back; tests that must commit use unique keys
  and clean up after themselves.
- The pre-commit hook runs `pytest -m "not integration"`. CI runs a separate
  "Integration tests" job that starts the compose services with a random,
  masked throwaway password and runs `pytest -m integration`.
- pytest-asyncio runs async tests and fixtures in one event loop per session.

## Consequences

- Committing needs no Docker; integration failures surface in CI, or locally
  with `docker compose up -d --wait` and `pytest -m integration`.
- This is a deliberate gap in "pre-commit mirrors CI": the integration job is
  CI-only.
- The "Integration tests" job is added to the `main` ruleset's required
  checks.
