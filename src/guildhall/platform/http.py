"""The platform's side of `Context.http` (docs/adr/0033, 0034, 0035).

Before each request, the plug-in's shared budget is checked. A short wait
happens in place; a longer one, a `429` or an unreachable limiter raises
`ThrottledError`, and the runner defers the job. Errors and log lines name the
method, host and path only, never the query string or headers, which is where
API keys go; all-digit path segments, often personal IDs, are masked
(docs/adr/0050).
"""

import asyncio
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

from guildhall.contract import HttpResponse, RetryableError, SourcePlugin
from guildhall.contract.http import Params
from guildhall.platform.limiter import (
    LimiterUnavailableError,
    RateLimiter,
    open_limiter,
)

_RANDOM = random.SystemRandom()
_log = structlog.get_logger(__name__)

USER_AGENT = "guildhall/0.1"
LIMITER_DOWN_DEFER_SECONDS = (30.0, 60.0)
"""Range a job is deferred by, at random, while Redis is unreachable."""


class HttpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    redis_url: str = "redis://127.0.0.1:6379/0"
    http_timeout_seconds: float = 10
    """Per request; kept well inside the job timeout."""
    rate_limit_max_wait_seconds: float = 5
    """Longest a handler waits for its budget before the job is deferred."""
    default_cooldown_seconds: float = 60
    """How long a `429` without a usable Retry-After pauses the plug-in."""


class ThrottledError(Exception):
    """The call cannot go out soon; the runner defers the job (docs/adr/0033).

    Not a failure: the job keeps its attempt number and is never dead-lettered.
    """

    def __init__(self, retry_after: float, reason: str) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds from a Retry-After header (seconds or an HTTP date), if usable."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    seconds = (moment - (now or datetime.now(UTC))).total_seconds()
    return max(0.0, seconds)


def describe(request: httpx.Request) -> str:
    """Method, host and path: never the query string, where keys go."""
    return f"{request.method} {request.url.host}{masked_path(request.url)}"


def masked_path(url: httpx.URL) -> str:
    """The path with each all-digit segment as `{n}`.

    Such segments are usually IDs, and some are personal, like the Dota account
    in `/players/123456789/matches`. A job's log lines still carry its dedupe
    key, which says what the call was for.
    """
    return "/".join(
        "{n}" if segment.isdecimal() else segment for segment in url.path.split("/")
    )


class PluginHttp:
    """`Context.http` for one plug-in."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        plugin: SourcePlugin,
        settings: HttpSettings,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._plugin = plugin
        self._settings = settings

    async def get(self, url: str, *, params: Params | None = None) -> HttpResponse:
        return await self._send(self._client.build_request("GET", url, params=params))

    async def post(
        self, url: str, *, params: Params | None = None, json: Any = None
    ) -> HttpResponse:
        return await self._send(
            self._client.build_request("POST", url, params=params, json=json)
        )

    async def _send(self, request: httpx.Request) -> HttpResponse:
        where = describe(request)
        await self._take_from_budget(where)
        started = time.monotonic()
        try:
            response = await self._client.send(request)
        except httpx.TimeoutException as error:
            raise RetryableError(f"{where} timed out") from error
        except httpx.TransportError as error:
            raise RetryableError(f"{where} failed: {type(error).__name__}") from error
        _log.info(
            "upstream call",
            method=request.method,
            host=request.url.host,
            path=masked_path(request.url),
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000),
        )

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            await self._cool_down(response)
        if response.status_code >= 500:
            raise RetryableError(f"{where} answered {response.status_code}")
        return HttpResponse(
            status=response.status_code,
            headers={name.lower(): value for name, value in response.headers.items()},
            body=response.content,
        )

    async def _take_from_budget(self, where: str) -> None:
        """Wait for a slot if it is near; otherwise defer the job."""
        waited = 0.0
        while True:
            try:
                wait = await self._limiter.acquire(
                    self._plugin.name, self._plugin.rate_limit
                )
            except LimiterUnavailableError as error:
                _log.warning("rate limiter unavailable", error=str(error))
                low, high = LIMITER_DOWN_DEFER_SECONDS
                raise ThrottledError(
                    _RANDOM.uniform(low, high), f"rate limiter unavailable: {error}"
                ) from error
            if wait == 0:
                return
            if waited + wait > self._settings.rate_limit_max_wait_seconds:
                raise ThrottledError(wait, f"rate limit reached before {where}")
            await asyncio.sleep(wait)
            waited += wait

    async def _cool_down(self, response: httpx.Response) -> None:
        seconds = parse_retry_after(response.headers.get("retry-after"))
        if seconds is None:
            seconds = self._settings.default_cooldown_seconds
        try:
            await self._limiter.cool_down(self._plugin.name, seconds)
        except LimiterUnavailableError as error:
            _log.warning("rate limiter unavailable", error=str(error))
        raise ThrottledError(
            seconds, f"{describe(response.request)} answered 429 (Too Many Requests)"
        )


class UpstreamHttp:
    """Builds each plug-in's `Context.http` on one shared client and limiter."""

    def __init__(
        self, client: httpx.AsyncClient, limiter: RateLimiter, settings: HttpSettings
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._settings = settings

    def for_plugin(self, plugin: SourcePlugin) -> PluginHttp:
        return PluginHttp(self._client, self._limiter, plugin, self._settings)


def new_client(
    settings: HttpSettings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The shared client: timeout, User-Agent, and no redirects followed."""
    return httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
        transport=transport,
    )


@asynccontextmanager
async def open_upstream_http(settings: HttpSettings) -> AsyncIterator[UpstreamHttp]:
    """Upstream HTTP for every plug-in; Redis may be down, which fails closed."""
    async with (
        new_client(settings) as client,
        open_limiter(settings.redis_url) as limiter,
    ):
        yield UpstreamHttp(client, limiter, settings)
