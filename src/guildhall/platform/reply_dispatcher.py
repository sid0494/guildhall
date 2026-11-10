"""Sends queued command replies to Discord (docs/adr/0064).

Each reply edits the "thinking..." message through the interaction's webhook;
the interaction token is the credential, so no bot token is needed. Editing is
idempotent, so a reply sent twice (sent, but not yet marked sent) is harmless.
Tokens last 15 minutes: a reply still pending after 14 is abandoned.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from enum import Enum, auto
from typing import Any, Final

import httpx
import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.platform.discord import API_URL, USER_AGENT
from guildhall.platform.replies import outbox_replies

REPLY_DEADLINE: Final = timedelta(minutes=14)
RETENTION: Final = timedelta(days=1)
MAX_RATE_LIMIT_PAUSE: Final = 60.0
MAX_RETRY_PAUSE: Final = 30.0
"""After a failed send, the pass waits 2, 4, 8... seconds, up to this."""
NO_MENTIONS: Final[dict[str, list[str]]] = {"parse": []}
"""Replies can hold user text: never let one ping @everyone, roles or users."""

_log = structlog.get_logger(__name__)


class ReplySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    reply_batch_size: int = Field(default=20, gt=0)
    reply_max_attempts: int = Field(default=5, gt=0)
    """Failed sends (Discord 5xx, timeouts) before a reply is abandoned."""


class _Sent(Enum):
    SENT = auto()
    GIVE_UP = auto()
    TRY_AGAIN = auto()
    RATE_LIMITED = auto()


@asynccontextmanager
async def open_interaction_webhooks() -> AsyncIterator[httpx.AsyncClient]:
    """A client for interaction webhooks: no Authorization header at all."""
    async with httpx.AsyncClient(
        base_url=API_URL, headers={"User-Agent": USER_AGENT}, timeout=10
    ) as client:
        yield client


class ReplyDispatcher:
    name = "replies"

    def __init__(
        self,
        engine: AsyncEngine,
        client: httpx.AsyncClient,
        settings: ReplySettings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._client = client
        self._settings = settings
        self._sleep = sleep

    async def dispatch_once(self) -> int:
        """Handle one batch of pending replies; returns how many were settled.

        Rows are locked with SKIP LOCKED, so several workers never handle the
        same reply. After a 429, the pass stops and waits Discord's
        `retry_after` once its transaction has ended.
        """
        pause = 0.0
        settled = 0
        async with self._engine.connect() as connection, connection.begin():
            now = await connection.scalar(select(func.now()))
            rows = (
                await connection.execute(
                    select(outbox_replies)
                    .where(
                        outbox_replies.c.sent_at.is_(None),
                        outbox_replies.c.abandoned_at.is_(None),
                    )
                    .order_by(outbox_replies.c.id)
                    .limit(self._settings.reply_batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                if now - row.created_at > REPLY_DEADLINE:
                    await _abandon(connection, row.id, "expired")
                    settled += 1
                    continue
                result, detail = await self._send(row)
                if result is _Sent.RATE_LIMITED:
                    pause = min(detail, MAX_RATE_LIMIT_PAUSE)
                    _log.warning("discord rate limit", seconds=pause)
                    break
                settled += await self._record(connection, row, result, detail)
                if result is _Sent.TRY_AGAIN:
                    pause = max(pause, min(2.0 ** (row.attempts + 1), MAX_RETRY_PAUSE))
        if pause:
            await self._sleep(pause)
        return settled

    async def _send(self, row: Any) -> tuple[_Sent, Any]:
        # The path holds the interaction token: never log it, nor the URL.
        path = (
            f"/webhooks/{row.application_id}/{row.interaction_token}/messages/@original"
        )
        try:
            response = await self._client.patch(
                path, json=row.message | {"allowed_mentions": NO_MENTIONS}
            )
        except httpx.HTTPError as error:
            return _Sent.TRY_AGAIN, type(error).__name__
        if response.is_success:
            return _Sent.SENT, response.status_code
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            return _Sent.RATE_LIMITED, _retry_after(response)
        if response.status_code >= 500:
            return _Sent.TRY_AGAIN, response.status_code
        return _Sent.GIVE_UP, response.status_code

    async def _record(
        self, connection: AsyncConnection, row: Any, result: _Sent, detail: Any
    ) -> int:
        if result is _Sent.SENT:
            await connection.execute(
                update(outbox_replies)
                .where(outbox_replies.c.id == row.id)
                .values(sent_at=func.now(), attempts=row.attempts + 1)
            )
            _log.info("reply sent", reply=row.id)
            return 1
        if result is _Sent.GIVE_UP:
            await _abandon(connection, row.id, f"discord answered {detail}")
            return 1
        attempts = row.attempts + 1
        if attempts >= self._settings.reply_max_attempts:
            await _abandon(connection, row.id, f"failed {attempts} times: {detail}")
            return 1
        await connection.execute(
            update(outbox_replies)
            .where(outbox_replies.c.id == row.id)
            .values(attempts=attempts)
        )
        _log.warning("reply failed, will retry", reply=row.id, error=str(detail))
        return 0


async def _abandon(connection: AsyncConnection, reply_id: int, reason: str) -> None:
    await connection.execute(
        update(outbox_replies)
        .where(outbox_replies.c.id == reply_id)
        .values(abandoned_at=func.now())
    )
    _log.warning("reply abandoned", reply=reply_id, reason=reason)


def _retry_after(response: httpx.Response) -> float:
    """Discord's wait, from the body or the header; a second if neither."""
    try:
        return float(response.json()["retry_after"])
    except ValueError, KeyError, TypeError:
        pass
    try:
        return float(response.headers.get("retry-after", 1))
    except ValueError:
        return 1.0


class ReplyPruner:
    """Deletes replies sent or abandoned over a day ago, tokens and all."""

    name = "outbox_replies"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def prune(self) -> int:
        cutoff = func.now() - RETENTION
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(outbox_replies).where(
                    or_(
                        outbox_replies.c.sent_at < cutoff,
                        outbox_replies.c.abandoned_at < cutoff,
                    )
                )
            )
        return result.rowcount
