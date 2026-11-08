"""The outboxes record jobs and events in the caller's transaction."""

import uuid
from datetime import timedelta
from typing import Literal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from guildhall.contract import ContractModel, Event, JobSpec
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.outbox import add_events, add_jobs, outbox_events, outbox_jobs


class Ping(ContractModel):
    n: int


class Pinged(Event):
    type: Literal["test.pinged"] = "test.pinged"
    schema_version: Literal[1] = 1
    n: int


def spec(delay: timedelta = timedelta(0)) -> JobSpec:
    return JobSpec(
        type="ping", payload=Ping(n=1), dedupe_key=f"test:{uuid.uuid4()}", delay=delay
    )


async def test_jobs_are_stored_as_pending_envelopes(
    connection: AsyncConnection,
) -> None:
    job = spec(delay=timedelta(seconds=30))
    await add_jobs(connection, "test", [job])
    row = (
        await connection.execute(
            select(outbox_jobs).where(outbox_jobs.c.dedupe_key == job.dedupe_key)
        )
    ).one()
    assert JobEnvelope.model_validate(row.body) == JobEnvelope.from_spec("test", job)
    assert (row.delay_seconds, row.sent_at) == (30, None)


async def test_the_same_job_may_be_queued_twice(connection: AsyncConnection) -> None:
    job = spec()
    await add_jobs(connection, "test", [job, job])
    rows = await connection.execute(
        select(outbox_jobs.c.id).where(outbox_jobs.c.dedupe_key == job.dedupe_key)
    )
    assert len(rows.all()) == 2


async def test_delay_beyond_the_sqs_limit_is_refused(
    connection: AsyncConnection,
) -> None:
    with pytest.raises(IntegrityError, match="delay_seconds_range"):
        await add_jobs(connection, "test", [spec(delay=timedelta(minutes=16))])


async def test_events_are_stored_with_their_full_payload(
    connection: AsyncConnection,
) -> None:
    event = Pinged(dedupe_key=f"test:{uuid.uuid4()}", n=7)
    await add_events(connection, "test", [event])
    row = (
        await connection.execute(
            select(outbox_events).where(outbox_events.c.dedupe_key == event.dedupe_key)
        )
    ).one()
    assert (row.plugin, row.type, row.schema_version) == ("test", "test.pinged", 1)
    assert row.payload["n"] == 7


async def test_an_event_key_is_recorded_once(connection: AsyncConnection) -> None:
    event = Pinged(dedupe_key=f"test:{uuid.uuid4()}", n=1)
    await add_events(connection, "test", [event])
    await add_events(connection, "test", [event])
    rows = await connection.execute(
        select(outbox_events.c.id).where(outbox_events.c.dedupe_key == event.dedupe_key)
    )
    assert len(rows.all()) == 1


async def test_nothing_to_add_is_a_no_op(connection: AsyncConnection) -> None:
    await add_jobs(connection, "test", [])
    await add_events(connection, "test", [])
