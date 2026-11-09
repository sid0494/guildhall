"""Receives job messages and runs several at once (docs/adr/0021).

A semaphore counts free job slots. The consumer waits for at least one free
slot, takes every free slot up to SQS's limit of 10 messages per receive, and
asks for exactly that many messages, so every message it receives starts at
once. A message never waits locally while its visibility timeout runs down.
Slots are returned as each job finishes, independently of the others.
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
    """The receive operation the consumer needs; `JobQueue` provides it."""

    async def receive(
        self, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[ReceivedMessage]: ...


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


class JobConsumer:
    def __init__(
        self,
        source: MessageSource,
        runner: Runner,
        settings: ConsumerSettings,
        *,
        wait_seconds: int = 20,
        error_pause_seconds: float = 5,
    ) -> None:
        self._source = source
        self._runner = runner
        self._slots = asyncio.Semaphore(settings.worker_concurrency)
        self._wait_seconds = wait_seconds
        self._error_pause_seconds = error_pause_seconds

    async def consume(self) -> None:
        """Receive and run jobs until cancelled."""
        async with asyncio.TaskGroup() as jobs:
            while True:
                free = await self._take_free_slots()
                try:
                    messages = await self._source.receive(
                        max_messages=free, wait_seconds=self._wait_seconds
                    )
                except Exception:
                    self._release(free)
                    _log.warning("receive failed", exc_info=True)
                    await asyncio.sleep(self._error_pause_seconds)
                    continue
                except BaseException:  # cancelled while waiting for messages
                    self._release(free)
                    raise
                self._release(free - len(messages))
                for message in messages:
                    jobs.create_task(self._run(message))

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
