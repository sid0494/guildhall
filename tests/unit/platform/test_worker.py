"""Unit tests for the worker's loops: adaptive relay polling, recovery in place."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from guildhall.platform.worker import Worker, WorkerSettings

type LogLines = Callable[[], list[dict[str, Any]]]


@dataclass
class FakeConsumer:
    running: bool = False

    async def consume(self) -> None:
        self.running = True
        await asyncio.Event().wait()  # until cancelled


@dataclass
class FakeRelay:
    """Each pass takes the next scripted result; an exception is raised instead."""

    passes: list[int | Exception] = field(default_factory=list)
    prunes: list[int | Exception] = field(default_factory=list)
    relay_calls: int = 0
    prune_calls: int = 0

    async def relay_once(self) -> int:
        self.relay_calls += 1
        return self._next(self.passes)

    async def prune(self) -> int:
        self.prune_calls += 1
        return self._next(self.prunes)

    @staticmethod
    def _next(script: list[int | Exception]) -> int:
        result = script.pop(0) if script else 0
        if isinstance(result, Exception):
            raise result
        return result


async def until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not condition():  # noqa: ASYNC110 - polls fakes' plain state
            await asyncio.sleep(0.001)


@pytest.fixture
def consumer() -> FakeConsumer:
    return FakeConsumer()


@pytest.fixture
def relay() -> FakeRelay:
    return FakeRelay()


@pytest.fixture
async def start(
    consumer: FakeConsumer, relay: FakeRelay
) -> AsyncIterator[Callable[..., None]]:
    """Start a worker with the given timings; cancel it after the test."""
    tasks: list[asyncio.Task[None]] = []

    def start_worker(
        relay_idle: float = 10, prune_interval: float = 10, error_pause: float = 0
    ) -> None:
        worker = Worker(
            consumer,
            relay,
            WorkerSettings(prune_interval_seconds=prune_interval),
            relay_idle_seconds=relay_idle,
            error_pause_seconds=error_pause,
        )
        tasks.append(asyncio.create_task(worker.run()))

    yield start_worker
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_runs_the_consumer_relay_and_prune_together(
    consumer: FakeConsumer, relay: FakeRelay, start: Callable[..., None]
) -> None:
    start()
    await until(
        lambda: consumer.running and relay.relay_calls > 0 and relay.prune_calls > 0
    )


async def test_relay_goes_again_at_once_while_there_is_work(
    relay: FakeRelay, start: Callable[..., None]
) -> None:
    relay.passes = [100, 100, 7, 0, 5]
    start(relay_idle=10)
    await until(lambda: relay.relay_calls == 4)
    await asyncio.sleep(0.02)
    assert relay.relay_calls == 4  # idle after the empty pass, not polling


async def test_a_failed_relay_pass_is_logged_and_the_loop_goes_on(
    relay: FakeRelay, start: Callable[..., None], log_lines: LogLines
) -> None:
    relay.passes = [ConnectionError("database down"), 3, 0]
    start()
    await until(lambda: relay.relay_calls == 3)
    failed = [line for line in log_lines() if line["event"] == "relay pass failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "warning"
    assert "database down" in failed[0]["exception"]


async def test_prune_runs_at_start_up_and_then_every_interval(
    relay: FakeRelay, start: Callable[..., None]
) -> None:
    start(prune_interval=0.01)
    await until(lambda: relay.prune_calls >= 3)


async def test_a_failed_prune_waits_for_the_next_interval(
    relay: FakeRelay, start: Callable[..., None], log_lines: LogLines
) -> None:
    relay.prunes = [ConnectionError("database down")]
    start(prune_interval=10)
    await until(lambda: relay.prune_calls == 1)
    await asyncio.sleep(0.02)
    assert relay.prune_calls == 1
    assert [line["event"] for line in log_lines()].count("prune failed") == 1


async def test_failing_loops_do_not_stop_the_others(
    consumer: FakeConsumer, relay: FakeRelay, start: Callable[..., None]
) -> None:
    relay.passes = [RuntimeError("bug")] * 5
    relay.prunes = [RuntimeError("bug")] * 5
    start(prune_interval=0.001)
    await until(lambda: relay.relay_calls >= 5 and relay.prune_calls >= 5)
    assert consumer.running
    await until(lambda: relay.relay_calls >= 6)  # still going after the failures
