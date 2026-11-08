"""Fixtures for tests that need the compose services (docs/adr/0015).

Every test under tests/integration is marked `integration`: pre-commit skips
them and CI runs them in their own job, after `docker compose up`.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.platform.database import DatabaseSettings, create_engine

REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(item.path).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """Bring the database schema up to date once per test session."""
    command.upgrade(Config(toml_file=str(REPO_ROOT / "pyproject.toml")), "head")


@pytest.fixture(scope="session")
async def engine(migrated_database: None) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(DatabaseSettings())
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection inside a transaction that is rolled back after the test."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()
