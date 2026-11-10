"""Posts plug-ins' events to Discord (docs/adr/0072).

Each pending event is rebuilt as its plug-in's model, rendered by the plug-in
(pure formatting, under a time limit) and posted to the configured channel
with the bot token. Creating a message is not idempotent, so every post
carries a nonce Discord enforces: a retry within a few minutes returns the
message already made instead of making another. An event still undelivered a
day after it was recorded is abandoned rather than posted late.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any, Final

import httpx
import structlog
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.contract import Message, SourcePlugin
from guildhall.platform.checks import event_types_by_name
from guildhall.platform.discord import Delivery, classify, discord_message
from guildhall.platform.outbox import outbox_events

STALE_AFTER: Final = timedelta(hours=24)
RETENTION: Final = timedelta(days=7)
MAX_RATE_LIMIT_PAUSE: Final = 60.0
MAX_RETRY_PAUSE: Final = 30.0

_log = structlog.get_logger(__name__)


class PostSettings(BaseSettings):
    """Where posts go. Without a channel, the worker posts nothing."""

    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    discord_channel_id: str | None = None
    """The channel posts go to: personal, so only in .env."""
    discord_bot_token: SecretStr | None = None
    event_batch_size: int = Field(default=20, gt=0)
    event_max_attempts: int = Field(default=5, gt=0)
    render_timeout_seconds: float = Field(default=2, gt=0)


class _UnpostableError(Exception):
    """The event can never be posted; the message says why."""


class EventDispatcher:
    name = "events"

    def __init__(
        self,
        engine: AsyncEngine,
        client: httpx.AsyncClient,
        plugins: Mapping[str, SourcePlugin],
        channel_id: str,
        settings: PostSettings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._client = client
        self._plugins = plugins
        self._channel_id = channel_id
        self._settings = settings
        self._sleep = sleep

    async def dispatch_once(self) -> int:
        """Handle one batch of pending events; returns how many were settled."""
        pause = 0.0
        settled = 0
        async with self._engine.connect() as connection, connection.begin():
            now = await connection.scalar(select(func.now()))
            rows = (
                await connection.execute(
                    select(outbox_events)
                    .where(
                        outbox_events.c.sent_at.is_(None),
                        outbox_events.c.abandoned_at.is_(None),
                    )
                    .order_by(outbox_events.c.id)
                    .limit(self._settings.event_batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                if now - row.created_at > STALE_AFTER:
                    await _abandon(connection, row.id, "stale")
                    settled += 1
                    continue
                try:
                    message = await self._render(row)
                except _UnpostableError as reason:
                    await _abandon(connection, row.id, str(reason))
                    settled += 1
                    continue
                if message is None:
                    await _mark_sent(connection, row.id, None)
                    settled += 1
                    continue
                result, detail = await self._post(row.id, message)
                if result is Delivery.RATE_LIMITED:
                    pause = min(detail, MAX_RATE_LIMIT_PAUSE)
                    _log.warning("discord rate limit", seconds=pause)
                    break
                settled += await self._record(connection, row, result, detail)
                if result is Delivery.TRY_AGAIN:
                    pause = max(pause, min(2.0 ** (row.attempts + 1), MAX_RETRY_PAUSE))
        if pause:
            await self._sleep(pause)
        return settled

    async def _render(self, row: Any) -> Message | None:
        plugin = self._plugins.get(row.plugin)
        if plugin is None:
            raise _UnpostableError(f"plug-in {row.plugin!r} is not enabled")
        event_type = event_types_by_name(plugin).get(row.type)
        if event_type is None:
            raise _UnpostableError(
                f"{row.type!r} is not one of the plug-in's event types"
            )
        try:
            event = event_type.model_validate(row.payload)
        except ValidationError as error:
            raise _UnpostableError(f"invalid {row.type!r} event") from error
        try:
            # In a thread, so a render that hangs cannot stall the worker.
            return await asyncio.wait_for(
                asyncio.to_thread(plugin.render, event),
                timeout=self._settings.render_timeout_seconds,
            )
        except TimeoutError:
            raise _UnpostableError("render took too long") from None
        except Exception as error:
            raise _UnpostableError(f"render failed: {type(error).__name__}") from error

    async def _post(self, event_id: int, message: Message) -> tuple[Delivery, Any]:
        body = discord_message(message) | {
            # Discord returns the message already made with this nonce, if any.
            "nonce": f"evt-{event_id}",
            "enforce_nonce": True,
        }
        try:
            response = await self._client.post(
                f"/channels/{self._channel_id}/messages", json=body
            )
        except httpx.HTTPError as error:
            return Delivery.TRY_AGAIN, type(error).__name__
        result, detail = classify(response)
        if result is Delivery.SENT:
            return result, str(response.json().get("id", ""))
        return result, detail

    async def _record(
        self, connection: AsyncConnection, row: Any, result: Delivery, detail: Any
    ) -> int:
        if result is Delivery.SENT:
            await _mark_sent(connection, row.id, detail or None, row.attempts + 1)
            _log.info("event posted", event_id=row.id, type=row.type)
            return 1
        if result is Delivery.GIVE_UP:
            await _abandon(connection, row.id, f"discord answered {detail}")
            return 1
        attempts = row.attempts + 1
        if attempts >= self._settings.event_max_attempts:
            await _abandon(connection, row.id, f"failed {attempts} times: {detail}")
            return 1
        await connection.execute(
            update(outbox_events)
            .where(outbox_events.c.id == row.id)
            .values(attempts=attempts)
        )
        _log.warning(
            "event post failed, will retry", event_id=row.id, error=str(detail)
        )
        return 0


async def _mark_sent(
    connection: AsyncConnection,
    event_id: int,
    message_id: str | None,
    attempts: int | None = None,
) -> None:
    values: dict[str, Any] = {"sent_at": func.now(), "discord_message_id": message_id}
    if attempts is not None:
        values["attempts"] = attempts
    await connection.execute(
        update(outbox_events).where(outbox_events.c.id == event_id).values(**values)
    )


async def _abandon(connection: AsyncConnection, event_id: int, reason: str) -> None:
    await connection.execute(
        update(outbox_events)
        .where(outbox_events.c.id == event_id)
        .values(abandoned_at=func.now())
    )
    _log.warning("event abandoned", event_id=event_id, reason=reason)


class EventPruner:
    """Deletes events posted or abandoned over a week ago. Long enough that no
    analysis can emit the same key again and post it twice: matches are posted
    only within 12 hours of ending (docs/adr/0056)."""

    name = "outbox_events"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def prune(self) -> int:
        cutoff = func.now() - RETENTION
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(outbox_events).where(
                    or_(
                        outbox_events.c.sent_at < cutoff,
                        outbox_events.c.abandoned_at < cutoff,
                    )
                )
            )
        return result.rowcount
