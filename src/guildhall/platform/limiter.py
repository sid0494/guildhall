"""Per-plug-in upstream budgets shared by every worker replica (docs/adr/0034).

Each plug-in's `RateLimitPolicy` is two moving windows in Redis, kept by the
`limits` library: at most `per_minute` calls in any 60 seconds and `per_day`
in any 24 hours. A `429` from the upstream sets a cooldown key that pauses the
plug-in's calls on every replica until it expires.

Redis being unreachable raises `LimiterUnavailableError`; callers fail closed
and make no call (docs/adr/0033).
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from limits import RateLimitItem, RateLimitItemPerDay, RateLimitItemPerMinute
from limits.aio.storage import RedisStorage
from limits.aio.strategies import MovingWindowRateLimiter
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

from guildhall.contract import RateLimitPolicy


class LimiterUnavailableError(Exception):
    """The shared budget could not be checked, so no call may go out."""


class RateLimiter(Protocol):
    """Shared upstream budgets; `RedisRateLimiter` provides it."""

    async def acquire(self, plugin: str, policy: RateLimitPolicy) -> float:
        """Take one call from the budget: 0 if taken, else seconds to wait first."""
        ...

    async def cool_down(self, plugin: str, seconds: float) -> None:
        """Pause the plug-in's calls everywhere for `seconds`."""
        ...


class RedisRateLimiter:
    def __init__(self, redis: Redis, storage: RedisStorage, prefix: str) -> None:
        self._redis = redis
        self._windows = MovingWindowRateLimiter(storage)
        self._prefix = prefix

    async def acquire(self, plugin: str, policy: RateLimitPolicy) -> float:
        try:
            cooling_ms = await self._redis.pttl(self._cooldown_key(plugin))
            if cooling_ms > 0:
                return cooling_ms / 1000
            # Minute first: when the day refuses, one minute slot is wasted,
            # which errs towards fewer calls, never more.
            for window in (
                RateLimitItemPerMinute(policy.per_minute),
                RateLimitItemPerDay(policy.per_day),
            ):
                if not await self._windows.hit(window, plugin):
                    return await self._wait_for(window, plugin)
        except (RedisError, OSError) as error:
            raise LimiterUnavailableError(type(error).__name__) from error
        return 0

    async def cool_down(self, plugin: str, seconds: float) -> None:
        try:
            await self._redis.set(
                self._cooldown_key(plugin), "1", px=max(1, round(seconds * 1000))
            )
        except (RedisError, OSError) as error:
            raise LimiterUnavailableError(type(error).__name__) from error

    async def _wait_for(self, window: RateLimitItem, plugin: str) -> float:
        stats = await self._windows.get_window_stats(window, plugin)
        return max(0.001, stats.reset_time - time.time())

    def _cooldown_key(self, plugin: str) -> str:
        return f"{self._prefix}:cooldown:{plugin}"


@asynccontextmanager
async def open_limiter(
    redis_url: str, prefix: str = "guildhall"
) -> AsyncIterator[RedisRateLimiter]:
    """A limiter on one connection pool; connects lazily, so Redis may be down."""
    pool = ConnectionPool.from_url(redis_url)
    redis = Redis(connection_pool=pool)
    storage = RedisStorage(
        "async+" + redis_url,
        implementation="redispy",
        key_prefix=f"{prefix}:limits",
        # A documented option, though `**options` is annotated as scalars only.
        connection_pool=pool,  # type: ignore[arg-type]
    )
    try:
        yield RedisRateLimiter(redis, storage, prefix)
    finally:
        await redis.aclose()
        await pool.aclose()
