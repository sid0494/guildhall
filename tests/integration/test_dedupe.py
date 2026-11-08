"""The 'a job takes effect once' guarantee, against a real Postgres."""

import asyncio
import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.platform.dedupe import claim, processed_jobs


def new_key() -> str:
    return f"test:job:{uuid.uuid4()}"


async def test_first_claim_succeeds(connection: AsyncConnection) -> None:
    assert await claim(connection, new_key())


async def test_second_claim_of_the_same_key_fails(connection: AsyncConnection) -> None:
    key = new_key()
    await claim(connection, key)
    assert not await claim(connection, key)


async def test_different_keys_do_not_conflict(connection: AsyncConnection) -> None:
    assert await claim(connection, new_key())
    assert await claim(connection, new_key())


async def test_claim_rolled_back_with_its_transaction_can_be_made_again(
    engine: AsyncEngine,
) -> None:
    key = new_key()
    async with engine.connect() as first:
        await first.begin()
        assert await claim(first, key)
        await first.rollback()
    try:
        async with engine.begin() as second:
            assert await claim(second, key)
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(
                delete(processed_jobs).where(processed_jobs.c.dedupe_key == key)
            )


async def test_concurrent_claims_wait_and_only_the_committed_one_wins(
    engine: AsyncEngine,
) -> None:
    """Two workers holding the same job: the second blocks, then loses."""
    key = new_key()
    try:
        async with engine.connect() as worker_a, engine.connect() as worker_b:
            await worker_a.begin()
            await worker_b.begin()
            assert await claim(worker_a, key)

            claim_b = asyncio.create_task(claim(worker_b, key))
            await asyncio.sleep(0.2)
            assert not claim_b.done(), "worker B should wait for worker A's commit"

            await worker_a.commit()
            assert not await claim_b
            await worker_b.rollback()
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(
                delete(processed_jobs).where(processed_jobs.c.dedupe_key == key)
            )
