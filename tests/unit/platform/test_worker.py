"""Unit tests for the worker's loops: relay polling, recovery, stopping."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from guildhall.platform.worker import Worker, WorkerSettings

type LogLines = Callable[[], list[dict[str, Any]]]


@dataclass
class FakeConsumer:
    """Runs until stopped, then drains until `drained` is set."""

    running: bool = False
    drained: asyncio.Event = field(default_factory=asyncio.Event)

    async def consume(self, stop: asyncio.Event) -> None:
        self.running = True
        await stop.wait()
        await self.drained.wait()


@dataclass
class FakeRelay:
    """Each pass takes the next scripted result; an exception is raised instead."""

    passes: list[int | Exception] = field(default_factory=list)
    prunes: list[int | Exception] = field(default_factory=list)
    relay_calls: int = 0
    prune_calls: int = 0
    hang: bool = False

    async def relay_once(self) -> int:
        self.relay_calls += 1
        if self.hang:
            await asyncio.Event().wait()
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


@dataclass
class FakePruner:
    """Counts its runs; fails each run while `failing`."""

    name: str
    failing: bool = False
    calls: int = 0

    async def prune(self) -> int:
        self.calls += 1
        if self.failing:
            raise ConnectionError("database down")
        return 2


@dataclass
class FakeScheduler:
    """Records when it was asked; an exception in `fails` is raised instead."""

    calls: list[datetime] = field(default_factory=list)
    fails: list[Exception] = field(default_factory=list)

    async def fire_due(self, now: datetime) -> int:
        self.calls.append(now)
        if self.fails:
            raise self.fails.pop(0)
        return 0


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
def scheduler() -> FakeScheduler:
    return FakeScheduler()


@pytest.fixture
def stop() -> asyncio.Event:
    return asyncio.Event()


type Start = Callable[..., asyncio.Task[None]]


@pytest.fixture
async def start(
    consumer: FakeConsumer,
    relay: FakeRelay,
    scheduler: FakeScheduler,
    stop: asyncio.Event,
) -> AsyncIterator[Start]:
    """Start a worker with the given timings; cancel it after the test."""
    tasks: list[asyncio.Task[None]] = []

    def start_worker(
        relay_idle: float = 10,
        prune_interval: float = 10,
        schedule_interval: float = 10,
        error_pause: float = 0,
        flush: float = 1,
        pruners: tuple[FakePruner, ...] = (),
    ) -> asyncio.Task[None]:
        worker = Worker(
            consumer,
            relay,
            scheduler,
            WorkerSettings(
                prune_interval_seconds=prune_interval,
                schedule_interval_seconds=schedule_interval,
            ),
            pruners=pruners,
            relay_idle_seconds=relay_idle,
            error_pause_seconds=error_pause,
            flush_seconds=flush,
        )
        task = asyncio.create_task(worker.run(stop))
        tasks.append(task)
        return task

    yield start_worker
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_runs_the_consumer_relay_and_prune_together(
    consumer: FakeConsumer, relay: FakeRelay, start: Start
) -> None:
    start()
    await until(
        lambda: consumer.running and relay.relay_calls > 0 and relay.prune_calls > 0
    )


async def test_relay_goes_again_at_once_while_there_is_work(
    relay: FakeRelay, start: Start
) -> None:
    relay.passes = [100, 100, 7, 0, 5]
    start(relay_idle=10)
    await until(lambda: relay.relay_calls == 4)
    await asyncio.sleep(0.02)
    assert relay.relay_calls == 4  # idle after the empty pass, not polling


async def test_a_failed_relay_pass_is_logged_and_the_loop_goes_on(
    relay: FakeRelay, start: Start, log_lines: LogLines
) -> None:
    relay.passes = [ConnectionError("database down"), 3, 0]
    start()
    await until(lambda: relay.relay_calls == 3)
    failed = [line for line in log_lines() if line["event"] == "relay pass failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "warning"
    assert "database down" in failed[0]["exception"]


async def test_prune_runs_at_start_up_and_then_every_interval(
    relay: FakeRelay, start: Start
) -> None:
    start(prune_interval=0.01)
    await until(lambda: relay.prune_calls >= 3)


async def test_a_failed_prune_waits_for_the_next_interval(
    relay: FakeRelay, start: Start, log_lines: LogLines
) -> None:
    relay.prunes = [ConnectionError("database down")]
    start(prune_interval=10)
    await until(lambda: relay.prune_calls == 1)
    await asyncio.sleep(0.02)
    assert relay.prune_calls == 1
    assert [line["event"] for line in log_lines()].count("prune failed") == 1


async def test_failing_loops_do_not_stop_the_others(
    consumer: FakeConsumer, relay: FakeRelay, start: Start
) -> None:
    relay.passes = [RuntimeError("bug")] * 5
    relay.prunes = [RuntimeError("bug")] * 5
    start(prune_interval=0.001)
    await until(lambda: relay.relay_calls >= 5 and relay.prune_calls >= 5)
    assert consumer.running
    await until(lambda: relay.relay_calls >= 6)  # still going after the failures


async def test_relay_keeps_sending_while_the_consumer_drains(
    consumer: FakeConsumer, relay: FakeRelay, start: Start, stop: asyncio.Event
) -> None:
    start(relay_idle=0.001)
    await until(lambda: relay.relay_calls > 0)
    stop.set()
    calls = relay.relay_calls
    await until(lambda: relay.relay_calls > calls + 3)  # follow-ups of draining jobs
    consumer.drained.set()


async def test_after_draining_the_relay_flushes_until_empty_then_run_returns(
    consumer: FakeConsumer, relay: FakeRelay, start: Start, stop: asyncio.Event
) -> None:
    relay.passes = [0, 4, 4, 0, 9]
    worker = start(relay_idle=10, prune_interval=10)
    await until(lambda: relay.relay_calls == 1)
    stop.set()
    consumer.drained.set()
    await asyncio.wait_for(worker, timeout=1)  # prune's 10 s wait was cancelled
    assert relay.relay_calls == 4  # 4, 4, then the empty pass; never the 9


async def test_a_failure_during_the_flush_ends_it(
    consumer: FakeConsumer, relay: FakeRelay, start: Start, stop: asyncio.Event
) -> None:
    relay.passes = [0, RuntimeError("database down"), 5]
    worker = start(error_pause=10)
    await until(lambda: relay.relay_calls == 1)
    stop.set()
    consumer.drained.set()
    await asyncio.wait_for(worker, timeout=1)
    assert relay.relay_calls == 2


async def test_a_flush_that_hangs_is_cut_off_at_its_deadline(
    consumer: FakeConsumer,
    relay: FakeRelay,
    start: Start,
    stop: asyncio.Event,
    log_lines: LogLines,
) -> None:
    worker = start(flush=0.05)
    await until(lambda: relay.relay_calls == 1)
    relay.hang = True  # a database call that never returns
    stop.set()
    consumer.drained.set()
    await asyncio.wait_for(worker, timeout=1)
    assert "outbox flush cut off at the deadline" in [
        line["event"] for line in log_lines()
    ]


async def test_schedules_are_checked_at_start_up_and_then_every_interval(
    scheduler: FakeScheduler, start: Start
) -> None:
    start(schedule_interval=0.01)
    await until(lambda: len(scheduler.calls) >= 3)
    assert all(call.tzinfo is UTC for call in scheduler.calls)
    assert scheduler.calls == sorted(scheduler.calls)


async def test_a_failed_schedule_pass_is_logged_and_the_next_one_runs(
    scheduler: FakeScheduler, start: Start, log_lines: LogLines
) -> None:
    scheduler.fails = [ConnectionError("database down")]
    start(schedule_interval=0.01)
    await until(lambda: len(scheduler.calls) >= 2)
    failed = [line for line in log_lines() if line["event"] == "schedule pass failed"]
    assert len(failed) == 1
    assert "database down" in failed[0]["exception"]


async def test_stopping_cancels_the_scheduler_without_waiting_for_it(
    consumer: FakeConsumer, scheduler: FakeScheduler, start: Start, stop: asyncio.Event
) -> None:
    worker = start(schedule_interval=10)
    await until(lambda: len(scheduler.calls) == 1)
    stop.set()
    consumer.drained.set()
    await asyncio.wait_for(worker, timeout=1)  # its 10 s wait was cancelled
    assert len(scheduler.calls) == 1


async def test_each_extra_pruner_runs_every_interval_and_is_logged(
    relay: FakeRelay, start: Start, log_lines: LogLines
) -> None:
    seen = FakePruner("interactions_seen")
    start(prune_interval=0.01, pruners=(seen,))
    await until(lambda: seen.calls >= 3 and relay.prune_calls >= 3)
    pruned = [line for line in log_lines() if line["event"] == "pruned"]
    assert pruned[0] | {"table": "interactions_seen", "count": 2} == pruned[0]


async def test_a_failing_pruner_does_not_stop_the_others(
    relay: FakeRelay, start: Start, log_lines: LogLines
) -> None:
    broken, fine = FakePruner("broken", failing=True), FakePruner("fine")
    start(prune_interval=0.01, pruners=(broken, fine))
    await until(lambda: fine.calls >= 3)
    assert broken.calls >= 3
    failed = [line for line in log_lines() if line["event"] == "prune failed"]
    assert {line.get("table") for line in failed} == {"broken"}
