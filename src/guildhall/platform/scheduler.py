"""Turns plug-ins' schedules into jobs, once per tick (docs/adr/0036-0038).

Every worker runs `fire_due` every `GUILDHALL_SCHEDULE_INTERVAL_SECONDS` (30 s). Each
schedule has one `schedule_runs` row holding the tick it last fired for. A
worker claims a newer tick with a compare-and-set update; only one replica's
update can match, and it writes the tick's jobs to the outbox in the same
transaction, so a claimed tick always has its jobs and the relay sends them.

Only the most recent due tick fires: ticks missed while no worker ran are
coalesced into one. A schedule seen for the first time records its latest
tick without firing it.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta

import structlog
from cronsim import CronSim
from sqlalchemy import Column, DateTime, Table, Text, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.contract import (
    JobSpec,
    LinkedAccount,
    Schedule,
    ScheduledRun,
    SourcePlugin,
)
from guildhall.platform.accounts import linked_accounts
from guildhall.platform.database import metadata
from guildhall.platform.outbox import add_jobs

_log = structlog.get_logger(__name__)

schedule_runs = Table(
    "schedule_runs",
    metadata,
    Column("plugin", Text, primary_key=True),
    Column("job_type", Text, primary_key=True),
    Column("fired_for", DateTime(timezone=True), nullable=False),
)
"""The tick each schedule last fired for (or was first seen at)."""


def latest_tick(cron: str, now: datetime) -> datetime:
    """The most recent time `cron` matches, at or before `now` (UTC)."""
    # CronSim's reverse iteration starts strictly before its start time.
    return next(CronSim(cron, now + timedelta(microseconds=1), reverse=True))


def dedupe_key(
    plugin: str, schedule: Schedule, tick: datetime, link: int | None
) -> str:
    """Namespaced by plug-in; the link's row id, never its external id."""
    key = f"{plugin}:schedule:{schedule.job_type}:{tick:%Y-%m-%dT%H:%MZ}"
    return key if link is None else f"{key}:{link}"


class Scheduler:
    def __init__(
        self, plugins: Mapping[str, SourcePlugin], engine: AsyncEngine
    ) -> None:
        self._plugins = plugins
        self._engine = engine

    async def fire_due(self, now: datetime) -> int:
        """Fire every schedule with a tick due at `now`; returns jobs created."""
        created = 0
        for plugin in self._plugins.values():
            for schedule in plugin.schedules():
                created += await self._fire(
                    plugin, schedule, latest_tick(schedule.cron, now)
                )
        return created

    async def _fire(
        self, plugin: SourcePlugin, schedule: Schedule, tick: datetime
    ) -> int:
        async with self._engine.begin() as connection:
            # A schedule's first row records its latest tick as already fired,
            # so the claim below cannot match it: first sight never fires.
            await connection.execute(
                insert(schedule_runs)
                .values(plugin=plugin.name, job_type=schedule.job_type, fired_for=tick)
                .on_conflict_do_nothing()
            )
            # Compare-and-set: only one transaction can move fired_for to this
            # tick. UPDATE's rowcount is reliable (an INSERT's is not).
            claimed = await connection.execute(
                update(schedule_runs)
                .where(
                    schedule_runs.c.plugin == plugin.name,
                    schedule_runs.c.job_type == schedule.job_type,
                    schedule_runs.c.fired_for < tick,
                )
                .values(fired_for=tick)
            )
            if claimed.rowcount == 0:  # fired already, here or by another worker
                return 0
            specs = await self._jobs_for(connection, plugin, schedule, tick)
            await add_jobs(connection, plugin.name, specs)
        _log.info(
            "schedule fired",
            plugin=plugin.name,
            job_type=schedule.job_type,
            scheduled_for=tick.isoformat(),
            jobs=len(specs),
        )
        return len(specs)

    async def _jobs_for(
        self,
        connection: AsyncConnection,
        plugin: SourcePlugin,
        schedule: Schedule,
        tick: datetime,
    ) -> list[JobSpec]:
        if not schedule.per_account:
            run = ScheduledRun(scheduled_for=tick)
            return [
                JobSpec(
                    type=schedule.job_type,
                    payload=run,
                    dedupe_key=dedupe_key(plugin.name, schedule, tick, None),
                )
            ]
        rows = await connection.execute(
            select(
                linked_accounts.c.id,
                linked_accounts.c.discord_user_id,
                linked_accounts.c.external_id,
            )
            .where(linked_accounts.c.plugin == plugin.name)
            .order_by(linked_accounts.c.id)
        )
        return [
            JobSpec(
                type=schedule.job_type,
                payload=ScheduledRun(
                    scheduled_for=tick,
                    account=LinkedAccount(
                        discord_user_id=row.discord_user_id,
                        external_id=row.external_id,
                    ),
                ),
                dedupe_key=dedupe_key(plugin.name, schedule, tick, row.id),
            )
            for row in rows
        ]
