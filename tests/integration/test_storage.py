"""`Context.db` against real Postgres: own-table reads only, read-only enforced."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.platform.checks import ContractViolationError
from guildhall.platform.dedupe import processed_jobs
from guildhall.platform.storage import PluginDatabase
from guildhall.plugins.synthetic import SyntheticPlugin, records


@pytest.fixture
async def work_id(engine: AsyncEngine) -> AsyncIterator[str]:
    """A synthetic_records row that exists for the test, removed afterwards."""
    work_id = f"storage-{uuid.uuid4().hex[:12]}"
    async with engine.begin() as connection:
        await connection.execute(insert(records).values(work_id=work_id, attempt=3))
    yield work_id
    async with engine.begin() as connection:
        await connection.execute(delete(records).where(records.c.work_id == work_id))


@pytest.fixture
def db(engine: AsyncEngine) -> PluginDatabase:
    return PluginDatabase(engine, SyntheticPlugin())


async def test_fetch_returns_rows_from_the_plugins_own_table(
    db: PluginDatabase, work_id: str
) -> None:
    rows = await db.fetch(select(records.c.attempt).where(records.c.work_id == work_id))
    assert [row.attempt for row in rows] == [3]


async def test_reading_another_table_is_a_contract_violation(
    db: PluginDatabase,
) -> None:
    with pytest.raises(ContractViolationError, match="does not own"):
        await db.fetch(select(processed_jobs))


async def test_postgres_refuses_writes_the_static_check_cannot_see(
    db: PluginDatabase,
) -> None:
    # nextval() passes the statement check (no foreign table, no raw text) but
    # changes the database; the read-only transaction is the second line.
    with pytest.raises(DBAPIError, match="read-only transaction"):
        await db.fetch(select(func.nextval("outbox_jobs_id_seq")))


async def test_fetch_holds_no_connection_afterwards(
    db: PluginDatabase, engine: AsyncEngine, work_id: str
) -> None:
    before = engine.pool.checkedout()  # type: ignore[attr-defined]
    await db.fetch(select(records))
    assert engine.pool.checkedout() == before  # type: ignore[attr-defined]
