"""The job runner: one test per row of the lifecycle and routing table (ADR 0016).

Runs against real Postgres, with a fake queue that records what the runner
told it to do.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import (
    Context,
    ContractModel,
    Job,
    JobSpec,
    JobType,
    Outcome,
    RateLimitPolicy,
    SourcePlugin,
)
from guildhall.platform.database import DatabaseSettings, create_engine
from guildhall.platform.dedupe import processed_jobs
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.outbox import outbox_events, outbox_jobs
from guildhall.platform.queue import ReceivedMessage
from guildhall.platform.runner import JobRunner, RunnerSettings, RunResult, Status
from guildhall.plugins.synthetic import SyntheticPlugin, records

type LogLines = Callable[[], list[dict[str, Any]]]


@dataclass
class FakeQueue:
    sent: list[tuple[str, int]] = field(default_factory=list)
    deleted: list[ReceivedMessage] = field(default_factory=list)
    retried: list[tuple[ReceivedMessage, int]] = field(default_factory=list)
    dead: list[tuple[ReceivedMessage, str]] = field(default_factory=list)

    async def send(self, body: str, delay_seconds: int = 0) -> None:
        self.sent.append((body, delay_seconds))

    async def delete(self, message: ReceivedMessage) -> None:
        self.deleted.append(message)

    async def retry_later(self, message: ReceivedMessage, delay_seconds: int) -> None:
        self.retried.append((message, delay_seconds))

    async def dead_letter(self, message: ReceivedMessage, reason: str) -> None:
        self.dead.append((message, reason))


class Ping(ContractModel):
    n: int


class Misbehaving(SourcePlugin):
    """Returns outcomes that break the contract."""

    name = "misbehaving"
    rate_limit = RateLimitPolicy(per_minute=1, per_day=1)

    def job_types(self) -> list[JobType[Any]]:
        return [
            JobType("foreign_key", Ping, self.foreign_key),
            JobType("long_delay", Ping, self.long_delay),
            JobType("foreign_write", Ping, self.foreign_write),
            JobType("foreign_read", Ping, self.foreign_read),
        ]

    async def foreign_key(self, job: Job[Ping], ctx: Context) -> Outcome:
        return Outcome(
            jobs=(
                JobSpec(type="foreign_key", payload=job.payload, dedupe_key="other:1"),
            )
        )

    async def long_delay(self, job: Job[Ping], ctx: Context) -> Outcome:
        return Outcome(retry_in=timedelta(hours=1))

    async def foreign_write(self, job: Job[Ping], ctx: Context) -> Outcome:
        return Outcome(writes=(insert(records).values(work_id="stolen", attempt=1),))

    async def foreign_read(self, job: Job[Ping], ctx: Context) -> Outcome:
        await ctx.db.fetch(select(records))
        return Outcome()


@pytest.fixture
def run_id() -> str:
    return uuid.uuid4().hex[:12]


@pytest.fixture
async def cleanup(engine: AsyncEngine, run_id: str) -> AsyncIterator[None]:
    yield
    async with engine.begin() as connection:
        for table in (processed_jobs, outbox_events, outbox_jobs):
            await connection.execute(
                delete(table).where(table.c.dedupe_key.contains(run_id))
            )
        await connection.execute(
            delete(records).where(records.c.work_id.contains(run_id))
        )


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def runner(engine: AsyncEngine, queue: FakeQueue, cleanup: None) -> JobRunner:
    plugins = {"synthetic": SyntheticPlugin(), "misbehaving": Misbehaving()}
    return JobRunner(plugins, engine, queue, RunnerSettings(job_timeout_seconds=1))


def message(body: str, receive_count: int = 1) -> ReceivedMessage:
    return ReceivedMessage(
        body=body, receipt_handle="handle", receive_count=receive_count
    )


def synthetic(run_id: str, attempt: int = 1, **payload: Any) -> str:
    payload.setdefault("work_id", run_id)
    return JobEnvelope(
        plugin="synthetic",
        type="work",
        dedupe_key=f"synthetic:work:{payload['work_id']}",
        attempt=attempt,
        payload=payload,
    ).model_dump_json()


async def count(engine: AsyncEngine, table: Any, run_id: str) -> int:
    async with engine.connect() as connection:
        total = await connection.scalar(
            select(func.count())
            .select_from(table)
            .where(table.c.dedupe_key.contains(run_id))
        )
    return int(total or 0)


async def test_finished_job_commits_its_key_and_outboxes_then_deletes(
    runner: JobRunner, queue: FakeQueue, engine: AsyncEngine, run_id: str
) -> None:
    received = message(synthetic(run_id, fan_out=2, emit_events=3))
    assert await runner.run(received) == RunResult(Status.DONE)
    assert queue.deleted == [received]
    assert await count(engine, processed_jobs, run_id) == 1
    assert await count(engine, outbox_jobs, run_id) == 2
    assert await count(engine, outbox_events, run_id) == 3


async def test_second_delivery_is_a_duplicate_and_changes_nothing(
    runner: JobRunner, queue: FakeQueue, engine: AsyncEngine, run_id: str
) -> None:
    body = synthetic(run_id, fan_out=1, emit_events=1)
    await runner.run(message(body))
    assert await runner.run(message(body, receive_count=2)) == RunResult(
        Status.DUPLICATE
    )
    assert len(queue.deleted) == 2
    assert await count(engine, outbox_jobs, run_id) == 1
    assert await count(engine, outbox_events, run_id) == 1


async def test_not_ready_job_is_resent_with_a_delay_and_the_next_attempt(
    runner: JobRunner, queue: FakeQueue, engine: AsyncEngine, run_id: str
) -> None:
    received = message(synthetic(run_id, not_ready_for=1))
    assert await runner.run(received) == RunResult(Status.NOT_READY)
    ((body, delay),) = queue.sent
    assert JobEnvelope.model_validate_json(body).attempt == 2
    assert delay == 1
    assert queue.deleted == [received]
    assert await count(engine, processed_jobs, run_id) == 0


async def test_attempt_includes_sqs_redeliveries(
    runner: JobRunner, run_id: str
) -> None:
    body = synthetic(run_id, attempt=1, not_ready_for=2)
    assert await runner.run(message(body, receive_count=3)) == RunResult(Status.DONE)


async def test_retryable_error_backs_off_without_deleting(
    runner: JobRunner, queue: FakeQueue, run_id: str
) -> None:
    received = message(synthetic(run_id, fail="retryable"), receive_count=2)
    result = await runner.run(received)
    assert result.status is Status.RETRYING
    assert result.reason == f"RetryableError: synthetic retryable failure for {run_id}"
    ((retried, delay),) = queue.retried
    assert retried == received
    assert 20 <= delay <= 40
    assert queue.deleted == []


async def test_unexpected_exception_is_retried_like_a_retryable_one(
    runner: JobRunner, queue: FakeQueue, run_id: str
) -> None:
    result = await runner.run(message(synthetic(run_id, fail="crash")))
    assert result.status is Status.RETRYING
    assert result.reason is not None
    assert result.reason.startswith("RuntimeError: ")


async def test_handler_that_runs_too_long_is_retried(
    runner: JobRunner, queue: FakeQueue, run_id: str
) -> None:
    result = await runner.run(message(synthetic(run_id, sleep_ms=2_000)))
    assert result == RunResult(Status.RETRYING, "timed out after 1.0 s")


async def test_permanent_error_is_dead_lettered_with_its_reason(
    runner: JobRunner, queue: FakeQueue, engine: AsyncEngine, run_id: str
) -> None:
    received = message(synthetic(run_id, fail="permanent"))
    result = await runner.run(received)
    reason = f"permanent error: synthetic permanent failure for {run_id}"
    assert result == RunResult(Status.DEAD_LETTERED, reason)
    assert queue.dead == [(received, reason)]
    assert await count(engine, processed_jobs, run_id) == 0


@pytest.mark.parametrize(
    ("body", "reason_start"),
    [
        ("not json", "invalid envelope: "),
        ('{"plugin": "synthetic"}', "invalid envelope: "),
        (
            '{"plugin": "gank_report", "type": "work",'
            ' "dedupe_key": "g:1", "payload": {}}',
            "plug-in 'gank_report' is not enabled",
        ),
        (
            '{"plugin": "synthetic", "type": "nope",'
            ' "dedupe_key": "s:1", "payload": {}}',
            "unknown job type 'nope'",
        ),
        (
            '{"plugin": "synthetic", "type": "work", "dedupe_key": "s:1",'
            ' "payload": {"work_id": "x", "fan_out": "many"}}',
            "invalid payload: fan_out: ",
        ),
    ],
    ids=["not-json", "incomplete", "disabled-plugin", "unknown-type", "bad-payload"],
)
async def test_messages_that_can_never_run_are_dead_lettered_at_once(
    runner: JobRunner, queue: FakeQueue, body: str, reason_start: str
) -> None:
    result = await runner.run(message(body))
    assert result.status is Status.DEAD_LETTERED
    assert result.reason is not None
    assert result.reason.startswith(reason_start)
    assert len(queue.dead) == 1


@pytest.mark.parametrize(
    ("job_type", "problem"),
    [("foreign_key", "must start with 'misbehaving:'"), ("long_delay", "retry delay")],
)
async def test_contract_violations_are_dead_lettered(
    runner: JobRunner, queue: FakeQueue, run_id: str, job_type: str, problem: str
) -> None:
    body = JobEnvelope(
        plugin="misbehaving",
        type=job_type,
        dedupe_key=f"misbehaving:{run_id}",
        payload={"n": 1},
    ).model_dump_json()
    result = await runner.run(message(body))
    assert result.status is Status.DEAD_LETTERED
    assert result.reason is not None
    assert result.reason.startswith("contract violation: ")
    assert problem in result.reason


async def test_database_outage_is_retried(queue: FakeQueue, run_id: str) -> None:
    unreachable = create_engine(DatabaseSettings(port=1))
    runner = JobRunner(
        {"synthetic": SyntheticPlugin()}, unreachable, queue, RunnerSettings()
    )
    result = await runner.run(message(synthetic(run_id)))
    await unreachable.dispose()
    assert result.status is Status.RETRYING
    assert result.reason == "database error: OperationalError"
    assert queue.deleted == []


def runner_lines(log_lines: LogLines) -> list[dict[str, Any]]:
    return [
        line for line in log_lines() if line["logger"] == "guildhall.platform.runner"
    ]


async def test_settled_job_is_logged_once_with_its_job_fields(
    runner: JobRunner, run_id: str, log_lines: LogLines
) -> None:
    await runner.run(message(synthetic(run_id), receive_count=2))
    (line,) = runner_lines(log_lines)
    assert (
        line.items()
        >= {
            "event": "job settled",
            "level": "info",
            "status": "done",
            "plugin": "synthetic",
            "job_type": "work",
            "dedupe_key": f"synthetic:work:{run_id}",
            "attempt": 2,
        }.items()
    )


async def test_handler_failure_is_logged_with_its_traceback(
    runner: JobRunner, run_id: str, log_lines: LogLines
) -> None:
    await runner.run(message(synthetic(run_id, fail="crash")))
    raised, settled = runner_lines(log_lines)
    assert raised["event"] == "handler raised"
    assert "RuntimeError" in raised["exception"]
    assert raised["dedupe_key"] == f"synthetic:work:{run_id}"
    assert (settled["level"], settled["status"]) == ("warning", "retrying")


async def test_unreadable_message_is_logged_as_an_error_without_job_fields(
    runner: JobRunner, log_lines: LogLines
) -> None:
    await runner.run(message("not json"))
    (line,) = runner_lines(log_lines)
    assert (line["level"], line["status"]) == ("error", "dead_lettered")
    assert line["reason"].startswith("invalid envelope: ")
    assert "dedupe_key" not in line


async def recorded_attempts(engine: AsyncEngine, run_id: str) -> list[int]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(records.c.attempt).where(records.c.work_id.contains(run_id))
        )
    return [row.attempt for row in rows]


async def test_plugin_writes_commit_with_the_jobs_claim(
    runner: JobRunner, engine: AsyncEngine, run_id: str
) -> None:
    received = message(synthetic(run_id, record=True), receive_count=2)
    assert await runner.run(received) == RunResult(Status.DONE)
    assert await recorded_attempts(engine, run_id) == [2]
    assert await count(engine, processed_jobs, run_id) == 1


async def test_a_duplicate_delivery_does_not_write_again(
    runner: JobRunner, engine: AsyncEngine, run_id: str
) -> None:
    body = synthetic(run_id, record=True)
    await runner.run(message(body))
    assert await runner.run(message(body, receive_count=2)) == RunResult(
        Status.DUPLICATE
    )
    assert await recorded_attempts(engine, run_id) == [1]


async def test_a_failing_write_rolls_back_the_claim_and_outboxes(
    runner: JobRunner, queue: FakeQueue, engine: AsyncEngine, run_id: str
) -> None:
    async with engine.begin() as connection:  # the row the job will try to insert
        await connection.execute(insert(records).values(work_id=run_id, attempt=9))
    body = synthetic(run_id, record=True, fan_out=1, emit_events=1)
    result = await runner.run(message(body))
    assert result == RunResult(Status.RETRYING, "database error: IntegrityError")
    assert queue.deleted == []
    for table in (processed_jobs, outbox_jobs, outbox_events):
        assert await count(engine, table, run_id) == 0


@pytest.mark.parametrize(
    ("job_type", "reason"),
    [
        (
            "foreign_write",
            "contract violation: write touches table 'synthetic_records',"
            " which 'misbehaving' does not own",
        ),
        (
            "foreign_read",
            "contract violation: read touches table 'synthetic_records',"
            " which 'misbehaving' does not own",
        ),
    ],
)
async def test_touching_another_plugins_table_is_dead_lettered(
    runner: JobRunner, queue: FakeQueue, run_id: str, job_type: str, reason: str
) -> None:
    body = JobEnvelope(
        plugin="misbehaving",
        type=job_type,
        dedupe_key=f"misbehaving:{run_id}",
        payload={"n": 1},
    ).model_dump_json()
    assert await runner.run(message(body)) == RunResult(Status.DEAD_LETTERED, reason)
    assert len(queue.dead) == 1
