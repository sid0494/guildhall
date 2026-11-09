"""Unit tests for the consumer: receive only what can start, drain on stop."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from guildhall.platform.consumer import ConsumerSettings, JobConsumer
from guildhall.platform.queue import ReceivedMessage
from guildhall.platform.runner import RunResult, Status

type LogLines = Callable[[], list[dict[str, Any]]]


def message(body: str) -> ReceivedMessage:
    return ReceivedMessage(body=body, receipt_handle=body, receive_count=1)


@dataclass
class FakeSource:
    """A queue holding `pending`; an empty receive waits briefly, like a long poll."""

    pending: list[ReceivedMessage] = field(default_factory=list)
    requests: list[int] = field(default_factory=list)
    failures: int = 0
    poll_gate: asyncio.Event | None = None
    """When set, a receive waits for this event, like a long poll in progress."""
    handed_back: list[tuple[str, int]] = field(default_factory=list)

    async def receive(
        self, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[ReceivedMessage]:
        self.requests.append(max_messages)
        if self.failures:
            self.failures -= 1
            raise ConnectionError("queue unreachable")
        if self.poll_gate is not None:
            await self.poll_gate.wait()
        elif not self.pending:
            await asyncio.sleep(0.005)
        taken, self.pending = (
            self.pending[:max_messages],
            self.pending[max_messages:],
        )
        return taken

    async def retry_later(self, message: ReceivedMessage, delay_seconds: int) -> None:
        self.handed_back.append((message.body, delay_seconds))


@dataclass
class FakeRunner:
    """Runs a message until its gate opens; bodies in `explode` raise instead."""

    gates: dict[str, asyncio.Event] = field(default_factory=dict)
    started: list[str] = field(default_factory=list)
    finished: list[str] = field(default_factory=list)
    explode: set[str] = field(default_factory=set)

    def gate(self, body: str) -> asyncio.Event:
        return self.gates.setdefault(body, asyncio.Event())

    async def run(self, message: ReceivedMessage) -> RunResult:
        self.started.append(message.body)
        if message.body in self.explode:
            raise ConnectionError("queue unreachable while settling")
        await self.gate(message.body).wait()
        self.finished.append(message.body)
        return RunResult(Status.DONE)


async def until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not condition():  # noqa: ASYNC110 - polls fakes' plain state
            await asyncio.sleep(0.001)


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def stop() -> asyncio.Event:
    return asyncio.Event()


type Start = Callable[..., asyncio.Task[None]]


@pytest.fixture
async def start(
    source: FakeSource, runner: FakeRunner, stop: asyncio.Event
) -> AsyncIterator[Start]:
    """Start a consumer; cancel it after the test if it is still running."""
    tasks: list[asyncio.Task[None]] = []

    def start_consumer(concurrency: int, drain: float = 1) -> asyncio.Task[None]:
        consumer = JobConsumer(
            source,
            runner,
            ConsumerSettings(
                worker_concurrency=concurrency, shutdown_drain_seconds=drain
            ),
            error_pause_seconds=0.01,
        )
        task = asyncio.create_task(consumer.consume(stop))
        tasks.append(task)
        return task

    yield start_consumer
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_never_receives_more_than_the_free_slots(
    source: FakeSource, runner: FakeRunner, start: Start
) -> None:
    source.pending = [message(f"m{n}") for n in range(5)]
    start(3)
    await until(lambda: len(runner.started) == 3)
    await asyncio.sleep(0.02)  # all slots busy: no further receive
    assert source.requests == [3]

    runner.gate("m0").set()
    await until(lambda: len(runner.started) == 4)
    assert source.requests == [3, 1]


async def test_a_slow_job_does_not_hold_up_the_other_slots(
    source: FakeSource, runner: FakeRunner, start: Start
) -> None:
    source.pending = [message(f"m{n}") for n in range(6)]
    for n in range(1, 6):
        runner.gate(f"m{n}").set()  # every job but m0 finishes at once
    start(2)
    await until(lambda: len(runner.finished) == 5)
    assert "m0" not in runner.finished


async def test_one_receive_asks_for_at_most_ten(
    source: FakeSource, start: Start
) -> None:
    start(25)
    await until(lambda: len(source.requests) >= 2)
    assert set(source.requests) == {10}


async def test_slots_a_receive_did_not_fill_are_given_back(
    source: FakeSource, runner: FakeRunner, start: Start
) -> None:
    source.pending = [message("m0")]
    start(4)
    await until(lambda: len(source.requests) >= 2)
    assert source.requests[:2] == [4, 3]


async def test_a_job_that_cannot_be_settled_is_logged_and_frees_its_slot(
    source: FakeSource,
    runner: FakeRunner,
    start: Start,
    log_lines: LogLines,
) -> None:
    runner.explode = {"bad"}
    runner.gate("good").set()
    source.pending = [message("bad"), message("good")]
    start(1)
    await until(lambda: runner.finished == ["good"])
    (line,) = [
        line for line in log_lines() if line["event"] == "could not settle message"
    ]
    assert "ConnectionError" in line["exception"]


async def test_a_failed_receive_is_logged_and_tried_again(
    source: FakeSource,
    runner: FakeRunner,
    start: Start,
    log_lines: LogLines,
) -> None:
    source.failures = 2
    runner.gate("m0").set()
    source.pending = [message("m0")]
    start(1)
    await until(lambda: runner.finished == ["m0"])
    failed = [line for line in log_lines() if line["event"] == "receive failed"]
    assert len(failed) == 2
    assert source.requests[:3] == [1, 1, 1]  # each failure returned its slot


async def test_stopping_lets_running_jobs_finish_and_receives_no_more(
    source: FakeSource, runner: FakeRunner, start: Start, stop: asyncio.Event
) -> None:
    source.pending = [message("m0")]
    consumer = start(2)
    await until(lambda: runner.started == ["m0"])
    stop.set()
    await asyncio.sleep(0.02)
    assert not consumer.done()  # still draining m0
    requests = len(source.requests)
    runner.gate("m0").set()
    await asyncio.wait_for(consumer, timeout=1)
    assert runner.finished == ["m0"]
    assert len(source.requests) == requests


async def test_jobs_still_running_at_the_deadline_are_cancelled(
    source: FakeSource,
    runner: FakeRunner,
    start: Start,
    stop: asyncio.Event,
    log_lines: LogLines,
) -> None:
    source.pending = [message("stuck")]
    consumer = start(1, drain=0.05)
    await until(lambda: runner.started == ["stuck"])
    stop.set()
    await asyncio.wait_for(consumer, timeout=1)
    assert runner.finished == []
    assert "cancelling work at the drain deadline" in [
        line["event"] for line in log_lines()
    ]


async def test_a_poll_in_progress_finishes_and_its_messages_are_handed_back(
    source: FakeSource, runner: FakeRunner, start: Start, stop: asyncio.Event
) -> None:
    source.poll_gate = asyncio.Event()
    consumer = start(2)
    await until(lambda: len(source.requests) == 1)
    stop.set()
    await asyncio.sleep(0.02)
    assert not consumer.done()  # the poll is left to finish, not cancelled
    source.pending = [message("late")]
    source.poll_gate.set()
    await asyncio.wait_for(consumer, timeout=1)
    assert source.handed_back == [("late", 0)]
    assert runner.started == []


async def test_stopping_while_every_slot_is_busy_starts_no_receive(
    source: FakeSource, runner: FakeRunner, start: Start, stop: asyncio.Event
) -> None:
    source.pending = [message("m0"), message("m1")]
    consumer = start(1)
    await until(lambda: runner.started == ["m0"])
    stop.set()
    runner.gate("m0").set()
    await asyncio.wait_for(consumer, timeout=1)
    assert source.requests == [1]
    assert runner.started == ["m0"]


async def test_cancelling_the_consumer_cancels_running_jobs_at_once(
    source: FakeSource, runner: FakeRunner, start: Start
) -> None:
    source.pending = [message("m0")]
    consumer = start(1)
    await until(lambda: runner.started == ["m0"])
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consumer, timeout=1)
    assert runner.finished == []
