"""The shared upstream budget against real Redis (ADR 0034).

Two limiters with their own connection pools stand in for two worker replicas.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from guildhall.contract import RateLimitPolicy
from guildhall.platform.limiter import (
    LimiterUnavailableError,
    RedisRateLimiter,
    open_limiter,
)

REDIS_URL = "redis://127.0.0.1:6379/0"
POLICY = RateLimitPolicy(per_minute=3, per_day=1_000)


@pytest.fixture
async def prefix() -> AsyncIterator[str]:
    """A key prefix unique to this test; its keys are deleted afterwards."""
    prefix = f"test-{uuid.uuid4().hex[:12]}"
    yield prefix
    async with Redis.from_url(REDIS_URL) as redis:
        keys = [key async for key in redis.scan_iter(f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)


@pytest.fixture
async def replica_a(prefix: str) -> AsyncIterator[RedisRateLimiter]:
    async with open_limiter(REDIS_URL, prefix) as limiter:
        yield limiter


@pytest.fixture
async def replica_b(prefix: str) -> AsyncIterator[RedisRateLimiter]:
    async with open_limiter(REDIS_URL, prefix) as limiter:
        yield limiter


async def test_the_budget_is_shared_by_every_replica(
    replica_a: RedisRateLimiter, replica_b: RedisRateLimiter
) -> None:
    waits = [
        await replica_a.acquire("gank_report", POLICY),
        await replica_b.acquire("gank_report", POLICY),
        await replica_a.acquire("gank_report", POLICY),
        await replica_b.acquire("gank_report", POLICY),
    ]
    assert waits[:3] == [0, 0, 0]
    assert 59 < waits[3] <= 60  # the first call leaves the window in a minute


async def test_each_plugin_has_its_own_budget(replica_a: RedisRateLimiter) -> None:
    for _ in range(3):
        await replica_a.acquire("gank_report", POLICY)
    assert await replica_a.acquire("side_quest", POLICY) == 0


async def test_the_daily_budget_applies_when_minutes_are_free(
    replica_a: RedisRateLimiter,
) -> None:
    policy = RateLimitPolicy(per_minute=100, per_day=2)
    assert [await replica_a.acquire("gank_report", policy) for _ in range(2)] == [0, 0]
    wait = await replica_a.acquire("gank_report", policy)
    assert 86_399 < wait <= 86_400


async def test_a_cooldown_pauses_the_plugin_on_every_replica(
    replica_a: RedisRateLimiter, replica_b: RedisRateLimiter
) -> None:
    await replica_a.cool_down("gank_report", 120)
    wait = await replica_b.acquire("gank_report", POLICY)
    assert 119 < wait <= 120
    assert await replica_b.acquire("side_quest", POLICY) == 0


async def test_a_cooldown_ends_by_itself(replica_a: RedisRateLimiter) -> None:
    await replica_a.cool_down("gank_report", 0.05)
    assert 0 < await replica_a.acquire("gank_report", POLICY) <= 0.05
    await asyncio.sleep(0.1)
    assert await replica_a.acquire("gank_report", POLICY) == 0


async def test_unreachable_redis_is_reported_not_ignored() -> None:
    async with open_limiter("redis://127.0.0.1:1/0") as limiter:
        with pytest.raises(LimiterUnavailableError, match="ConnectionError"):
            await limiter.acquire("gank_report", POLICY)
        with pytest.raises(LimiterUnavailableError):
            await limiter.cool_down("gank_report", 60)
