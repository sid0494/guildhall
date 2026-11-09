"""The outbox relay against real Postgres, and against moto for delivery.

The relay works on every pending row, so these tests clear `outbox_jobs` in
the test database before each test.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import ContractModel, JobSpec
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.outbox import add_jobs, outbox_jobs
from guildhall.platform.queue import JobQueue
from guildhall.platform.relay import OutboxRelay, RelaySettings


class Ping(ContractModel):
    n: int


@dataclass
class RecordingSender:
    sent: list[tuple[str, int]] = field(default_factory=list)
    gate: asyncio.Event | None = None
    """When set, the first send waits for the gate, holding the batch's locks."""
    started: asyncio.Event = field(default_factory=asyncio.Event)
    fail_after: int | None = None

    async def send(self, body: str, delay_seconds: int = 0) -> None:
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise ConnectionError("queue unavailable")
        self.sent.append((body, delay_seconds))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()


@pytest.fixture
async def empty_outbox(engine: AsyncEngine) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.execute(delete(outbox_jobs))
    yield
    async with engine.begin() as connection:
        await connection.execute(delete(outbox_jobs))


async def queue_jobs(engine: AsyncEngine, count: int, delay: int = 0) -> list[str]:
    specs = [
        JobSpec(
            type="ping",
            payload=Ping(n=n),
            dedupe_key=f"test:ping:{uuid.uuid4()}",
            delay=timedelta(seconds=delay),
        )
        for n in range(count)
    ]
    async with engine.begin() as connection:
        await add_jobs(connection, "test", specs)
    return [spec.dedupe_key for spec in specs]


def keys(sent: list[tuple[str, int]]) -> list[str]:
    return [JobEnvelope.model_validate_json(body).dedupe_key for body, _ in sent]


async def pending(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(outbox_jobs.c.id).where(outbox_jobs.c.sent_at.is_(None))
        )
    return len(rows.all())


def relay(
    engine: AsyncEngine, sender: RecordingSender, batch: int = 100
) -> OutboxRelay:
    return OutboxRelay(engine, sender, RelaySettings(relay_batch_size=batch))


async def test_pending_jobs_are_sent_oldest_first_and_marked_sent(
    engine: AsyncEngine, empty_outbox: None
) -> None:
    queued = await queue_jobs(engine, 3, delay=30)
    sender = RecordingSender()
    assert await relay(engine, sender).relay_once() == 3
    assert keys(sender.sent) == queued
    assert {delay for _, delay in sender.sent} == {30}
    assert await pending(engine) == 0
    assert await relay(engine, sender).relay_once() == 0


async def test_a_pass_sends_at_most_one_batch(
    engine: AsyncEngine, empty_outbox: None
) -> None:
    await queue_jobs(engine, 5)
    assert await relay(engine, RecordingSender(), batch=2).relay_once() == 2
    assert await pending(engine) == 3


async def test_concurrent_relays_take_different_rows(
    engine: AsyncEngine, empty_outbox: None
) -> None:
    await queue_jobs(engine, 4)
    slow = RecordingSender(gate=asyncio.Event())
    fast = RecordingSender()

    first = asyncio.create_task(relay(engine, slow, batch=2).relay_once())
    await slow.started.wait()
    assert await relay(engine, fast, batch=2).relay_once() == 2
    slow.gate.set()  # type: ignore[union-attr]
    assert await first == 2

    assert set(keys(slow.sent)).isdisjoint(keys(fast.sent))
    assert await pending(engine) == 0


async def test_failed_send_leaves_the_whole_batch_pending(
    engine: AsyncEngine, empty_outbox: None
) -> None:
    await queue_jobs(engine, 3)
    with pytest.raises(ConnectionError):
        await relay(engine, RecordingSender(fail_after=1)).relay_once()
    assert await pending(engine) == 3


async def test_prune_deletes_only_rows_sent_before_the_retention_period(
    engine: AsyncEngine, empty_outbox: None
) -> None:
    old, recent, unsent = await queue_jobs(engine, 3)
    async with engine.begin() as connection:
        await connection.execute(
            update(outbox_jobs)
            .where(outbox_jobs.c.dedupe_key == old)
            .values(sent_at=text("now() - interval '8 days'"))
        )
        await connection.execute(
            update(outbox_jobs)
            .where(outbox_jobs.c.dedupe_key == recent)
            .values(sent_at=text("now() - interval '1 day'"))
        )
    assert await relay(engine, RecordingSender()).prune() == 1
    async with engine.connect() as connection:
        left = (await connection.execute(select(outbox_jobs.c.dedupe_key))).scalars()
        assert set(left) == {recent, unsent}


async def test_relayed_job_arrives_on_the_queue_as_an_envelope(
    engine: AsyncEngine, empty_outbox: None, job_queue: JobQueue
) -> None:
    (key,) = await queue_jobs(engine, 1)
    assert await OutboxRelay(engine, job_queue, RelaySettings()).relay_once() == 1
    (message,) = await job_queue.receive(wait_seconds=1)
    envelope = JobEnvelope.model_validate_json(message.body)
    assert (envelope.plugin, envelope.type, envelope.dedupe_key) == (
        "test",
        "ping",
        key,
    )
    assert envelope.payload == {"n": 0}
