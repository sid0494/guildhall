"""The scheduler against real Postgres (ADR 0036-0038).

Each test uses a plug-in with a name of its own, so schedule rows, outbox rows
and linked accounts never collide between tests.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.contract import (
    Context,
    Job,
    JobSpec,
    JobType,
    LinkedAccount,
    Outcome,
    RateLimitPolicy,
    Schedule,
    ScheduledRun,
    SourcePlugin,
)
from guildhall.platform import scheduler as scheduler_module
from guildhall.platform.accounts import linked_accounts
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.outbox import outbox_jobs
from guildhall.platform.scheduler import Scheduler, schedule_runs

T0 = datetime(2030, 1, 7, 12, 0, tzinfo=UTC)  # a Monday, on a quarter hour
QUARTER = timedelta(minutes=15)


async def handle(job: Job[ScheduledRun], ctx: Context) -> Outcome:
    return Outcome()


class Clock(SourcePlugin):
    """A test plug-in: polls every 15 minutes, reports per account on Sundays."""

    rate_limit = RateLimitPolicy(per_minute=1, per_day=1)

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def job_types(self) -> list[JobType[Any]]:
        return [
            JobType("poll", ScheduledRun, handle),
            JobType("report", ScheduledRun, handle),
        ]

    def schedules(self) -> list[Schedule]:
        return [
            Schedule(job_type="poll", cron="*/15 * * * *", per_account=False),
            Schedule(job_type="report", cron="0 18 * * SUN"),
        ]


@pytest.fixture
async def plugin(engine: AsyncEngine) -> AsyncIterator[Clock]:
    plugin = Clock(f"clock_{uuid.uuid4().hex[:12]}")
    yield plugin
    async with engine.begin() as connection:
        await connection.execute(
            delete(schedule_runs).where(schedule_runs.c.plugin == plugin.name)
        )
        await connection.execute(
            delete(outbox_jobs).where(outbox_jobs.c.dedupe_key.startswith(plugin.name))
        )
        await connection.execute(
            delete(linked_accounts).where(linked_accounts.c.plugin == plugin.name)
        )


@pytest.fixture
def scheduler(plugin: Clock, engine: AsyncEngine) -> Scheduler:
    return Scheduler({plugin.name: plugin}, engine)


async def queued(engine: AsyncEngine, plugin: Clock) -> list[JobEnvelope]:
    """The jobs the scheduler put in the outbox, oldest first."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(outbox_jobs.c.body)
            .where(outbox_jobs.c.dedupe_key.startswith(plugin.name))
            .order_by(outbox_jobs.c.id)
        )
    return [JobEnvelope.model_validate(row.body) for row in rows]


def runs(envelopes: Sequence[JobEnvelope]) -> list[ScheduledRun]:
    return [ScheduledRun.model_validate(envelope.payload) for envelope in envelopes]


async def link(engine: AsyncEngine, plugin: Clock, user: str, external: str) -> int:
    async with engine.begin() as connection:
        row_id = await connection.scalar(
            insert(linked_accounts)
            .values(plugin=plugin.name, discord_user_id=user, external_id=external)
            .returning(linked_accounts.c.id)
        )
    assert row_id is not None
    return int(row_id)


async def test_a_new_schedule_records_its_latest_tick_without_firing(
    scheduler: Scheduler, engine: AsyncEngine, plugin: Clock
) -> None:
    # Deploying on Monday must not post last Sunday's report.
    assert await scheduler.fire_due(T0 + timedelta(minutes=7)) == 0
    assert await queued(engine, plugin) == []
    async with engine.connect() as connection:
        fired = dict(
            (row.job_type, row.fired_for)
            for row in await connection.execute(
                select(schedule_runs).where(schedule_runs.c.plugin == plugin.name)
            )
        )
    assert fired == {"poll": T0, "report": datetime(2030, 1, 6, 18, 0, tzinfo=UTC)}


async def test_the_next_tick_fires_once(
    scheduler: Scheduler, engine: AsyncEngine, plugin: Clock
) -> None:
    await scheduler.fire_due(T0)
    assert await scheduler.fire_due(T0 + QUARTER) == 1
    assert await scheduler.fire_due(T0 + QUARTER + timedelta(seconds=30)) == 0
    (envelope,) = await queued(engine, plugin)
    assert (envelope.plugin, envelope.type, envelope.attempt) == (
        plugin.name,
        "poll",
        1,
    )
    assert envelope.dedupe_key == f"{plugin.name}:schedule:poll:2030-01-07T12:15Z"
    assert runs([envelope]) == [ScheduledRun(scheduled_for=T0 + QUARTER)]


async def test_missed_ticks_fire_once_for_the_latest(
    scheduler: Scheduler, engine: AsyncEngine, plugin: Clock
) -> None:
    await scheduler.fire_due(T0)
    # The laptop slept for three hours and a bit: twelve ticks went by.
    assert await scheduler.fire_due(T0 + timedelta(hours=3, minutes=5)) == 1
    assert runs(await queued(engine, plugin)) == [
        ScheduledRun(scheduled_for=T0 + timedelta(hours=3))
    ]


async def test_workers_racing_for_a_tick_fire_it_once(
    plugin: Clock, engine: AsyncEngine
) -> None:
    replicas = [Scheduler({plugin.name: plugin}, engine) for _ in range(5)]
    await replicas[0].fire_due(T0)
    created = await asyncio.gather(*(r.fire_due(T0 + QUARTER) for r in replicas))
    assert sorted(created) == [0, 0, 0, 0, 1]
    assert len(await queued(engine, plugin)) == 1


async def test_workers_meeting_a_new_schedule_together_fire_nothing(
    plugin: Clock, engine: AsyncEngine
) -> None:
    replicas = [Scheduler({plugin.name: plugin}, engine) for _ in range(5)]
    created = await asyncio.gather(*(r.fire_due(T0) for r in replicas))
    assert created == [0] * 5
    async with engine.connect() as connection:
        rows = await connection.scalar(
            select(func.count())
            .select_from(schedule_runs)
            .where(schedule_runs.c.plugin == plugin.name)
        )
    assert rows == 2  # one per schedule


async def test_per_account_schedules_fan_out_over_linked_accounts(
    scheduler: Scheduler, engine: AsyncEngine, plugin: Clock
) -> None:
    first = await link(engine, plugin, user="discord-user-a", external="source-a")
    second = await link(engine, plugin, user="discord-user-b", external="source-b")
    other = Clock(f"other_{uuid.uuid4().hex[:12]}")
    await link(engine, other, user="discord-user-c", external="source-c")
    sunday = datetime(2030, 1, 13, 18, 0, tzinfo=UTC)
    await scheduler.fire_due(T0)
    try:
        assert await scheduler.fire_due(sunday) == 3  # two reports and one poll
        reports = [e for e in await queued(engine, plugin) if e.type == "report"]
        assert [e.dedupe_key.rsplit(":", 1)[1] for e in reports] == [
            str(first),
            str(second),
        ]
        assert "source-a" not in reports[0].dedupe_key  # personal ids stay out
        assert [run.account for run in runs(reports)] == [
            LinkedAccount(discord_user_id="discord-user-a", external_id="source-a"),
            LinkedAccount(discord_user_id="discord-user-b", external_id="source-b"),
        ]
        assert {run.scheduled_for for run in runs(reports)} == {sunday}
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(linked_accounts).where(linked_accounts.c.plugin == other.name)
            )


async def test_a_per_account_tick_with_no_accounts_fires_nothing(
    scheduler: Scheduler, engine: AsyncEngine, plugin: Clock
) -> None:
    await scheduler.fire_due(T0)
    sunday = datetime(2030, 1, 13, 18, 0, tzinfo=UTC)
    await scheduler.fire_due(sunday)
    assert [e.type for e in await queued(engine, plugin)] == ["poll"]


async def test_a_tick_whose_jobs_fail_to_queue_stays_unclaimed(
    scheduler: Scheduler,
    engine: AsyncEngine,
    plugin: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await scheduler.fire_due(T0)

    async def broken(
        connection: AsyncConnection, plugin: str, specs: Sequence[JobSpec]
    ) -> None:
        raise ConnectionError("database went away mid-tick")

    with monkeypatch.context() as patch:
        patch.setattr(scheduler_module, "add_jobs", broken)
        with pytest.raises(ConnectionError):
            await scheduler.fire_due(T0 + QUARTER)
    # The claim rolled back with the jobs, so the next pass fires the tick.
    assert await scheduler.fire_due(T0 + QUARTER) == 1
