"""The `guildhall-worker` command as a real process: start, drain, stop (ADR 0023).

Each test runs the installed command against the compose services, with its
own queues and the test database, and talks to it only through the queue,
signals, its exit code and its JSON log lines.
"""

import asyncio
import json
import os
import signal
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.platform.database import DatabaseSettings
from guildhall.platform.dedupe import processed_jobs
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.queue import JobQueue, QueueSettings, open_sqs

WORKER = Path(sys.executable).with_name("guildhall-worker")


class WorkerProcess:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.lines: list[dict[str, Any]] = []

    async def wait_for(self, event: str) -> dict[str, Any]:
        """Read log lines until one with this event appears."""
        assert self.process.stdout is not None
        async with asyncio.timeout(10):
            while True:
                raw = await self.process.stdout.readline()
                if not raw:
                    raise AssertionError(f"worker exited before logging {event!r}")
                line: dict[str, Any] = json.loads(raw)
                self.lines.append(line)
                if line["event"] == event:
                    return line

    def signal(self, signum: signal.Signals) -> None:
        self.process.send_signal(signum)

    async def exit_code(self) -> int:
        assert self.process.stdout is not None
        async with asyncio.timeout(10):
            async for raw in self.process.stdout:
                self.lines.append(json.loads(raw))
            return await self.process.wait()

    def events(self) -> list[str]:
        return [line["event"] for line in self.lines]


@pytest.fixture
async def worker(
    job_queue: JobQueue,
    queue_settings: QueueSettings,
    test_database_settings: DatabaseSettings,
) -> AsyncIterator[WorkerProcess]:
    """A running worker on this test's queues; killed if a test leaves it running."""
    env = {
        **os.environ,
        "GUILDHALL_PLUGINS": "synthetic",
        "GUILDHALL_POSTGRES_DB": test_database_settings.db,
        "GUILDHALL_JOBS_QUEUE_NAME": queue_settings.jobs_queue_name,
        "GUILDHALL_DEAD_LETTER_QUEUE_NAME": queue_settings.dead_letter_queue_name,
        "GUILDHALL_LOG_FORMAT": "json",
        "GUILDHALL_RECEIVE_WAIT_SECONDS": "1",  # an idle stop waits out one poll
    }
    process = await asyncio.create_subprocess_exec(
        WORKER, env=env, stdout=asyncio.subprocess.PIPE
    )
    running = WorkerProcess(process)
    await running.wait_for("worker started")
    yield running
    if process.returncode is None:
        process.kill()
        await process.wait()


async def send_sleeping_job(queue: JobQueue, sleep_ms: int) -> str:
    work_id = f"process-{uuid.uuid4().hex[:12]}"
    dedupe_key = f"synthetic:work:{work_id}"
    await queue.send(
        JobEnvelope(
            plugin="synthetic",
            type="work",
            dedupe_key=dedupe_key,
            attempt=1,
            payload={"work_id": work_id, "sleep_ms": sleep_ms},
        ).model_dump_json()
    )
    return dedupe_key


async def wait_until_received(settings: QueueSettings) -> None:
    """Wait until the worker has the message: it is in flight, not visible."""
    async with open_sqs(settings) as client, asyncio.timeout(10):
        url = (await client.get_queue_url(QueueName=settings.jobs_queue_name))[
            "QueueUrl"
        ]
        while True:
            attributes = await client.get_queue_attributes(
                QueueUrl=url, AttributeNames=["ApproximateNumberOfMessagesNotVisible"]
            )
            if attributes["Attributes"]["ApproximateNumberOfMessagesNotVisible"] == "1":
                return
            await asyncio.sleep(0.05)


async def test_sigterm_stops_an_idle_worker_cleanly(worker: WorkerProcess) -> None:
    worker.signal(signal.SIGTERM)
    assert await worker.exit_code() == 0
    assert worker.events()[-1] == "worker stopped"


async def test_a_job_running_at_sigterm_finishes_before_the_worker_exits(
    worker: WorkerProcess,
    job_queue: JobQueue,
    queue_settings: QueueSettings,
    engine: AsyncEngine,
) -> None:
    dedupe_key = await send_sleeping_job(job_queue, sleep_ms=1_000)
    await wait_until_received(queue_settings)
    worker.signal(signal.SIGTERM)
    assert await worker.exit_code() == 0
    settled = [line for line in worker.lines if line["event"] == "job settled"]
    assert [(line["dedupe_key"], line["status"]) for line in settled] == [
        (dedupe_key, "done")
    ]
    async with engine.begin() as connection:
        assert await connection.scalar(
            select(processed_jobs.c.dedupe_key).where(
                processed_jobs.c.dedupe_key == dedupe_key
            )
        )
        await connection.execute(
            delete(processed_jobs).where(processed_jobs.c.dedupe_key == dedupe_key)
        )


async def test_a_second_signal_stops_at_once(
    worker: WorkerProcess, job_queue: JobQueue, queue_settings: QueueSettings
) -> None:
    await send_sleeping_job(job_queue, sleep_ms=20_000)
    await wait_until_received(queue_settings)
    worker.signal(signal.SIGTERM)
    await worker.wait_for("draining")
    worker.signal(signal.SIGINT)
    assert await worker.exit_code() == 1
    assert "job settled" not in worker.events()
    assert worker.events()[-1] == "worker forced to stop"
