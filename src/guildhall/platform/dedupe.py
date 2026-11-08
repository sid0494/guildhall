"""A job's dedupe key takes effect at most once.

SQS delivers messages at least once, so a job may run twice. The worker claims
the job's key in the same transaction as the job's writes: if the key was
already claimed by a committed transaction, the claim fails and the worker
rolls back. Concurrent claims of one key serialise on its primary key: the
second waits until the first transaction ends, then fails if it committed or
succeeds if it rolled back.
"""

from sqlalchemy import Column, DateTime, Table, Text, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from guildhall.platform.database import metadata

processed_jobs = Table(
    "processed_jobs",
    metadata,
    Column("dedupe_key", Text, primary_key=True),
    Column(
        "completed_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)


async def claim(connection: AsyncConnection, dedupe_key: str) -> bool:
    """Record `dedupe_key` in the caller's transaction.

    Returns False if a committed transaction already recorded it, meaning the
    job already took effect and this delivery must change nothing.
    """
    statement = (
        insert(processed_jobs)
        .values(dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=[processed_jobs.c.dedupe_key])
        .returning(processed_jobs.c.dedupe_key)
    )
    result = await connection.execute(statement)
    return result.first() is not None
