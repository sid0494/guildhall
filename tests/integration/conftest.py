"""Fixtures for tests that need the compose services (docs/adr/0015).

Every test under tests/integration is marked `integration`: pre-commit skips
them and CI runs them in their own job, after `docker compose up`. Tests use a
separate `guildhall_test` database on the same server, so they never touch
development data.
"""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import sqlalchemy
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.platform.archive import ArchiveSettings, S3ArchiveStore, open_archive
from guildhall.platform.database import DatabaseSettings, create_engine
from guildhall.platform.dev_aws import ensure_bucket, ensure_queues
from guildhall.platform.queue import JobQueue, QueueSettings, open_job_queue

REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(item.path).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.integration)


TEST_DATABASE = "guildhall_test"


@pytest.fixture(scope="session")
def test_database_settings() -> DatabaseSettings:
    """Create the test database if needed and bring its schema up to date."""
    server = DatabaseSettings()
    admin = sqlalchemy.create_engine(server.url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.scalar(
            sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": TEST_DATABASE},
        )
        if not exists:
            connection.execute(sqlalchemy.text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    admin.dispose()

    settings = server.model_copy(update={"db": TEST_DATABASE})
    config = Config(toml_file=str(REPO_ROOT / "pyproject.toml"))
    config.attributes["url"] = settings.url()
    command.upgrade(config, "head")
    return settings


@pytest.fixture(scope="session")
async def engine(
    test_database_settings: DatabaseSettings,
) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(test_database_settings)
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection inside a transaction that is rolled back after the test."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()


@pytest.fixture
def queue_settings() -> QueueSettings:
    """Queue settings with names unique to this test, so tests never share queues."""
    suffix = uuid.uuid4().hex[:12]
    return QueueSettings(
        jobs_queue_name=f"test-jobs-{suffix}",
        dead_letter_queue_name=f"test-jobs-dlq-{suffix}",
        max_receive_count=2,
    )


@pytest.fixture
async def job_queue(queue_settings: QueueSettings) -> AsyncIterator[JobQueue]:
    await ensure_queues(queue_settings)
    async with open_job_queue(queue_settings) as queue:
        yield queue


@pytest.fixture
def archive_settings() -> ArchiveSettings:
    """A bucket name unique to this test, so tests never share archived objects."""
    return ArchiveSettings(archive_bucket=f"test-raw-{uuid.uuid4().hex[:12]}")


@pytest.fixture
async def archive(archive_settings: ArchiveSettings) -> AsyncIterator[S3ArchiveStore]:
    await ensure_bucket(archive_settings)
    async with open_archive(archive_settings) as archive:
        yield archive
