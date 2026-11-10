"""The event dispatcher against real Postgres and a fake Discord."""

import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any, Literal

import httpx
import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.contract import (
    Event,
    JobType,
    Message,
    RateLimitPolicy,
    SourcePlugin,
)
from guildhall.platform.event_dispatcher import (
    EventDispatcher,
    EventPruner,
    PostSettings,
)
from guildhall.platform.outbox import add_events, outbox_events

CHANNEL = "chan-1"
type Discord = Callable[[httpx.Request], httpx.Response]


class Said(Event):
    type: Literal["posting.said"] = "posting.said"
    schema_version: Literal[1] = 1
    text: str
    render_as: Literal["post", "nothing", "crash", "hang"] = "post"


class Posting(SourcePlugin):
    name = "posting"
    rate_limit = RateLimitPolicy(per_minute=60, per_day=1000)

    def job_types(self) -> list[JobType[Any]]:
        return []

    def event_types(self) -> list[type[Event]]:
        return [Said]

    def render(self, event: Event) -> Message | None:
        assert isinstance(event, Said)
        match event.render_as:
            case "nothing":
                return None
            case "crash":
                raise RuntimeError("bug in render")
            case "hang":
                time.sleep(1)  # a plug-in doing blocking I/O it must not do
        return Message(content=f"<@42> {event.text}", mentions=("42",))


@pytest.fixture(autouse=True)
async def empty(engine: AsyncEngine) -> AsyncIterator[None]:
    """The dispatcher takes any pending event: start and end with none."""
    async with engine.begin() as connection:
        await connection.execute(delete(outbox_events))
    yield
    async with engine.begin() as connection:
        await connection.execute(delete(outbox_events))


async def record(engine: AsyncEngine, **fields: Any) -> int:
    event = Said(dedupe_key=f"posting:said:{uuid.uuid4().hex}", **fields)
    async with engine.begin() as connection:
        await add_events(connection, "posting", [event])
        return int(await connection.scalar(select(func.max(outbox_events.c.id))) or 0)


async def stored(engine: AsyncEngine, event_id: int) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                select(outbox_events).where(outbox_events.c.id == event_id)
            )
        ).one()


class Harness:
    def __init__(
        self, engine: AsyncEngine, discord: Discord, plugins: Any = None
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

        def recorded(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return discord(request)

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        client = httpx.AsyncClient(
            base_url="https://discord.com/api/v10",
            transport=httpx.MockTransport(recorded),
        )
        settings = PostSettings(
            event_max_attempts=5, render_timeout_seconds=0.2, _env_file=None
        )
        self.dispatcher = EventDispatcher(
            engine,
            client,
            {"posting": Posting()} if plugins is None else plugins,
            CHANNEL,
            settings,
            sleep=sleep,
        )


def answer(status: int, body: Any = None) -> Discord:
    return lambda request: httpx.Response(status, json=body or {})


async def test_an_event_is_posted_once_with_a_nonce_and_its_message_id_kept(
    engine: AsyncEngine,
) -> None:
    event_id = await record(engine, text="report")
    harness = Harness(engine, answer(200, {"id": "9001"}))
    assert await harness.dispatcher.dispatch_once() == 1
    [request] = harness.requests
    assert (request.method, request.url.path) == (
        "POST",
        f"/api/v10/channels/{CHANNEL}/messages",
    )
    assert json.loads(request.content) == {
        "content": "<@42> report",
        "allowed_mentions": {"parse": [], "users": ["42"]},
        "nonce": f"evt-{event_id}",
        "enforce_nonce": True,
    }
    row = await stored(engine, event_id)
    assert (row.discord_message_id, row.attempts, row.abandoned_at) == ("9001", 1, None)
    assert row.sent_at is not None
    assert await harness.dispatcher.dispatch_once() == 0  # nothing left to post


async def test_an_event_rendered_as_nothing_is_settled_without_posting(
    engine: AsyncEngine,
) -> None:
    event_id = await record(engine, text="quiet", render_as="nothing")
    harness = Harness(engine, answer(200))
    assert await harness.dispatcher.dispatch_once() == 1
    assert harness.requests == []
    row = await stored(engine, event_id)
    assert (row.sent_at is not None, row.discord_message_id) == (True, None)


@pytest.mark.parametrize("render_as", ["crash", "hang"])
async def test_a_render_that_fails_or_hangs_abandons_only_its_event(
    engine: AsyncEngine, render_as: str
) -> None:
    broken = await record(engine, text="x", render_as=render_as)
    fine = await record(engine, text="fine")
    harness = Harness(engine, answer(200, {"id": "1"}))
    assert await harness.dispatcher.dispatch_once() == 2
    assert (await stored(engine, broken)).abandoned_at is not None
    assert (await stored(engine, fine)).sent_at is not None
    assert len(harness.requests) == 1


async def test_events_that_cannot_be_rebuilt_are_abandoned_unposted(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            insert(outbox_events),
            [
                {
                    "plugin": "posting",
                    "dedupe_key": "posting:a",
                    "type": "posting.unknown",
                    "schema_version": 1,
                    "payload": {},
                },
                {
                    "plugin": "posting",
                    "dedupe_key": "posting:b",
                    "type": "posting.said",
                    "schema_version": 1,
                    "payload": {"type": "posting.said"},
                },
                {
                    "plugin": "gone",
                    "dedupe_key": "gone:c",
                    "type": "gone.said",
                    "schema_version": 1,
                    "payload": {},
                },
            ],
        )
    harness = Harness(engine, answer(200))
    assert await harness.dispatcher.dispatch_once() == 3
    assert harness.requests == []
    async with engine.connect() as connection:
        abandoned = await connection.scalar(
            select(func.count()).where(outbox_events.c.abandoned_at.is_not(None))
        )
    assert abandoned == 3


async def test_a_post_discord_refuses_is_abandoned(engine: AsyncEngine) -> None:
    event_id = await record(engine, text="x")
    harness = Harness(engine, answer(403, {"message": "Missing Permissions"}))
    assert await harness.dispatcher.dispatch_once() == 1
    assert (await stored(engine, event_id)).abandoned_at is not None


async def test_a_failed_post_is_retried_after_a_pause(engine: AsyncEngine) -> None:
    event_id = await record(engine, text="x")
    harness = Harness(engine, answer(502))
    assert await harness.dispatcher.dispatch_once() == 0
    row = await stored(engine, event_id)
    assert (row.attempts, row.sent_at, row.abandoned_at) == (1, None, None)
    assert harness.sleeps == [2.0]


async def test_a_rate_limit_stops_the_pass(engine: AsyncEngine) -> None:
    first, second = await record(engine, text="a"), await record(engine, text="b")
    harness = Harness(engine, answer(429, {"retry_after": 3}))
    assert await harness.dispatcher.dispatch_once() == 0
    assert len(harness.requests) == 1
    assert harness.sleeps == [3.0]
    for event_id in (first, second):
        assert (await stored(engine, event_id)).sent_at is None


async def test_an_event_a_day_old_is_abandoned_not_posted_late(
    engine: AsyncEngine,
) -> None:
    event_id = await record(engine, text="old")
    async with engine.begin() as connection:
        await connection.execute(
            update(outbox_events)
            .where(outbox_events.c.id == event_id)
            .values(created_at=func.now() - timedelta(hours=25))
        )
    harness = Harness(engine, answer(200))
    assert await harness.dispatcher.dispatch_once() == 1
    assert harness.requests == []
    assert (await stored(engine, event_id)).abandoned_at is not None


async def test_the_pruner_removes_settled_events_a_week_old(
    engine: AsyncEngine,
) -> None:
    week_old = func.now() - timedelta(days=7, minutes=1)
    old_sent, old_abandoned, recent, pending = [
        await record(engine, text=str(n)) for n in range(4)
    ]
    async with engine.begin() as connection:
        for event_id, values in (
            (old_sent, {"sent_at": week_old}),
            (old_abandoned, {"abandoned_at": week_old}),
            (recent, {"sent_at": func.now()}),
        ):
            await connection.execute(
                update(outbox_events)
                .where(outbox_events.c.id == event_id)
                .values(**values)
            )
    assert await EventPruner(engine).prune() == 2
    async with engine.connect() as connection:
        left = set(await connection.scalars(select(outbox_events.c.id)))
    assert left == {recent, pending}
