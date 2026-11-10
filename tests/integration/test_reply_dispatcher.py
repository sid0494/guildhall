"""The reply dispatcher against real Postgres and a fake Discord."""

import json
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import Message
from guildhall.platform.replies import add_reply, outbox_replies
from guildhall.platform.reply_dispatcher import (
    ReplyDispatcher,
    ReplyPruner,
    ReplySettings,
)

TOKEN = "stand-in-interaction-token"
type Discord = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
async def empty(engine: AsyncEngine) -> AsyncIterator[None]:
    """The dispatcher takes any pending reply: start and end with none."""
    async with engine.begin() as connection:
        await connection.execute(delete(outbox_replies))
    yield
    async with engine.begin() as connection:
        await connection.execute(delete(outbox_replies))


async def queue_reply(engine: AsyncEngine, content: str = "hello", **row: Any) -> int:
    async with engine.begin() as connection:
        await add_reply(connection, "app-1", TOKEN, Message(content=content))
        reply_id = int(
            await connection.scalar(select(func.max(outbox_replies.c.id))) or 0
        )
        if row:
            await connection.execute(
                update(outbox_replies)
                .where(outbox_replies.c.id == reply_id)
                .values(**row)
            )
    return reply_id


async def stored(engine: AsyncEngine, reply_id: int) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                select(outbox_replies).where(outbox_replies.c.id == reply_id)
            )
        ).one()


class Harness:
    def __init__(self, engine: AsyncEngine, discord: Discord) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return discord(request)

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        client = httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            transport=httpx.MockTransport(record),
        )
        self.dispatcher = ReplyDispatcher(
            engine, client, ReplySettings(reply_max_attempts=5), sleep=sleep
        )


def answer(status: int, body: Any = None) -> Discord:
    return lambda request: httpx.Response(status, json=body or {})


async def test_a_reply_edits_the_original_message_without_pinging_anyone(
    engine: AsyncEngine, log_lines: Callable[[], list[dict[str, Any]]]
) -> None:
    reply_id = await queue_reply(engine, "hi @everyone")
    harness = Harness(engine, answer(200))
    assert await harness.dispatcher.dispatch_once() == 1
    [request] = harness.requests
    assert request.method == "PATCH"
    assert request.url.path == f"/api/v10/webhooks/app-1/{TOKEN}/messages/@original"
    assert json.loads(request.content) == {
        "content": "hi @everyone",
        "allowed_mentions": {"parse": []},
    }
    assert "authorization" not in request.headers  # the token in the path is enough
    row = await stored(engine, reply_id)
    assert (row.sent_at is not None, row.abandoned_at, row.attempts) == (True, None, 1)
    assert TOKEN not in str(log_lines())


async def test_a_reply_discord_refuses_is_abandoned(engine: AsyncEngine) -> None:
    reply_id = await queue_reply(engine)
    harness = Harness(engine, answer(404, {"message": "Unknown Webhook"}))
    assert await harness.dispatcher.dispatch_once() == 1
    row = await stored(engine, reply_id)
    assert (row.sent_at, row.abandoned_at is not None) == (None, True)


async def test_a_failed_send_is_retried_after_a_growing_pause(
    engine: AsyncEngine,
) -> None:
    reply_id = await queue_reply(engine)
    harness = Harness(engine, answer(502))
    assert await harness.dispatcher.dispatch_once() == 0
    assert await harness.dispatcher.dispatch_once() == 0
    row = await stored(engine, reply_id)
    assert (row.attempts, row.sent_at, row.abandoned_at) == (2, None, None)
    assert harness.sleeps == [2.0, 4.0]


async def test_a_reply_that_keeps_failing_is_abandoned(engine: AsyncEngine) -> None:
    reply_id = await queue_reply(engine, attempts=4)
    harness = Harness(engine, answer(500))
    assert await harness.dispatcher.dispatch_once() == 1
    assert (await stored(engine, reply_id)).abandoned_at is not None


async def test_a_reply_past_its_tokens_life_is_abandoned_unsent(
    engine: AsyncEngine,
) -> None:
    reply_id = await queue_reply(engine, created_at=func.now() - timedelta(minutes=15))
    harness = Harness(engine, answer(200))
    assert await harness.dispatcher.dispatch_once() == 1
    assert harness.requests == []
    assert (await stored(engine, reply_id)).abandoned_at is not None


async def test_a_rate_limit_stops_the_pass_and_waits_as_discord_asks(
    engine: AsyncEngine,
) -> None:
    first, second = await queue_reply(engine), await queue_reply(engine)
    harness = Harness(engine, answer(429, {"retry_after": 1.5}))
    assert await harness.dispatcher.dispatch_once() == 0
    assert len(harness.requests) == 1  # the second was not tried
    assert harness.sleeps == [1.5]
    for reply_id in (first, second):
        row = await stored(engine, reply_id)
        assert (row.attempts, row.sent_at, row.abandoned_at) == (0, None, None)


async def test_the_pruner_removes_settled_replies_a_day_old(
    engine: AsyncEngine,
) -> None:
    day_old = func.now() - timedelta(days=1, minutes=1)
    old_sent = await queue_reply(engine, sent_at=day_old)
    old_abandoned = await queue_reply(engine, abandoned_at=day_old)
    recent = await queue_reply(engine, sent_at=func.now())
    pending = await queue_reply(engine, created_at=day_old)
    assert await ReplyPruner(engine).prune() == 2
    async with engine.connect() as connection:
        left = set(await connection.scalars(select(outbox_replies.c.id)))
    assert left == {recent, pending}
    assert not left & {old_sent, old_abandoned}
