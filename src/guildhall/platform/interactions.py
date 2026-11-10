"""Discord interactions take effect once (docs/adr/0061).

Discord can send an interaction again after a network error. The api records
each interaction's ID before acting on it, in the same transaction as what it
does; a repeat finds the ID taken and changes nothing. Discord retries within
seconds, so IDs are kept a week and then pruned.
"""

from datetime import timedelta
from typing import Final

from sqlalchemy import Column, DateTime, Table, Text, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.contract import JobSpec
from guildhall.platform.database import metadata
from guildhall.platform.outbox import add_jobs

RETENTION: Final = timedelta(days=7)

interactions_seen = Table(
    "interactions_seen",
    metadata,
    Column("interaction_id", Text, primary_key=True),
    Column(
        "received_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)


class PostgresInteractionLog:
    """The api's record of interactions, in Postgres."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def first_time(self, interaction_id: str) -> bool:
        """Record the ID; False if it was already recorded."""
        async with self._engine.begin() as connection:
            return await _record(connection, interaction_id)

    async def accept_command(
        self, interaction_id: str, plugin: str, spec: JobSpec
    ) -> bool:
        """Record the ID and queue the command's job in one transaction, so a
        crash between the two can neither lose the command nor run it twice."""
        async with self._engine.begin() as connection:
            if not await _record(connection, interaction_id):
                return False
            await add_jobs(connection, plugin, [spec])
            return True

    async def is_ready(self) -> bool:
        """Whether Postgres answers."""
        try:
            async with self._engine.connect() as connection:
                await connection.execute(select(1))
        except SQLAlchemyError, OSError:
            return False
        return True


class InteractionPruner:
    """Deletes interaction IDs older than `RETENTION`; run by the worker."""

    name = "interactions_seen"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def prune(self) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(interactions_seen).where(
                    interactions_seen.c.received_at < func.now() - RETENTION
                )
            )
        return result.rowcount


async def _record(connection: AsyncConnection, interaction_id: str) -> bool:
    """Insert the ID; True if it was new. An INSERT's row count cannot be
    trusted here, so `RETURNING` tells a new row from a conflict."""
    statement = (
        insert(interactions_seen)
        .values(interaction_id=interaction_id)
        .on_conflict_do_nothing(index_elements=[interactions_seen.c.interaction_id])
        .returning(interactions_seen.c.interaction_id)
    )
    result = await connection.execute(statement)
    return result.first() is not None
