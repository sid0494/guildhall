"""Transactional outboxes: rows written in the job's transaction, sent later.

A database and a queue cannot share a transaction. Writing follow-up jobs and
events as rows in the same transaction as the job's other changes means they
exist if and only if the job committed. A relay sends pending jobs to SQS
afterwards; resending after a crash creates duplicates, which job dedupe makes
harmless. Events are delivered to Discord by the dispatcher (M2).
"""

from collections.abc import Sequence

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from guildhall.contract import Event, JobSpec
from guildhall.platform.database import metadata
from guildhall.platform.envelope import JobEnvelope

MAX_DELAY_SECONDS = 900
"""SQS rejects message delays longer than 15 minutes."""

outbox_jobs = Table(
    "outbox_jobs",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("dedupe_key", Text, nullable=False),
    Column("body", JSONB, nullable=False),
    Column("delay_seconds", Integer, nullable=False, server_default="0"),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column("sent_at", DateTime(timezone=True)),
    CheckConstraint(
        f"delay_seconds BETWEEN 0 AND {MAX_DELAY_SECONDS}", name="delay_seconds_range"
    ),
    Index("ix_outbox_jobs_pending", "id", postgresql_where="sent_at IS NULL"),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("plugin", Text, nullable=False),
    Column("dedupe_key", Text, nullable=False, unique=True),
    Column("type", Text, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
)


async def add_jobs(
    connection: AsyncConnection, plugin: str, specs: Sequence[JobSpec]
) -> None:
    """Queue follow-up jobs in the caller's transaction."""
    if not specs:
        return
    await connection.execute(
        outbox_jobs.insert(),
        [
            {
                "dedupe_key": spec.dedupe_key,
                "body": JobEnvelope.from_spec(plugin, spec).model_dump(mode="json"),
                "delay_seconds": int(spec.delay.total_seconds()),
            }
            for spec in specs
        ],
    )


async def add_events(
    connection: AsyncConnection, plugin: str, events: Sequence[Event]
) -> None:
    """Record events in the caller's transaction; a repeated dedupe key is ignored."""
    if not events:
        return
    await connection.execute(
        insert(outbox_events).on_conflict_do_nothing(
            index_elements=[outbox_events.c.dedupe_key]
        ),
        [
            {
                "plugin": plugin,
                "dedupe_key": event.dedupe_key,
                "type": event.type,
                "schema_version": event.schema_version,
                "payload": event.model_dump(mode="json"),
            }
            for event in events
        ],
    )
