"""The worker process: consumer, outbox relay and pruning side by side (docs/adr/0022).

The three loops run as sibling tasks in one `asyncio.TaskGroup`. Each loop
recovers from a failed pass in place, by logging it and pausing, so a database
or queue outage pauses work instead of ending the process. Only cancellation
ends a loop, and with it the group.
"""

import asyncio
from typing import Protocol

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = structlog.get_logger(__name__)


class Consumer(Protocol):
    """Receives and runs jobs until cancelled; `JobConsumer` provides it."""

    async def consume(self) -> None: ...


class Relay(Protocol):
    """Outbox housekeeping; `OutboxRelay` provides it."""

    async def relay_once(self) -> int: ...
    async def prune(self) -> int: ...


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    prune_interval_seconds: float = Field(default=3600, gt=0)
    """How often sent outbox rows past their retention are deleted."""


class Worker:
    def __init__(
        self,
        consumer: Consumer,
        relay: Relay,
        settings: WorkerSettings,
        *,
        relay_idle_seconds: float = 1,
        error_pause_seconds: float = 5,
    ) -> None:
        self._consumer = consumer
        self._relay = relay
        self._settings = settings
        self._relay_idle_seconds = relay_idle_seconds
        self._error_pause_seconds = error_pause_seconds

    async def run(self) -> None:
        """Run the consumer, relay and prune loops until cancelled."""
        async with asyncio.TaskGroup() as loops:
            loops.create_task(self._consumer.consume())
            loops.create_task(self._relay_loop())
            loops.create_task(self._prune_loop())

    async def _relay_loop(self) -> None:
        """Go again at once after sending; wait a moment after an empty pass."""
        while True:
            try:
                sent = await self._relay.relay_once()
            except Exception:
                _log.warning("relay pass failed", exc_info=True)
                await asyncio.sleep(self._error_pause_seconds)
                continue
            if sent:
                _log.info("relayed jobs", count=sent)
            else:
                await asyncio.sleep(self._relay_idle_seconds)

    async def _prune_loop(self) -> None:
        """Prune at start-up, then every interval; a failure waits for the next."""
        while True:
            try:
                pruned = await self._relay.prune()
            except Exception:
                _log.warning("prune failed", exc_info=True)
            else:
                _log.info("pruned outbox", count=pruned)
            await asyncio.sleep(self._settings.prune_interval_seconds)
