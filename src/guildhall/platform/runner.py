"""Runs one received job message through the lifecycle in docs/adr/0016.

The runner settles every message it is given, by deleting it, delaying it or
dead-lettering it, and reports what happened. Errors from the queue itself
propagate to the caller, since the message cannot be settled safely.
"""

import asyncio
import logging
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import structlog
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import (
    Context,
    Http,
    Job,
    Outcome,
    PermanentError,
    SourcePlugin,
)
from guildhall.platform.archive import (
    ArchiveError,
    ArchiveStore,
    PluginArchive,
    archive_key,
)
from guildhall.platform.checks import (
    MAX_QUEUE_DELAY,
    ContractViolationError,
    check_outcome,
    job_types_by_name,
)
from guildhall.platform.dedupe import claim, processed_jobs
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.http import ThrottledError
from guildhall.platform.logs import bind_job
from guildhall.platform.outbox import add_events, add_jobs
from guildhall.platform.queue import ReceivedMessage
from guildhall.platform.storage import PluginDatabase

_RANDOM = random.SystemRandom()
_log = structlog.get_logger(__name__)


class MessageQueue(Protocol):
    """The queue operations the runner needs; `JobQueue` provides them."""

    async def send(self, body: str, delay_seconds: int = 0) -> None: ...
    async def delete(self, message: ReceivedMessage) -> None: ...
    async def retry_later(
        self, message: ReceivedMessage, delay_seconds: int
    ) -> None: ...
    async def dead_letter(self, message: ReceivedMessage, reason: str) -> None: ...


class HttpProvider(Protocol):
    """Each plug-in's `Context.http`; `UpstreamHttp` provides it."""

    def for_plugin(self, plugin: SourcePlugin) -> Http: ...


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    job_timeout_seconds: float = 30
    archive_timeout_seconds: float = 10
    """With the job timeout, kept below the queue's visibility timeout, so a job
    never runs twice at once."""
    retry_base_seconds: float = 20
    retry_cap_seconds: float = 900


class Status(StrEnum):
    DONE = "done"
    DUPLICATE = "duplicate"
    NOT_READY = "not_ready"
    DEFERRED = "deferred"
    RETRYING = "retrying"
    DEAD_LETTERED = "dead_lettered"


@dataclass(frozen=True)
class RunResult:
    status: Status
    reason: str | None = None


_LEVELS = {
    Status.DONE: logging.INFO,
    Status.DUPLICATE: logging.INFO,
    Status.NOT_READY: logging.INFO,
    Status.DEFERRED: logging.INFO,
    Status.RETRYING: logging.WARNING,
    Status.DEAD_LETTERED: logging.ERROR,
}


def _log_result(result: RunResult) -> None:
    _log.log(
        _LEVELS[result.status],
        "job settled",
        status=result.status,
        reason=result.reason,
    )


def backoff_seconds(receive_count: int, base: float, cap: float) -> int:
    """Exponential backoff with equal jitter: half fixed, half random."""
    exponential = min(cap, base * 2.0 ** (receive_count - 1))
    return round(exponential / 2 + _RANDOM.uniform(0, exponential / 2))


def defer_seconds(retry_after: float) -> int:
    """Retry-after plus up to half again, so deferred jobs don't return together;
    at most the queue's 15-minute limit."""
    delay = math.ceil(retry_after * _RANDOM.uniform(1.0, 1.5))
    return min(int(MAX_QUEUE_DELAY.total_seconds()), max(1, delay))


def describe(error: ValidationError) -> str:
    """Field locations and messages only, never the offending input values."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
        for item in error.errors()
    )


class JobRunner:
    def __init__(
        self,
        plugins: Mapping[str, SourcePlugin],
        engine: AsyncEngine,
        queue: MessageQueue,
        archive: ArchiveStore,
        http: HttpProvider,
        settings: RunnerSettings,
    ) -> None:
        self._plugins = plugins
        self._engine = engine
        self._queue = queue
        self._archive = archive
        self._http = http
        self._settings = settings

    async def run(self, message: ReceivedMessage) -> RunResult:
        """Settle the message and log one line saying how."""
        try:
            envelope = JobEnvelope.model_validate_json(message.body)
        except ValidationError as error:
            result = await self._dead_letter(
                message, f"invalid envelope: {describe(error)}"
            )
            _log_result(result)
            return result

        attempt = envelope.attempt + message.receive_count - 1
        with bind_job(envelope.plugin, envelope.type, envelope.dedupe_key, attempt):
            result = await self._run(message, envelope, attempt)
            _log_result(result)
            return result

    async def _run(
        self, message: ReceivedMessage, envelope: JobEnvelope, attempt: int
    ) -> RunResult:
        plugin = self._plugins.get(envelope.plugin)
        if plugin is None:
            return await self._dead_letter(
                message, f"plug-in {envelope.plugin!r} is not enabled"
            )
        job_type = job_types_by_name(plugin).get(envelope.type)
        if job_type is None:
            return await self._dead_letter(
                message, f"unknown job type {envelope.type!r} for {plugin.name!r}"
            )

        try:
            if await self._already_processed(envelope.dedupe_key):
                await self._queue.delete(message)
                return RunResult(Status.DUPLICATE)
        except SQLAlchemyError as error:
            return await self._retry(message, f"database error: {type(error).__name__}")

        try:
            payload = job_type.payload_model.model_validate(envelope.payload)
        except ValidationError as error:
            return await self._dead_letter(
                message, f"invalid payload: {describe(error)}"
            )

        context = Context(
            db=PluginDatabase(self._engine, plugin),
            archive=PluginArchive(self._archive, plugin),
            http=self._http.for_plugin(plugin),
        )
        job = Job[Any](
            type=envelope.type,
            payload=payload,
            dedupe_key=envelope.dedupe_key,
            attempt=attempt,
        )
        try:
            async with asyncio.timeout(self._settings.job_timeout_seconds):
                outcome = await job_type.handler(job, context)
        except PermanentError as error:
            return await self._dead_letter(message, f"permanent error: {error}")
        except ContractViolationError as error:
            return await self._dead_letter(message, f"contract violation: {error}")
        except ThrottledError as error:
            return await self._defer(message, envelope, attempt, error)
        except TimeoutError:
            return await self._retry(
                message, f"timed out after {self._settings.job_timeout_seconds} s"
            )
        except Exception as error:  # RetryableError and unexpected failures alike
            _log.warning("handler raised", exc_info=error)
            return await self._retry(message, f"{type(error).__name__}: {error}")

        problems = check_outcome(plugin, outcome)
        if problems:
            return await self._dead_letter(
                message, "contract violation: " + "; ".join(problems)
            )

        if outcome.retry_in is not None:
            retry = envelope.model_copy(update={"attempt": attempt + 1})
            await self._queue.send(
                retry.model_dump_json(),
                delay_seconds=int(outcome.retry_in.total_seconds()),
            )
            await self._queue.delete(message)
            return RunResult(Status.NOT_READY)

        # Before the commit, so a committed job's archives always exist; a
        # failure here commits nothing and the job runs again (docs/adr/0031).
        try:
            async with asyncio.timeout(self._settings.archive_timeout_seconds):
                await self._upload(plugin, outcome)
        except ArchiveError as error:
            return await self._retry(message, f"archive error: {error}")
        except TimeoutError:
            return await self._retry(
                message,
                "archive upload timed out after"
                f" {self._settings.archive_timeout_seconds} s",
            )

        try:
            committed = await self._commit(plugin, envelope, outcome)
        except SQLAlchemyError as error:
            return await self._retry(message, f"database error: {type(error).__name__}")
        await self._queue.delete(message)
        return RunResult(Status.DONE if committed else Status.DUPLICATE)

    async def _upload(self, plugin: SourcePlugin, outcome: Outcome) -> None:
        for archive in outcome.archives:
            key = archive_key(plugin.name, archive.kind, archive.id)
            await self._archive.put(key, archive.body)

    async def _already_processed(self, dedupe_key: str) -> bool:
        async with self._engine.connect() as connection:
            found = await connection.scalar(
                select(processed_jobs.c.dedupe_key).where(
                    processed_jobs.c.dedupe_key == dedupe_key
                )
            )
        return found is not None

    async def _commit(
        self, plugin: SourcePlugin, envelope: JobEnvelope, outcome: Outcome
    ) -> bool:
        """Claim the key, run the plug-in's writes and fill the outboxes, atomically.

        Returns False, writing nothing, if another delivery already committed.
        """
        async with self._engine.connect() as connection:
            transaction = await connection.begin()
            if not await claim(connection, envelope.dedupe_key):
                await transaction.rollback()
                return False
            for write in outcome.writes:
                await connection.execute(write)
            await add_events(connection, plugin.name, outcome.events)
            await add_jobs(connection, plugin.name, outcome.jobs)
            await transaction.commit()
            return True

    async def _defer(
        self,
        message: ReceivedMessage,
        envelope: JobEnvelope,
        attempt: int,
        error: ThrottledError,
    ) -> RunResult:
        """Send the job again later as a new message, keeping its attempt number.

        A new message starts its receive count again, so throttling never moves
        a job towards the dead-letter queue (docs/adr/0033).
        """
        later = envelope.model_copy(update={"attempt": attempt})
        delay = defer_seconds(error.retry_after)
        await self._queue.send(later.model_dump_json(), delay_seconds=delay)
        await self._queue.delete(message)
        return RunResult(Status.DEFERRED, f"{error} (again in {delay} s)")

    async def _retry(self, message: ReceivedMessage, reason: str) -> RunResult:
        delay = backoff_seconds(
            message.receive_count,
            self._settings.retry_base_seconds,
            self._settings.retry_cap_seconds,
        )
        await self._queue.retry_later(message, delay)
        return RunResult(Status.RETRYING, reason)

    async def _dead_letter(self, message: ReceivedMessage, reason: str) -> RunResult:
        await self._queue.dead_letter(message, reason)
        return RunResult(Status.DEAD_LETTERED, reason)
