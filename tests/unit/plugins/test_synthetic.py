"""Unit tests for the synthetic plug-in's payload-driven behaviour."""

import asyncio

import pytest
from pydantic import ValidationError

from guildhall.contract import (
    Context,
    Job,
    Outcome,
    PermanentError,
    RetryableError,
)
from guildhall.plugins.synthetic import (
    NOT_READY_DELAY,
    SyntheticPlugin,
    SyntheticWork,
    WorkDone,
)


def run(work: SyntheticWork, attempt: int = 1) -> Outcome:
    job = Job[SyntheticWork](
        type="work",
        payload=work,
        dedupe_key=f"synthetic:work:{work.work_id}",
        attempt=attempt,
    )
    return asyncio.run(SyntheticPlugin().work(job, Context()))


def test_plain_work_finishes_with_no_output() -> None:
    assert run(SyntheticWork(work_id="a")) == Outcome()


def test_fan_out_creates_namespaced_child_jobs() -> None:
    outcome = run(SyntheticWork(work_id="a", fan_out=2))
    assert [spec.dedupe_key for spec in outcome.jobs] == [
        "synthetic:work:a.0",
        "synthetic:work:a.1",
    ]
    assert [spec.payload for spec in outcome.jobs] == [
        SyntheticWork(work_id="a.0"),
        SyntheticWork(work_id="a.1"),
    ]


def test_emit_events_creates_one_event_each() -> None:
    outcome = run(SyntheticWork(work_id="a", emit_events=2))
    assert outcome.events == (
        WorkDone(dedupe_key="synthetic:work_done:a:0", work_id="a", index=0),
        WorkDone(dedupe_key="synthetic:work_done:a:1", work_id="a", index=1),
    )


def test_not_ready_work_retries_until_the_attempt_passes() -> None:
    work = SyntheticWork(work_id="a", not_ready_for=2, emit_events=1)
    assert run(work, attempt=1) == Outcome(retry_in=NOT_READY_DELAY)
    assert run(work, attempt=2) == Outcome(retry_in=NOT_READY_DELAY)
    assert len(run(work, attempt=3).events) == 1


@pytest.mark.parametrize(
    ("fail", "error"),
    [
        ("retryable", RetryableError),
        ("permanent", PermanentError),
        ("crash", RuntimeError),
    ],
)
def test_fail_raises_the_requested_error(fail: str, error: type[Exception]) -> None:
    work = SyntheticWork.model_validate({"work_id": "a", "fail": fail})
    with pytest.raises(error, match="a"):
        run(work)


@pytest.mark.parametrize(
    "overrides",
    [{"work_id": ""}, {"work_id": "has space"}, {"fan_out": 1_001}, {"sleep_ms": -1}],
)
def test_out_of_range_payloads_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SyntheticWork.model_validate({"work_id": "a", **overrides})
