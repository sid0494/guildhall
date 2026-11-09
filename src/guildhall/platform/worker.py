"""The worker process: consumer, outbox relay and pruning side by side (docs/adr/0022).

The three loops run as sibling tasks in one `asyncio.TaskGroup`. Each loop
recovers from a failed pass in place, by logging it and pausing, so a database
or queue outage pauses work instead of ending the process.

Stopping (docs/adr/0023): the consumer stops receiving and drains its jobs
while the relay keeps sending their follow-up jobs; then pruning is cancelled
and the relay makes final passes until the outbox is empty, within a deadline.
"""

import asyncio
import contextlib
from typing import Protocol

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = structlog.get_logger(__name__)


class Consumer(Protocol):
    """Runs jobs until `stop` is set, then drains; `JobConsumer` provides it."""

    async def consume(self, stop: asyncio.Event) -> None: ...


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
        flush_seconds: float = 5,
    ) -> None:
        self._consumer = consumer
        self._relay = relay
        self._settings = settings
        self._relay_idle_seconds = relay_idle_seconds
        self._error_pause_seconds = error_pause_seconds
        self._flush_seconds = flush_seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Run the consumer, relay and prune loops until `stop`, then wind down."""
        finishing = asyncio.Event()
        async with asyncio.TaskGroup() as loops:
            relay = loops.create_task(self._relay_loop(finishing))
            prune = loops.create_task(self._prune_loop())
            await self._consumer.consume(stop)
            prune.cancel()
            finishing.set()
            _, left = await asyncio.wait({relay}, timeout=self._flush_seconds)
            if left:
                _log.warning("outbox flush cut off at the deadline")
                relay.cancel()

    async def _relay_loop(self, finishing: asyncio.Event) -> None:
        """Go again at once after sending; wait a moment after an empty pass.

        Once `finishing` is set, keep going until a pass finds nothing, then
        return; a failure then ends the loop instead of pausing it.
        """
        while True:
            try:
                sent = await self._relay.relay_once()
            except Exception:
                _log.warning("relay pass failed", exc_info=True)
                if finishing.is_set():
                    return
                await asyncio.sleep(self._error_pause_seconds)
                continue
            if sent:
                _log.info("relayed jobs", count=sent)
                continue
            if finishing.is_set():
                return
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._relay_idle_seconds):
                    await finishing.wait()

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
