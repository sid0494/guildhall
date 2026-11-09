"""Linked accounts: who each Discord user is in each plug-in's source.

`/link` writes them (docs/design-spec.md, M2 C2); `per_account` schedules fan
out over them (docs/adr/0038). `external_id` is a personal identifier, so it
appears in job payloads, never in dedupe keys or log lines; those use `id`.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Table,
    Text,
    UniqueConstraint,
    func,
)

from guildhall.platform.database import metadata

linked_accounts = Table(
    "linked_accounts",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("plugin", Text, nullable=False),
    Column("discord_user_id", Text, nullable=False),
    Column("external_id", Text, nullable=False),
    Column(
        "linked_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    UniqueConstraint("plugin", "discord_user_id"),
)
"""One account per Discord user per plug-in."""
