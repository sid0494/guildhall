"""The platform's side of plug-in storage: `Context.db` (docs/adr/0030).

Each `fetch` checks the statement, borrows a pooled connection, runs it in a
read-only transaction and returns every row, so no connection or transaction is
held while the handler waits on anything else. Postgres enforces read-only
independently of the statement check.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, Select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import SourcePlugin
from guildhall.platform.checks import ContractViolationError, check_read


class PluginDatabase:
    """`Context.db` for one plug-in: reads of its own tables only."""

    def __init__(self, engine: AsyncEngine, plugin: SourcePlugin) -> None:
        self._engine = engine
        self._plugin = plugin

    async def fetch(self, statement: Select[Any]) -> Sequence[Row[Any]]:
        problems = check_read(self._plugin, statement)
        if problems:
            raise ContractViolationError("read " + "; ".join(problems))
        async with self._engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            rows: Sequence[Row[Any]] = (await connection.execute(statement)).all()
        return rows
