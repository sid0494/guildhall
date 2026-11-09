"""Plug-in storage: the tables a plug-in owns and how it reads them.

A plug-in declares its tables with SQLAlchemy Core in a `plugin_metadata()`
(docs/adr/0029). It reads them through `Context.db` and writes them by
returning statements in `Outcome.writes`, which the platform runs in the job's
transaction (docs/adr/0028, docs/adr/0030).
"""

from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy import MetaData, Row, Select

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Deterministic constraint names, so migrations can refer to them by name."""


def plugin_metadata() -> MetaData:
    """A `MetaData` for a plug-in's tables, with the platform's naming convention."""
    return MetaData(naming_convention=NAMING_CONVENTION)


class Database(Protocol):
    """Read access to the plug-in's own tables."""

    async def fetch(self, statement: Select[Any]) -> Sequence[Row[Any]]:
        """Run one `select` in its own read-only transaction; return every row.

        The statement may read only tables the plug-in declares, and may not
        contain raw SQL text. No connection is held between calls.
        """
        ...
