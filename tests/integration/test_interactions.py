"""The api's interaction log and its pruner, against real Postgres."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.platform.database import DatabaseSettings, create_engine
from guildhall.platform.interactions import (
    InteractionPruner,
    PostgresInteractionLog,
    interactions_seen,
)


@pytest.fixture
async def ids(engine: AsyncEngine) -> AsyncIterator[list[str]]:
    """Interaction IDs unique to the test; their rows are removed afterwards."""
    made: list[str] = []
    yield made
    async with engine.begin() as connection:
        await connection.execute(
            delete(interactions_seen).where(
                interactions_seen.c.interaction_id.in_(made)
            )
        )


def new_id(ids: list[str]) -> str:
    ids.append(f"test-{uuid.uuid4().hex}")
    return ids[-1]


async def test_an_interaction_is_new_once(engine: AsyncEngine, ids: list[str]) -> None:
    log = PostgresInteractionLog(engine)
    first, other = new_id(ids), new_id(ids)
    assert await log.first_time(first)
    assert not await log.first_time(first)
    assert await log.first_time(other)


async def test_a_retry_racing_the_original_is_new_only_once(
    engine: AsyncEngine, ids: list[str]
) -> None:
    log = PostgresInteractionLog(engine)
    interaction_id = new_id(ids)
    results = await asyncio.gather(*(log.first_time(interaction_id) for _ in range(5)))
    assert sorted(results) == [False] * 4 + [True]


async def test_ready_while_postgres_answers(engine: AsyncEngine) -> None:
    assert await PostgresInteractionLog(engine).is_ready()


async def test_not_ready_when_postgres_cannot_be_reached(
    test_database_settings: DatabaseSettings,
) -> None:
    # `model_copy` does not check names: `port` must be the real field name.
    nowhere = test_database_settings.model_copy(update={"port": 1})
    assert nowhere.port == 1
    engine = create_engine(nowhere)
    try:
        assert not await PostgresInteractionLog(engine).is_ready()
    finally:
        await engine.dispose()


async def test_the_pruner_removes_only_ids_older_than_a_week(
    engine: AsyncEngine, ids: list[str]
) -> None:
    old, recent = new_id(ids), new_id(ids)
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.execute(
            insert(interactions_seen),
            [
                {"interaction_id": old, "received_at": now - timedelta(days=8)},
                {
                    "interaction_id": recent,
                    "received_at": now - timedelta(days=6),
                },
            ],
        )
    assert await InteractionPruner(engine).prune() >= 1
    async with engine.connect() as connection:
        left = await connection.scalars(
            select(interactions_seen.c.interaction_id).where(
                interactions_seen.c.interaction_id.in_(ids)
            )
        )
        assert list(left) == [recent]
