"""A synthetic plug-in for end-to-end tests, failure drills and load checks.

It has no upstream API of its own: a job calls one only when its payload
names a URL to fetch, which tests and drills point at a fake. Each job's
payload says what the job should do, so new drill scenarios need no new code.
Disabled unless enabled in config.
"""

import asyncio
from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import Column, DateTime, Integer, Table, Text, func, insert

from guildhall.contract import (
    Archive,
    Command,
    CommandOutcome,
    Context,
    ContractModel,
    Event,
    Invocation,
    Job,
    JobSpec,
    JobType,
    Message,
    Outcome,
    PermanentError,
    RateLimitPolicy,
    RetryableError,
    SourcePlugin,
    plugin_metadata,
)

NOT_READY_DELAY = timedelta(seconds=1)

metadata = plugin_metadata()

records = Table(
    "synthetic_records",
    metadata,
    Column("work_id", Text, primary_key=True),
    Column("attempt", Integer, nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
"""One row per finished job that asked to be recorded, written with its outcome."""


class SyntheticWork(ContractModel):
    """What one synthetic job does, in order.

    Waits `sleep_ms`; GETs `fetch` through `ctx.http`, if set; returns a retry
    outcome while `attempt <= not_ready_for`; then fails as `fail` says, or
    finishes with `fan_out` follow-up jobs and `emit_events` events, writing a
    row to `synthetic_records` if `record` is set. With `archive`, it archives
    the fetched body (or, with nothing fetched, its own payload) as kind "work".
    `fail="crash"` raises an untyped exception on purpose, to drill how
    the platform handles a broken plug-in.
    """

    work_id: Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")]
    sleep_ms: Annotated[int, Field(ge=0, le=60_000)] = 0
    fetch: Annotated[str, Field(pattern=r"^https?://")] | None = None
    not_ready_for: Annotated[int, Field(ge=0, le=100)] = 0
    fail: Literal["none", "retryable", "permanent", "crash"] = "none"
    fan_out: Annotated[int, Field(ge=0, le=1_000)] = 0
    emit_events: Annotated[int, Field(ge=0, le=100)] = 0
    record: bool = False
    archive: bool = False


class EchoOptions(ContractModel):
    """Options of `/synthetic echo` and `/synthetic whisper`, drill commands."""

    text: str = Field(min_length=1, max_length=200, description="What to say back")


class WorkDone(Event):
    """Emitted by a finished synthetic job."""

    type: Literal["synthetic.work_done"] = "synthetic.work_done"
    schema_version: Literal[1] = 1
    work_id: str
    index: int


class SyntheticPlugin(SourcePlugin):
    name = "synthetic"
    rate_limit = RateLimitPolicy(per_minute=6_000, per_day=1_000_000)
    tables = metadata

    def job_types(self) -> list[JobType[Any]]:
        return [JobType("work", SyntheticWork, self.work)]

    def event_types(self) -> list[type[Event]]:
        return [WorkDone]

    def commands(self) -> list[Command[Any]]:
        return [
            Command(
                ("synthetic", "echo"), "Say something back", EchoOptions, self.echo
            ),
            Command(
                ("synthetic", "whisper"),
                "Say something back, only to you",
                EchoOptions,
                self.echo,
                private=True,
            ),
        ]

    async def echo(
        self, invocation: Invocation[EchoOptions], ctx: Context
    ) -> CommandOutcome:
        """Replies with the text: exercises commands end to end."""
        return CommandOutcome(reply=Message(content=invocation.options.text))

    async def work(self, job: Job[SyntheticWork], ctx: Context) -> Outcome:
        work = job.payload
        if work.sleep_ms:
            await asyncio.sleep(work.sleep_ms / 1000)
        fetched = (await ctx.http.get(work.fetch)).body if work.fetch else None
        if job.attempt <= work.not_ready_for:
            return Outcome(retry_in=NOT_READY_DELAY)

        match work.fail:
            case "retryable":
                raise RetryableError(f"synthetic retryable failure for {work.work_id}")
            case "permanent":
                raise PermanentError(f"synthetic permanent failure for {work.work_id}")
            case "crash":
                raise RuntimeError(f"synthetic crash for {work.work_id}")
            case "none":
                pass

        children = tuple(
            JobSpec(
                type="work",
                payload=SyntheticWork(work_id=f"{work.work_id}.{index}"),
                dedupe_key=f"synthetic:work:{work.work_id}.{index}",
            )
            for index in range(work.fan_out)
        )
        events = tuple(
            WorkDone(
                dedupe_key=f"synthetic:work_done:{work.work_id}:{index}",
                work_id=work.work_id,
                index=index,
            )
            for index in range(work.emit_events)
        )
        # A plain insert: the platform runs it once, with the job's claim, so a
        # second run would fail loudly instead of hiding a dedupe bug.
        writes = (
            (insert(records).values(work_id=work.work_id, attempt=job.attempt),)
            if work.record
            else ()
        )
        # The fetched body is a raw upstream response; the payload stands in
        # for one when nothing was fetched.
        body = work.model_dump_json().encode() if fetched is None else fetched
        archives = (
            (Archive(kind="work", id=work.work_id, body=body),) if work.archive else ()
        )
        return Outcome(writes=writes, archives=archives, events=events, jobs=children)
