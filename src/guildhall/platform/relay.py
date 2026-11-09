"""Sends follow-up jobs from `outbox_jobs` to the queue (docs/adr/0019).

Each pass locks the oldest unsent rows with `FOR UPDATE SKIP LOCKED`, so
concurrent relays in other worker replicas take different rows instead of
waiting or sending the same ones. Rows are marked sent in the same transaction
that locked them; if sending fails part-way, the transaction rolls back and
the whole batch is sent again later, and job dedupe absorbs the duplicates.
"""

from datetime import timedelta
from typing import Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.outbox import outbox_jobs


class JobSender(Protocol):
    """The one queue operation the relay needs; `JobQueue` provides it."""

    async def send(self, body: str, delay_seconds: int = 0) -> None: ...


class RelaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    relay_batch_size: int = 100
    outbox_retention_days: float = 7
    """How long sent rows are kept, so "was this job sent, and when?" is answerable."""


class OutboxRelay:
    def __init__(
        self, engine: AsyncEngine, queue: JobSender, settings: RelaySettings
    ) -> None:
        self._engine = engine
        self._queue = queue
        self._settings = settings

    async def relay_once(self) -> int:
        """Send one batch of pending jobs; returns how many were sent."""
        async with self._engine.connect() as connection, connection.begin():
            rows = (
                await connection.execute(
                    select(
                        outbox_jobs.c.id,
                        outbox_jobs.c.body,
                        outbox_jobs.c.delay_seconds,
                    )
                    .where(outbox_jobs.c.sent_at.is_(None))
                    .order_by(outbox_jobs.c.id)
                    .limit(self._settings.relay_batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                body = JobEnvelope.model_validate(row.body).model_dump_json()
                await self._queue.send(body, delay_seconds=row.delay_seconds)
            if rows:
                await connection.execute(
                    update(outbox_jobs)
                    .where(outbox_jobs.c.id.in_([row.id for row in rows]))
                    .values(sent_at=func.now())
                )
        return len(rows)

    async def prune(self) -> int:
        """Delete rows sent longer ago than the retention period; returns the count."""
        cutoff = func.now() - timedelta(days=self._settings.outbox_retention_days)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(outbox_jobs).where(outbox_jobs.c.sent_at < cutoff)
            )
        return result.rowcount
