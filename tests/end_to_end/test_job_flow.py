"""M1 acceptance: a plug-in's jobs flow end to end; a duplicate is a no-op.

Since M2 the job also archives a raw response, which must be in the bucket.

Runs against the containerised stack: the image, the compose wiring, the
one-shot setup services and the worker, exactly as they would be deployed.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.platform.archive import S3ArchiveStore
from guildhall.platform.aws import open_sqs
from guildhall.platform.dedupe import processed_jobs
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.outbox import outbox_events, outbox_jobs
from guildhall.platform.queue import JobQueue, QueueSettings
from guildhall.plugins.synthetic import SyntheticWork, records

DEADLINE_SECONDS = 30


async def eventually(check: Callable[[], Awaitable[bool]], description: str) -> None:
    """Poll `check` until it is true; fail with `description` at the deadline."""
    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            while not await check():  # noqa: ASYNC110 - polls the stack from outside
                await asyncio.sleep(0.2)
    except TimeoutError:
        pytest.fail(f"not true after {DEADLINE_SECONDS} s: {description}")


async def count(engine: AsyncEngine, table: Any, key_prefix: str) -> int:
    async with engine.connect() as connection:
        total = await connection.scalar(
            select(func.count())
            .select_from(table)
            .where(table.c.dedupe_key.startswith(key_prefix))
        )
    return int(total or 0)


async def unsent(engine: AsyncEngine, key_prefix: str) -> int:
    async with engine.connect() as connection:
        total = await connection.scalar(
            select(func.count())
            .select_from(outbox_jobs)
            .where(outbox_jobs.c.dedupe_key.startswith(key_prefix))
            .where(outbox_jobs.c.sent_at.is_(None))
        )
    return int(total or 0)


async def recorded(engine: AsyncEngine, work_id: str) -> int:
    async with engine.connect() as connection:
        total = await connection.scalar(
            select(func.count())
            .select_from(records)
            .where(records.c.work_id == work_id)
        )
    return int(total or 0)


async def queue_is_empty(settings: QueueSettings) -> bool:
    """No message waiting on the job queue and none being worked on."""
    async with open_sqs(settings) as client:
        url = await client.get_queue_url(QueueName=settings.jobs_queue_name)
        attributes = await client.get_queue_attributes(
            QueueUrl=url["QueueUrl"],
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )
    return set(attributes["Attributes"].values()) == {"0"}


async def test_jobs_flow_end_to_end_and_a_duplicate_delivery_is_a_no_op(
    engine: AsyncEngine,
    queue: JobQueue,
    queue_settings: QueueSettings,
    archive: S3ArchiveStore,
) -> None:
    work_id = f"acceptance-{uuid.uuid4().hex[:12]}"
    jobs = f"synthetic:work:{work_id}"  # the job and its follow-ups, "<id>.<n>"
    events = f"synthetic:work_done:{work_id}:"
    body = JobEnvelope(
        plugin="synthetic",
        type="work",
        dedupe_key=jobs,
        attempt=1,
        payload={
            "work_id": work_id,
            "fan_out": 3,
            "emit_events": 2,
            "record": True,
            "archive": True,
        },
    ).model_dump_json()

    await queue.send(body)

    async def all_four_processed() -> bool:
        return await count(engine, processed_jobs, jobs) == 4

    # The job, then its 3 follow-ups: written to the outbox, sent by the relay,
    # received and run by the worker.
    await eventually(all_four_processed, "the job and its 3 follow-ups are processed")
    assert await count(engine, outbox_jobs, jobs) == 3
    assert await unsent(engine, jobs) == 0
    assert await count(engine, outbox_events, events) == 2
    assert await recorded(engine, work_id) == 1  # the plug-in's own row
    archived = await archive.get(f"raw/synthetic/work/{work_id}.json")
    assert archived is not None  # uploaded before the job committed
    assert SyntheticWork.model_validate_json(archived).work_id == work_id

    await queue.send(body)  # the same message again, as SQS may deliver it

    await eventually(
        lambda: queue_is_empty(queue_settings), "the duplicate is consumed"
    )
    assert await count(engine, processed_jobs, jobs) == 4
    assert await count(engine, outbox_jobs, jobs) == 3
    assert await count(engine, outbox_events, events) == 2
    assert await recorded(engine, work_id) == 1
