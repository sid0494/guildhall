"""`Context.http` with a mock transport and an in-memory limiter (ADR 0033-0035)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from guildhall.contract import RateLimitPolicy, RetryableError
from guildhall.platform.http import (
    USER_AGENT,
    HttpSettings,
    PluginHttp,
    ThrottledError,
    new_client,
    parse_retry_after,
)
from guildhall.platform.limiter import LimiterUnavailableError
from guildhall.plugins.synthetic import SyntheticPlugin

QUERY_VALUE = "query-value-123"
"""Stands in for an API key, which travels in the query string."""


@dataclass
class MemoryLimiter:
    """Answers with queued waits (0 once they run out); records cooldowns."""

    waits: list[float] = field(default_factory=list)
    down: bool = False
    sharing_fails: bool = False
    acquired: list[tuple[str, RateLimitPolicy]] = field(default_factory=list)
    cooldowns: list[tuple[str, float]] = field(default_factory=list)

    async def acquire(self, plugin: str, policy: RateLimitPolicy) -> float:
        if self.down:
            raise LimiterUnavailableError("ConnectionError")
        self.acquired.append((plugin, policy))
        return self.waits.pop(0) if self.waits else 0

    async def cool_down(self, plugin: str, seconds: float) -> None:
        if self.down or self.sharing_fails:
            raise LimiterUnavailableError("ConnectionError")
        self.cooldowns.append((plugin, seconds))


type Upstream = Callable[[httpx.Request], httpx.Response]


@dataclass
class Harness:
    limiter: MemoryLimiter
    requests: list[httpx.Request]
    http: PluginHttp


def harness(upstream: Upstream, limiter: MemoryLimiter | None = None) -> Harness:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return upstream(request)

    limiter = limiter or MemoryLimiter()
    settings = HttpSettings(rate_limit_max_wait_seconds=0.05)
    client = new_client(settings, transport=httpx.MockTransport(record))
    return Harness(
        limiter, requests, PluginHttp(client, limiter, SyntheticPlugin(), settings)
    )


def answer(status: int, **kwargs: Any) -> Upstream:
    return lambda request: httpx.Response(status, **kwargs)


async def test_a_response_is_returned_as_received() -> None:
    h = harness(answer(200, content=b'{"gpm": 512.0}', headers={"X-Thing": "1"}))
    response = await h.http.get("https://api.example.test/matches/1")
    assert (response.status, response.body) == (200, b'{"gpm": 512.0}')
    assert response.headers["x-thing"] == "1"
    assert response.json() == {"gpm": 512.0}


async def test_every_call_takes_from_the_plugins_budget() -> None:
    h = harness(answer(200))
    await h.http.get("https://api.example.test/a")
    await h.http.post("https://api.example.test/b")
    assert h.limiter.acquired == [("synthetic", SyntheticPlugin.rate_limit)] * 2


async def test_params_json_and_the_user_agent_are_sent() -> None:
    h = harness(answer(200))
    await h.http.post("https://api.example.test/request/1", params={"n": 2}, json=[1])
    (request,) = h.requests
    assert request.url.params["n"] == "2"
    assert request.content == b"[1]"
    assert request.headers["user-agent"] == USER_AGENT


async def test_client_errors_are_returned_for_the_plugin_to_judge() -> None:
    h = harness(answer(404))
    assert (await h.http.get("https://api.example.test/matches/1")).status == 404


async def test_server_errors_are_retryable_and_never_show_the_query() -> None:
    h = harness(answer(503))
    with pytest.raises(RetryableError) as raised:
        await h.http.get("https://api.example.test/x", params={"key": QUERY_VALUE})
    assert str(raised.value) == "GET api.example.test/x answered 503"


async def test_a_timeout_is_retryable() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(RetryableError, match=r"^GET api.example.test/x timed out$"):
        await harness(slow).http.get("https://api.example.test/x?key=" + QUERY_VALUE)


async def test_a_connection_failure_is_retryable() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RetryableError, match="failed: ConnectError"):
        await harness(refused).http.get("https://api.example.test/x")


async def test_a_429_cools_the_plugin_down_everywhere_and_defers() -> None:
    h = harness(answer(429, headers={"Retry-After": "120"}))
    with pytest.raises(ThrottledError) as raised:
        await h.http.get("https://api.example.test/x", params={"key": QUERY_VALUE})
    assert raised.value.retry_after == 120
    assert (
        str(raised.value) == "GET api.example.test/x answered 429 (Too Many Requests)"
    )
    assert h.limiter.cooldowns == [("synthetic", 120)]


async def test_a_429_without_retry_after_cools_down_for_the_default() -> None:
    h = harness(answer(429))
    with pytest.raises(ThrottledError):
        await h.http.get("https://api.example.test/x")
    assert h.limiter.cooldowns == [("synthetic", 60)]


async def test_a_429_still_defers_when_the_cooldown_cannot_be_shared() -> None:
    h = harness(
        answer(429, headers={"Retry-After": "5"}), MemoryLimiter(sharing_fails=True)
    )
    with pytest.raises(ThrottledError):
        await h.http.get("https://api.example.test/x")


async def test_a_near_slot_is_waited_for_in_place() -> None:
    h = harness(answer(200), MemoryLimiter(waits=[0.02, 0.02]))
    assert (await h.http.get("https://api.example.test/x")).status == 200
    assert len(h.limiter.acquired) == 3


async def test_a_far_slot_defers_the_job_without_calling() -> None:
    h = harness(answer(200), MemoryLimiter(waits=[60]))
    with pytest.raises(ThrottledError) as raised:
        await h.http.get("https://api.example.test/x", params={"key": QUERY_VALUE})
    assert raised.value.retry_after == 60
    assert str(raised.value) == "rate limit reached before GET api.example.test/x"
    assert h.requests == []


async def test_waits_add_up_towards_the_limit() -> None:
    h = harness(answer(200), MemoryLimiter(waits=[0.03, 0.03]))
    with pytest.raises(ThrottledError):
        await h.http.get("https://api.example.test/x")
    assert h.requests == []


async def test_an_unreachable_limiter_fails_closed() -> None:
    h = harness(answer(200), MemoryLimiter(down=True))
    with pytest.raises(ThrottledError) as raised:
        await h.http.get("https://api.example.test/x")
    assert 30 <= raised.value.retry_after <= 60
    assert str(raised.value) == "rate limiter unavailable: ConnectionError"
    assert h.requests == []


@pytest.mark.parametrize(
    ("header", "seconds"),
    [
        ("120", 120.0),
        (" 7 ", 7.0),
        ("Mon, 09 Nov 2026 12:02:00 GMT", 120.0),
        ("Mon, 09 Nov 2026 11:00:00 GMT", 0.0),  # already past
        ("soon", None),
        ("-5", None),
        (None, None),
    ],
)
def test_retry_after_is_read_as_seconds_or_a_date(
    header: str | None, seconds: float | None
) -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    assert parse_retry_after(header, now=now) == seconds


async def test_each_call_is_logged_without_the_query_string(
    log_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    h = harness(answer(200))
    await h.http.get("https://api.example.test/x", params={"key": QUERY_VALUE})
    (line,) = [line for line in log_lines() if line["event"] == "upstream call"]
    assert (
        line.items()
        >= {
            "method": "GET",
            "host": "api.example.test",
            "path": "/x",
            "status": 200,
        }.items()
    )
    assert QUERY_VALUE not in str(log_lines())


PATH_ID = "123456789"
"""Stands in for a personal ID in a URL path, such as a Dota account ID."""


async def test_numeric_path_segments_are_masked_in_the_log(
    log_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    h = harness(answer(200))
    await h.http.get(f"https://api.example.test/api/players/{PATH_ID}/matches")
    (line,) = [line for line in log_lines() if line["event"] == "upstream call"]
    assert line["path"] == "/api/players/{n}/matches"
    assert PATH_ID not in str(log_lines())


async def test_numeric_path_segments_are_masked_in_errors() -> None:
    with pytest.raises(RetryableError) as raised:
        await harness(answer(500)).http.get(
            f"https://api.example.test/api/players/{PATH_ID}/matches"
        )
    assert str(raised.value) == (
        "GET api.example.test/api/players/{n}/matches answered 500"
    )


async def test_segments_that_are_not_all_digits_are_kept(
    log_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    h = harness(answer(200))
    await h.http.get("https://api.example.test/v2/items/abc123/0x1f")
    (line,) = [line for line in log_lines() if line["event"] == "upstream call"]
    assert line["path"] == "/v2/items/abc123/0x1f"
