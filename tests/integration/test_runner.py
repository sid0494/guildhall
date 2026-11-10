"""The job runner: one test per row of the lifecycle and routing table (ADR 0016).

Runs against real Postgres, with a fake queue that records what the runner
told it to do.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import (
    Accepted,
    AccountLink,
    Archive,
    Command,
    CommandOutcome,
    Context,
    ContractModel,
    Invocation,
    Job,
    JobSpec,
    JobType,
    LinkedAccount,
    LinkRequest,
    Message,
    Outcome,
    PermanentError,
    RateLimitPolicy,
    Rejected,
    RetryableError,
    SourcePlugin,
)
from guildhall.platform.accounts import linked_accounts
from guildhall.platform.archive import ArchiveError
from guildhall.platform.command_jobs import CommandJob, command_spec
from guildhall.platform.database import DatabaseSettings, create_engine
from guildhall.platform.dedupe import processed_jobs
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.http import HttpSettings, UpstreamHttp, new_client
from guildhall.platform.outbox import outbox_events, outbox_jobs
from guildhall.platform.queue import ReceivedMessage
from guildhall.platform.replies import outbox_replies
from guildhall.platform.runner import (
    COMMAND_FAILED,
    JobRunner,
    RunnerSettings,
    RunResult,
    Status,
)
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


@dataclass
class FakeArchive:
    """Records uploads; can fail them, slow them, or watch the claim while they run."""

    objects: dict[str, bytes] = field(default_factory=dict)
    fail: bool = False
    delay_seconds: float = 0
    engine: AsyncEngine | None = None
    watched_key: str = ""
    claimed_during_upload: list[bool] = field(default_factory=list)

    async def put(self, key: str, body: bytes) -> None:
        await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise ArchiveError("upload failed: InternalError")
        if self.engine is not None:
            async with self.engine.connect() as connection:
                claim = await connection.scalar(
                    select(processed_jobs.c.dedupe_key).where(
                        processed_jobs.c.dedupe_key == self.watched_key
                    )
                )
            self.claimed_during_upload.append(claim is not None)
        self.objects[key] = body

    async def get(self, key: str) -> bytes | None:
        return self.objects.get(key)


@dataclass
class OpenLimiter:
    """A budget that never runs out; records cooldowns."""

    cooldowns: list[tuple[str, float]] = field(default_factory=list)

    async def acquire(self, plugin: str, policy: RateLimitPolicy) -> float:
        return 0

    async def cool_down(self, plugin: str, seconds: float) -> None:
        self.cooldowns.append((plugin, seconds))


def fake_upstream(request: httpx.Request) -> httpx.Response:
    """https://upstream.test/<path>: ok, busy (429, 2 min), swamped (429, 1 h), down."""
    match request.url.path:
        case "/ok":
            return httpx.Response(200, content=b'{"from": "upstream"}')
        case "/busy":
            return httpx.Response(429, headers={"Retry-After": "120"})
        case "/swamped":
            return httpx.Response(429, headers={"Retry-After": "3600"})
        case _:
            return httpx.Response(503)


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
            JobType("archive_escape", Ping, self.archive_escape),
            JobType("archive_read_escape", Ping, self.archive_read_escape),
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

    async def archive_escape(self, job: Job[Ping], ctx: Context) -> Outcome:
        return Outcome(archives=(Archive(kind="work", id="../synthetic", body=b"{}"),))

    async def archive_read_escape(self, job: Job[Ping], ctx: Context) -> Outcome:
        await ctx.archive.get("work", "../../synthetic/work/1")
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
def archive() -> FakeArchive:
    return FakeArchive()


@pytest.fixture
def limiter() -> OpenLimiter:
    return OpenLimiter()


@pytest.fixture
async def http(limiter: OpenLimiter) -> AsyncIterator[UpstreamHttp]:
    settings = HttpSettings()
    transport = httpx.MockTransport(fake_upstream)
    async with new_client(settings, transport) as client:
        yield UpstreamHttp(client, limiter, settings)


@pytest.fixture
def runner(
    engine: AsyncEngine,
    queue: FakeQueue,
    archive: FakeArchive,
    http: UpstreamHttp,
    cleanup: None,
) -> JobRunner:
    plugins = {"synthetic": SyntheticPlugin(), "misbehaving": Misbehaving()}
    settings = RunnerSettings(job_timeout_seconds=1, archive_timeout_seconds=0.5)
    return JobRunner(plugins, engine, queue, archive, http, settings)


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


async def test_database_outage_is_retried(
    queue: FakeQueue, http: UpstreamHttp, run_id: str
) -> None:
    unreachable = create_engine(DatabaseSettings(port=1))
    runner = JobRunner(
        {"synthetic": SyntheticPlugin()},
        unreachable,
        queue,
        FakeArchive(),
        http,
        RunnerSettings(),
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


@pytest.mark.parametrize(
    ("job_type", "reason"),
    [
        (
            "archive_escape",
            "contract violation: archive id '../synthetic' must match",
        ),
        (
            "archive_read_escape",
            "contract violation: archive read archive id '../../synthetic/work/1'"
            " must match",
        ),
    ],
)
async def test_reaching_outside_the_plugins_archive_is_dead_lettered(
    runner: JobRunner,
    queue: FakeQueue,
    archive: FakeArchive,
    run_id: str,
    job_type: str,
    reason: str,
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
    assert result.reason.startswith(reason)
    assert archive.objects == {}


async def test_declared_archives_are_uploaded_under_the_plugins_folder(
    runner: JobRunner, archive: FakeArchive, run_id: str
) -> None:
    assert await runner.run(message(synthetic(run_id, archive=True))) == RunResult(
        Status.DONE
    )
    (key,) = archive.objects
    assert key == f"raw/synthetic/work/{run_id}.json"
    assert b'"archive":true' in archive.objects[key]


async def test_archives_are_uploaded_before_the_job_commits(
    runner: JobRunner, archive: FakeArchive, engine: AsyncEngine, run_id: str
) -> None:
    archive.engine, archive.watched_key = engine, f"synthetic:work:{run_id}"
    await runner.run(message(synthetic(run_id, archive=True)))
    assert archive.claimed_during_upload == [False]
    assert await count(engine, processed_jobs, run_id) == 1


async def test_a_failed_upload_commits_nothing_and_retries(
    runner: JobRunner,
    queue: FakeQueue,
    archive: FakeArchive,
    engine: AsyncEngine,
    run_id: str,
) -> None:
    archive.fail = True
    body = synthetic(run_id, archive=True, record=True, fan_out=1, emit_events=1)
    result = await runner.run(message(body))
    assert result == RunResult(
        Status.RETRYING, "archive error: upload failed: InternalError"
    )
    assert queue.deleted == []
    assert await recorded_attempts(engine, run_id) == []
    for table in (processed_jobs, outbox_jobs, outbox_events):
        assert await count(engine, table, run_id) == 0


async def test_a_slow_upload_times_out_and_retries(
    runner: JobRunner, queue: FakeQueue, archive: FakeArchive, run_id: str
) -> None:
    archive.delay_seconds = 2
    result = await runner.run(message(synthetic(run_id, archive=True)))
    assert result == RunResult(Status.RETRYING, "archive upload timed out after 0.5 s")
    assert queue.deleted == []


async def test_a_fetched_response_can_be_archived(
    runner: JobRunner, archive: FakeArchive, run_id: str
) -> None:
    body = synthetic(run_id, fetch="https://upstream.test/ok", archive=True)
    assert await runner.run(message(body)) == RunResult(Status.DONE)
    assert archive.objects == {
        f"raw/synthetic/work/{run_id}.json": b'{"from": "upstream"}'
    }


async def test_a_throttled_job_is_deferred_keeping_its_attempt(
    runner: JobRunner,
    queue: FakeQueue,
    limiter: OpenLimiter,
    engine: AsyncEngine,
    run_id: str,
) -> None:
    body = synthetic(run_id, fetch="https://upstream.test/busy", record=True)
    received = message(body, receive_count=4)  # one receive short of the DLQ
    result = await runner.run(received)

    assert result.status is Status.DEFERRED
    ((sent, delay),) = queue.sent
    assert result.reason == (
        f"GET upstream.test/busy answered 429 (Too Many Requests) (again in {delay} s)"
    )
    assert JobEnvelope.model_validate_json(sent).attempt == 4  # same, not 5
    assert 120 <= delay <= 180
    assert queue.deleted == [received]  # a new message: SQS counts from 1 again
    assert (queue.retried, queue.dead) == ([], [])
    assert limiter.cooldowns == [("synthetic", 120)]
    assert await count(engine, processed_jobs, run_id) == 0
    assert await recorded_attempts(engine, run_id) == []


async def test_a_deferral_never_exceeds_the_queues_delay_limit(
    runner: JobRunner, queue: FakeQueue, run_id: str
) -> None:
    await runner.run(message(synthetic(run_id, fetch="https://upstream.test/swamped")))
    ((_, delay),) = queue.sent
    assert delay == 900


async def test_an_upstream_server_error_is_an_ordinary_retry(
    runner: JobRunner, queue: FakeQueue, run_id: str
) -> None:
    result = await runner.run(
        message(synthetic(run_id, fetch="https://upstream.test/x"))
    )
    assert result == RunResult(
        Status.RETRYING, "RetryableError: GET upstream.test/x answered 503"
    )
    assert queue.sent == []


# Slash commands run as jobs (docs/adr/0063, 0064).


class WhoOptions(ContractModel):
    pass


class Accounting(SourcePlugin):
    """A plug-in whose command says which account the caller linked."""

    name = "accounting"
    rate_limit = RateLimitPolicy(per_minute=60, per_day=1000)

    def job_types(self) -> list[JobType[Any]]:
        return []

    def commands(self) -> list[Command[Any]]:
        return [
            Command(("who",), "Who am I", WhoOptions, self.who),
            Command(("broken",), "Always fails for good", WhoOptions, self.broken),
            Command(("flaky",), "Always fails, worth a retry", WhoOptions, self.flaky),
        ]

    async def broken(
        self, invocation: Invocation[WhoOptions], ctx: Context
    ) -> CommandOutcome:
        raise PermanentError("this command cannot work")

    async def flaky(
        self, invocation: Invocation[WhoOptions], ctx: Context
    ) -> CommandOutcome:
        raise RetryableError("try again")

    async def who(
        self, invocation: Invocation[WhoOptions], ctx: Context
    ) -> CommandOutcome:
        account = invocation.account
        linked = account.external_id if account else "nobody"
        return CommandOutcome(reply=Message(content=f"linked: {linked}"))


def command(
    run_id: str,
    path: tuple[str, ...] = ("synthetic", "echo"),
    options: dict[str, Any] | None = None,
    plugin: str = "synthetic",
    user: str = "user-1",
) -> str:
    job = CommandJob(
        path=path,
        discord_user_id=user,
        options={"text": "hello"} if options is None else options,
        application_id="app-1",
        interaction_token=f"token-{run_id}",
        private=False,
    )
    return JobEnvelope.from_spec(
        plugin, command_spec(plugin, f"interaction-{run_id}", job)
    ).model_dump_json()


@pytest.fixture
async def replies(engine: AsyncEngine, run_id: str) -> AsyncIterator[None]:
    yield
    async with engine.begin() as connection:
        await connection.execute(
            delete(outbox_replies).where(
                outbox_replies.c.interaction_token.contains(run_id)
            )
        )


async def queued_replies(engine: AsyncEngine, run_id: str) -> list[Any]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(outbox_replies).where(
                outbox_replies.c.interaction_token == f"token-{run_id}"
            )
        )
        return list(rows)


async def test_a_command_commits_its_reply_with_its_claim(
    runner: JobRunner, queue: FakeQueue, engine: AsyncEngine, run_id: str, replies: None
) -> None:
    result = await runner.run(message(command(run_id)))
    assert result == RunResult(Status.DONE)
    [reply] = await queued_replies(engine, run_id)
    assert (reply.application_id, reply.message) == ("app-1", {"content": "hello"})
    assert reply.sent_at is None
    assert await count(engine, processed_jobs, run_id) == 1
    assert len(queue.deleted) == 1


async def test_a_command_delivered_twice_replies_once(
    runner: JobRunner, engine: AsyncEngine, run_id: str, replies: None
) -> None:
    body = command(run_id)
    assert await runner.run(message(body)) == RunResult(Status.DONE)
    assert await runner.run(message(body)) == RunResult(Status.DUPLICATE)
    assert len(await queued_replies(engine, run_id)) == 1


async def test_a_command_with_invalid_options_is_dead_lettered(
    runner: JobRunner, queue: FakeQueue, engine: AsyncEngine, run_id: str, replies: None
) -> None:
    result = await runner.run(message(command(run_id, options={})))
    assert result.status is Status.DEAD_LETTERED
    assert result.reason == "invalid options: text: Field required"
    [reply] = await queued_replies(engine, run_id)
    assert reply.message == {"content": COMMAND_FAILED}


async def test_a_command_the_plugin_no_longer_has_is_dead_lettered(
    runner: JobRunner, run_id: str, replies: None
) -> None:
    result = await runner.run(message(command(run_id, path=("synthetic", "shout"))))
    assert result.status is Status.DEAD_LETTERED
    assert result.reason == "unknown command /synthetic shout for 'synthetic'"


@pytest.fixture
async def linked(engine: AsyncEngine, run_id: str) -> AsyncIterator[str]:
    """A Discord user with a linked account for the accounting plug-in."""
    user = f"user-{run_id}"
    async with engine.begin() as connection:
        await connection.execute(
            insert(linked_accounts).values(
                plugin="accounting", discord_user_id=user, external_id="acct-77"
            )
        )
    yield user
    async with engine.begin() as connection:
        await connection.execute(
            delete(linked_accounts).where(linked_accounts.c.discord_user_id == user)
        )


async def test_a_command_sees_the_callers_linked_account(
    engine: AsyncEngine,
    queue: FakeQueue,
    archive: FakeArchive,
    http: UpstreamHttp,
    cleanup: None,
    replies: None,
    run_id: str,
    linked: str,
) -> None:
    runner = JobRunner(
        {"accounting": Accounting()}, engine, queue, archive, http, RunnerSettings()
    )
    for user, expected in ((linked, "linked: acct-77"), ("stranger", "linked: nobody")):
        body = command(
            f"{run_id}-{user}",
            path=("who",),
            options={},
            plugin="accounting",
            user=user,
        )
        assert await runner.run(message(body)) == RunResult(Status.DONE)
        [reply] = await queued_replies(engine, f"{run_id}-{user}")
        assert reply.message == {"content": expected}


@pytest.fixture
def accounting_runner(
    engine: AsyncEngine,
    queue: FakeQueue,
    archive: FakeArchive,
    http: UpstreamHttp,
    cleanup: None,
    replies: None,
) -> JobRunner:
    settings = RunnerSettings(max_receive_count=3)
    return JobRunner(
        {"accounting": Accounting()}, engine, queue, archive, http, settings
    )


def accounting(run_id: str, name: str) -> str:
    return command(run_id, path=(name,), options={}, plugin="accounting")


async def test_a_command_that_fails_for_good_replies_with_an_error(
    accounting_runner: JobRunner, engine: AsyncEngine, run_id: str
) -> None:
    result = await accounting_runner.run(message(accounting(run_id, "broken")))
    assert result.status is Status.DEAD_LETTERED
    [reply] = await queued_replies(engine, run_id)
    assert reply.message == {"content": COMMAND_FAILED}


async def test_a_failing_command_replies_with_an_error_only_on_its_last_receive(
    accounting_runner: JobRunner, engine: AsyncEngine, run_id: str
) -> None:
    body = accounting(run_id, "flaky")
    for receive_count in (1, 2):
        result = await accounting_runner.run(message(body, receive_count))
        assert result.status is Status.RETRYING
        assert await queued_replies(engine, run_id) == []
    result = await accounting_runner.run(message(body, receive_count=3))
    assert result.status is Status.RETRYING  # SQS dead-letters it after this
    [reply] = await queued_replies(engine, run_id)
    assert reply.message == {"content": COMMAND_FAILED}


# /link (docs/adr/0066).


class Linking(SourcePlugin):
    """Links "game" accounts: digits only; remembers what verify was shown."""

    name = "linking"
    rate_limit = RateLimitPolicy(per_minute=60, per_day=1000)
    seen_previous: ClassVar[list[str | None]] = []

    def job_types(self) -> list[JobType[Any]]:
        return [JobType("backfill", LinkedAccount, self.backfill)]

    async def backfill(self, job: Job[LinkedAccount], ctx: Context) -> Outcome:
        return Outcome()

    def account_link(self) -> AccountLink | None:
        return AccountLink("game", "Your game ID", self.verify, on_link="backfill")

    async def verify(self, request: LinkRequest, ctx: Context) -> Accepted | Rejected:
        previous = request.previous
        self.seen_previous.append(previous.external_id if previous else None)
        if not request.external_id.isdecimal():
            return Rejected(reason="That is not a game ID.")
        return Accepted(external_id=str(int(request.external_id)))


@pytest.fixture
def link_runner(
    engine: AsyncEngine,
    queue: FakeQueue,
    archive: FakeArchive,
    http: UpstreamHttp,
    cleanup: None,
    replies: None,
) -> JobRunner:
    Linking.seen_previous.clear()
    return JobRunner(
        {"linking": Linking()}, engine, queue, archive, http, RunnerSettings()
    )


@pytest.fixture
async def user(engine: AsyncEngine, run_id: str) -> AsyncIterator[str]:
    discord_user = f"user-{run_id}"
    yield discord_user
    async with engine.begin() as connection:
        await connection.execute(
            delete(linked_accounts).where(
                linked_accounts.c.discord_user_id == discord_user
            )
        )


def link(run_id: str, user: str, account: str) -> str:
    return command(
        run_id,
        path=("link", "game"),
        options={"account": account},
        plugin="linking",
        user=user,
    )


async def links_of(engine: AsyncEngine, user: str) -> list[Any]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(linked_accounts).where(linked_accounts.c.discord_user_id == user)
        )
        return list(rows)


async def follow_ups(engine: AsyncEngine, run_id: str) -> list[Any]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(outbox_jobs.c.dedupe_key, outbox_jobs.c.body).where(
                outbox_jobs.c.dedupe_key.contains(
                    f"linking:on_link:interaction-{run_id}"
                )
            )
        )
        return list(rows)


async def test_an_accepted_account_is_linked_followed_up_and_confirmed(
    link_runner: JobRunner, engine: AsyncEngine, run_id: str, user: str
) -> None:
    assert await link_runner.run(message(link(run_id, user, " 0042 "))) == RunResult(
        Status.DONE
    )
    [stored] = await links_of(engine, user)
    assert (stored.plugin, stored.external_id) == ("linking", "42")  # normalised
    [job] = await follow_ups(engine, run_id)
    assert job.body["type"] == "backfill"
    assert job.body["payload"] == {
        "id": stored.id,
        "discord_user_id": user,
        "external_id": "42",
    }
    [reply] = await queued_replies(engine, run_id)
    assert reply.message == {"content": "Linked your game account 42."}
    assert Linking.seen_previous == [None]


async def test_a_rejected_account_changes_nothing_but_says_why(
    link_runner: JobRunner, engine: AsyncEngine, run_id: str, user: str
) -> None:
    assert await link_runner.run(message(link(run_id, user, "abc"))) == RunResult(
        Status.DONE
    )
    assert await links_of(engine, user) == []
    assert await follow_ups(engine, run_id) == []
    [reply] = await queued_replies(engine, run_id)
    assert reply.message == {"content": "That is not a game ID."}


async def test_linking_again_replaces_the_account_and_follows_up_again(
    link_runner: JobRunner, engine: AsyncEngine, run_id: str, user: str
) -> None:
    await link_runner.run(message(link(run_id, user, "42")))
    [first] = await links_of(engine, user)
    again = f"{run_id}-again"
    assert await link_runner.run(message(link(again, user, "77"))) == RunResult(
        Status.DONE
    )
    [second] = await links_of(engine, user)
    assert (second.id, second.external_id) == (first.id, "77")  # same link, new account
    assert second.linked_at >= first.linked_at
    assert len(await follow_ups(engine, again)) == 1
    [reply] = await queued_replies(engine, again)
    assert reply.message == {"content": "Linked your game account 77, replacing 42."}
    assert Linking.seen_previous == [None, "42"]


async def test_a_link_delivered_twice_links_follows_up_and_replies_once(
    link_runner: JobRunner, engine: AsyncEngine, run_id: str, user: str
) -> None:
    body = link(run_id, user, "42")
    assert await link_runner.run(message(body)) == RunResult(Status.DONE)
    assert await link_runner.run(message(body)) == RunResult(Status.DUPLICATE)
    assert len(await links_of(engine, user)) == 1
    assert len(await follow_ups(engine, run_id)) == 1
    assert len(await queued_replies(engine, run_id)) == 1
