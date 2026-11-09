"""A synthetic plug-in for end-to-end tests, failure drills and load checks.

It calls no upstream API. Each job's payload says what the job should do, so
new drill scenarios need no new code. Disabled unless enabled in config.
"""

import asyncio
from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import Column, DateTime, Integer, Table, Text, func, insert

from guildhall.contract import (
    Context,
    ContractModel,
    Event,
    Job,
    JobSpec,
    JobType,
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

    Waits `sleep_ms`; returns a retry outcome while `attempt <= not_ready_for`;
    then fails as `fail` says, or finishes with `fan_out` follow-up jobs and
    `emit_events` events, writing a row to `synthetic_records` if `record` is
    set. `fail="crash"` raises an untyped exception on purpose, to drill how
    the platform handles a broken plug-in.
    """

    work_id: Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")]
    sleep_ms: Annotated[int, Field(ge=0, le=60_000)] = 0
    not_ready_for: Annotated[int, Field(ge=0, le=100)] = 0
    fail: Literal["none", "retryable", "permanent", "crash"] = "none"
    fan_out: Annotated[int, Field(ge=0, le=1_000)] = 0
    emit_events: Annotated[int, Field(ge=0, le=100)] = 0
    record: bool = False


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

    async def work(self, job: Job[SyntheticWork], ctx: Context) -> Outcome:
        work = job.payload
        if work.sleep_ms:
            await asyncio.sleep(work.sleep_ms / 1000)
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
        return Outcome(writes=writes, events=events, jobs=children)
