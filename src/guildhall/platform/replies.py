"""Command replies, waiting to be edited into Discord (docs/adr/0064).

A command's reply is queued here in the same transaction as its writes, jobs
and events, so the reply is sent if and only if the command took effect. The
worker's reply dispatcher sends it through the interaction's webhook.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Integer,
    Table,
    Text,
    func,
    insert,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection

from guildhall.contract import Message
from guildhall.platform.database import metadata

outbox_replies = Table(
    "outbox_replies",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("application_id", Text, nullable=False),
    Column("interaction_token", Text, nullable=False),
    Column("message", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("sent_at", DateTime(timezone=True)),
    Column("abandoned_at", DateTime(timezone=True)),
)
"""One row per reply; pending while neither sent nor abandoned."""


async def add_reply(
    connection: AsyncConnection,
    application_id: str,
    interaction_token: str,
    message: Message,
) -> None:
    """Queue a reply in the caller's transaction."""
    await connection.execute(
        insert(outbox_replies).values(
            application_id=application_id,
            interaction_token=interaction_token,
            message=message.model_dump(mode="json"),
        )
    )
