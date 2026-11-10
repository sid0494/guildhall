"""Runs one received job message through the lifecycle in docs/adr/0016.

The runner settles every message it is given, by deleting it, delaying it or
dead-lettering it, and reports what happened. Errors from the queue itself
propagate to the caller, since the message cannot be settled safely.
"""

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import structlog
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import (
    Accepted,
    AccountLink,
    Context,
    Http,
    Invocation,
    Job,
    JobSpec,
    LinkedAccount,
    LinkRequest,
    Message,
    Outcome,
    PermanentError,
    SourcePlugin,
)
from guildhall.platform.accounts import linked_accounts
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
from guildhall.platform.command_jobs import (
    LINK_COMMAND,
    CommandJob,
    commands_by_path,
    is_command,
    on_link_key,
)
from guildhall.platform.command_schema import ACCOUNT_OPTION
from guildhall.platform.dedupe import claim, processed_jobs
from guildhall.platform.envelope import JobEnvelope
from guildhall.platform.http import ThrottledError
from guildhall.platform.logs import bind_job
from guildhall.platform.outbox import add_events, add_jobs
from guildhall.platform.queue import ReceivedMessage
from guildhall.platform.replies import add_reply
from guildhall.platform.storage import PluginDatabase

_RANDOM = random.SystemRandom()
COMMAND_FAILED = "Something went wrong with that command."
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
    max_receive_count: int = 5
    """The queue's redrive limit (as GUILDHALL_MAX_RECEIVE_COUNT): a failure on
    the last receive is final, so a command's error reply is queued then."""


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


@dataclass(frozen=True)
class Reply:
    """A command's reply, queued with the command's outcome (docs/adr/0064)."""

    application_id: str
    interaction_token: str
    message: Message


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
        if is_command(envelope.type):
            return await self._run_command(message, envelope, attempt, plugin)
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
        outcome = await self._invoke(
            message, envelope, attempt, lambda: job_type.handler(job, context)
        )
        if isinstance(outcome, RunResult):
            return outcome

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

    async def _run_command(
        self,
        message: ReceivedMessage,
        envelope: JobEnvelope,
        attempt: int,
        plugin: SourcePlugin,
    ) -> RunResult:
        """Run a slash command's handler; its reply commits with its outcome.

        A command that fails for good still gets a reply: the generic
        `COMMAND_FAILED`, so the user is not left with "thinking..."
        (docs/adr/0064).
        """
        try:
            if await self._already_processed(envelope.dedupe_key):
                await self._queue.delete(message)
                return RunResult(Status.DUPLICATE)
        except SQLAlchemyError as error:
            return await self._retry(message, f"database error: {type(error).__name__}")
        try:
            job = CommandJob.model_validate(envelope.payload)
        except ValidationError as error:
            return await self._dead_letter(
                message, f"invalid command job: {describe(error)}"
            )
        result = await self._run_command_job(message, envelope, attempt, plugin, job)
        final = result.status is Status.DEAD_LETTERED or (
            result.status is Status.RETRYING
            and message.receive_count >= self._settings.max_receive_count
        )
        if final:
            await self._reply_failed(job)
        return result

    async def _reply_failed(self, job: CommandJob) -> None:
        try:
            async with self._engine.begin() as connection:
                await add_reply(
                    connection,
                    job.application_id,
                    job.interaction_token,
                    Message(content=COMMAND_FAILED),
                )
        except SQLAlchemyError:
            _log.warning("error reply not queued", exc_info=True)

    async def _run_command_job(
        self,
        message: ReceivedMessage,
        envelope: JobEnvelope,
        attempt: int,
        plugin: SourcePlugin,
        job: CommandJob,
    ) -> RunResult:
        if job.path[:1] == (LINK_COMMAND,):
            return await self._run_link(message, envelope, attempt, plugin, job)
        command = commands_by_path(plugin).get(job.path)
        if command is None:
            return await self._dead_letter(
                message, f"unknown command /{' '.join(job.path)} for {plugin.name!r}"
            )
        try:
            options = command.options.model_validate(job.options)
        except ValidationError as error:
            return await self._dead_letter(
                message, f"invalid options: {describe(error)}"
            )
        try:
            account = await self._linked_account(plugin.name, job.discord_user_id)
        except SQLAlchemyError as error:
            return await self._retry(message, f"database error: {type(error).__name__}")

        invocation = Invocation[Any](
            discord_user_id=job.discord_user_id, account=account, options=options
        )
        context = Context(
            db=PluginDatabase(self._engine, plugin),
            archive=PluginArchive(self._archive, plugin),
            http=self._http.for_plugin(plugin),
        )
        result = await self._invoke(
            message, envelope, attempt, lambda: command.handler(invocation, context)
        )
        if isinstance(result, RunResult):
            return result
        outcome = Outcome(writes=result.writes, jobs=result.jobs, events=result.events)
        problems = check_outcome(plugin, outcome)
        if problems:
            return await self._dead_letter(
                message, "contract violation: " + "; ".join(problems)
            )
        reply = Reply(job.application_id, job.interaction_token, result.reply)
        try:
            committed = await self._commit(plugin, envelope, outcome, reply)
        except SQLAlchemyError as error:
            return await self._retry(message, f"database error: {type(error).__name__}")
        await self._queue.delete(message)
        return RunResult(Status.DONE if committed else Status.DUPLICATE)

    async def _run_link(
        self,
        message: ReceivedMessage,
        envelope: JobEnvelope,
        attempt: int,
        plugin: SourcePlugin,
        job: CommandJob,
    ) -> RunResult:
        """`/link <kind> account:<id>` (docs/adr/0066): the plug-in checks the
        account; the platform stores the link, queues the plug-in's follow-up
        job and replies, all in one transaction."""
        link = plugin.account_link()
        if link is None or job.path[1:] != (link.kind,):
            return await self._dead_letter(
                message, f"unknown link /{' '.join(job.path)} for {plugin.name!r}"
            )
        try:
            previous = await self._linked_account(plugin.name, job.discord_user_id)
        except SQLAlchemyError as error:
            return await self._retry(message, f"database error: {type(error).__name__}")
        try:
            request = LinkRequest(
                discord_user_id=job.discord_user_id,
                external_id=str(job.options.get(ACCOUNT_OPTION, "")).strip(),
                previous=previous,
            )
        except ValidationError as error:
            return await self._dead_letter(
                message, f"invalid options: {describe(error)}"
            )
        context = Context(
            db=PluginDatabase(self._engine, plugin),
            archive=PluginArchive(self._archive, plugin),
            http=self._http.for_plugin(plugin),
        )
        verdict = await self._invoke(
            message, envelope, attempt, lambda: link.verify(request, context)
        )
        if isinstance(verdict, RunResult):
            return verdict
        try:
            if isinstance(verdict, Accepted):
                committed = await self._commit_link(
                    plugin, envelope, job, link, verdict, previous
                )
            else:
                reply = Reply(
                    job.application_id,
                    job.interaction_token,
                    Message(content=verdict.reason),
                )
                committed = await self._commit(plugin, envelope, Outcome(), reply)
        except SQLAlchemyError as error:
            return await self._retry(message, f"database error: {type(error).__name__}")
        await self._queue.delete(message)
        return RunResult(Status.DONE if committed else Status.DUPLICATE)

    async def _commit_link(
        self,
        plugin: SourcePlugin,
        envelope: JobEnvelope,
        job: CommandJob,
        link: AccountLink,
        accepted: Accepted,
        previous: LinkedAccount | None,
    ) -> bool:
        """Claim, store the link (keeping its ID when replacing one), queue the
        follow-up job with the stored account, and queue the reply."""
        new_link = insert(linked_accounts).values(
            plugin=plugin.name,
            discord_user_id=job.discord_user_id,
            external_id=accepted.external_id,
        )
        upsert = new_link.on_conflict_do_update(
            index_elements=[
                linked_accounts.c.plugin,
                linked_accounts.c.discord_user_id,
            ],
            set_={
                "external_id": new_link.excluded.external_id,
                "linked_at": func.now(),
            },
        ).returning(linked_accounts.c.id)
        async with self._engine.connect() as connection:
            transaction = await connection.begin()
            if not await claim(connection, envelope.dedupe_key):
                await transaction.rollback()
                return False
            link_id = (await connection.execute(upsert)).scalar_one()
            account = LinkedAccount(
                id=link_id,
                discord_user_id=job.discord_user_id,
                external_id=accepted.external_id,
            )
            if link.on_link is not None:
                follow_up = JobSpec(
                    type=link.on_link,
                    payload=account,
                    dedupe_key=on_link_key(envelope.dedupe_key),
                )
                await add_jobs(connection, plugin.name, [follow_up])
            await add_reply(
                connection,
                job.application_id,
                job.interaction_token,
                Message(content=_linked(link.kind, accepted, previous)),
            )
            await transaction.commit()
            return True

    async def _invoke[T](
        self,
        message: ReceivedMessage,
        envelope: JobEnvelope,
        attempt: int,
        call: Callable[[], Awaitable[T]],
    ) -> T | RunResult:
        """Run a handler under the job timeout; a failure settles the message
        and comes back as the RunResult saying how."""
        try:
            async with asyncio.timeout(self._settings.job_timeout_seconds):
                return await call()
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

    async def _linked_account(
        self, plugin: str, discord_user_id: str
    ) -> LinkedAccount | None:
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    select(
                        linked_accounts.c.id,
                        linked_accounts.c.discord_user_id,
                        linked_accounts.c.external_id,
                    ).where(
                        linked_accounts.c.plugin == plugin,
                        linked_accounts.c.discord_user_id == discord_user_id,
                    )
                )
            ).first()
        if row is None:
            return None
        return LinkedAccount(
            id=row.id, discord_user_id=row.discord_user_id, external_id=row.external_id
        )

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
        self,
        plugin: SourcePlugin,
        envelope: JobEnvelope,
        outcome: Outcome,
        reply: Reply | None = None,
    ) -> bool:
        """Claim the key, run the plug-in's writes and fill the outboxes, atomically.

        A command's reply joins the same transaction, so it is sent if and only
        if the command took effect. Returns False, writing nothing, if another
        delivery already committed.
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
            if reply is not None:
                await add_reply(
                    connection,
                    reply.application_id,
                    reply.interaction_token,
                    reply.message,
                )
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


def _linked(kind: str, accepted: Accepted, previous: LinkedAccount | None) -> str:
    replaced = (
        f", replacing {previous.external_id}"
        if previous is not None and previous.external_id != accepted.external_id
        else ""
    )
    return f"Linked your {kind} account {accepted.external_id}{replaced}."
