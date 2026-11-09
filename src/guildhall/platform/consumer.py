"""Receives job messages and runs several at once (docs/adr/0021).

A semaphore counts free job slots. The consumer waits for at least one free
slot, takes every free slot up to SQS's limit of 10 messages per receive, and
asks for exactly that many messages, so every message it receives starts at
once. A message never waits locally while its visibility timeout runs down.
Slots are returned as each job finishes, independently of the others.

When asked to stop (docs/adr/0023), the consumer starts no new receive. A
receive already waiting on SQS is left to finish, because cancelling it on our
side does not end it on the server, which may still hand it messages that then
stay invisible for the whole visibility timeout. Whatever it returns is handed
back at once for another worker. Running jobs drain in the meantime, and
anything still running at the deadline is cancelled.
"""

import asyncio
from typing import Protocol

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from guildhall.platform.queue import ReceivedMessage
from guildhall.platform.runner import RunResult

MAX_RECEIVE = 10
"""SQS returns at most 10 messages per receive."""

_log = structlog.get_logger(__name__)


class MessageSource(Protocol):
    """The queue operations the consumer needs; `JobQueue` provides them."""

    async def receive(
        self, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[ReceivedMessage]: ...
    async def retry_later(
        self, message: ReceivedMessage, delay_seconds: int
    ) -> None: ...


class Runner(Protocol):
    """Settles one message; `JobRunner` provides it."""

    async def run(self, message: ReceivedMessage) -> RunResult: ...


class ConsumerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    worker_concurrency: int = Field(default=10, ge=1)
    """Jobs one worker process runs at once."""
    receive_wait_seconds: int = Field(default=20, ge=0, le=20)
    """How long one receive waits on SQS for messages (long polling, at most 20)."""
    shutdown_drain_seconds: float = Field(default=35, ge=0)
    """How long a stopping worker waits for running jobs; above the job timeout."""


class JobConsumer:
    def __init__(
        self,
        source: MessageSource,
        runner: Runner,
        settings: ConsumerSettings,
        *,
        error_pause_seconds: float = 5,
    ) -> None:
        self._source = source
        self._runner = runner
        self._slots = asyncio.Semaphore(settings.worker_concurrency)
        self._drain_seconds = settings.shutdown_drain_seconds
        self._wait_seconds = settings.receive_wait_seconds
        self._error_pause_seconds = error_pause_seconds
        self._jobs: set[asyncio.Task[None]] = set()
        self._polling = False
        """True while a receive is waiting on SQS; such a receive is never cancelled."""

    async def consume(self, stop: asyncio.Event) -> None:
        """Receive and run jobs until `stop` is set, then drain.

        If this coroutine is itself cancelled, everything stops at once.
        """
        receiving = asyncio.create_task(self._receive(stop))
        try:
            await stop.wait()
            if not self._polling:  # waiting for a slot or pausing: nothing to lose
                receiving.cancel()
            _log.info("draining", running=len(self._jobs), polling=self._polling)
            _, left = await asyncio.wait(
                {receiving, *self._jobs}, timeout=self._drain_seconds
            )
            if left:
                _log.warning("cancelling work at the drain deadline", count=len(left))
        finally:
            receiving.cancel()
            for job in list(self._jobs):
                job.cancel()
            await asyncio.gather(receiving, *self._jobs, return_exceptions=True)

    async def _receive(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            free = await self._take_free_slots()
            try:
                messages = await self._poll(free)
            except Exception:
                self._release(free)
                _log.warning("receive failed", exc_info=True)
                if stop.is_set():
                    return
                await asyncio.sleep(self._error_pause_seconds)
                continue
            except BaseException:  # cancelled: only at the deadline or forced
                self._release(free)
                raise
            self._release(free - len(messages))
            if stop.is_set():
                await self._hand_back(messages)
                self._release(len(messages))
                return
            for message in messages:
                job = asyncio.create_task(self._run(message))
                self._jobs.add(job)
                job.add_done_callback(self._jobs.discard)

    async def _poll(self, count: int) -> list[ReceivedMessage]:
        self._polling = True
        try:
            return await self._source.receive(
                max_messages=count, wait_seconds=self._wait_seconds
            )
        finally:
            self._polling = False

    async def _hand_back(self, messages: list[ReceivedMessage]) -> None:
        """Make messages received while stopping visible again at once."""
        for message in messages:
            try:
                await self._source.retry_later(message, 0)
            except Exception:
                # It becomes visible anyway when its visibility timeout ends.
                _log.warning("could not hand back message", exc_info=True)
        if messages:
            _log.info("handed back messages", count=len(messages))

    async def _take_free_slots(self) -> int:
        """Wait for one free slot, then take any others free right now."""
        await self._slots.acquire()
        taken = 1
        while taken < MAX_RECEIVE and not self._slots.locked():
            await self._slots.acquire()  # returns at once: a slot is free
            taken += 1
        return taken

    def _release(self, count: int) -> None:
        for _ in range(count):
            self._slots.release()

    async def _run(self, message: ReceivedMessage) -> None:
        try:
            await self._runner.run(message)
        except Exception:
            # The runner settles everything except queue failures; the message
            # stays on the queue and is delivered again after its timeout.
            _log.exception("could not settle message")
        finally:
            self._slots.release()
