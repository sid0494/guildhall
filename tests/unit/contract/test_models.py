"""Unit tests for the contract's value types."""

from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, Integer, Table, delete, insert, select

from guildhall.contract import (
    Archive,
    CommandOutcome,
    ContractModel,
    Event,
    Invocation,
    Job,
    JobSpec,
    LinkedAccount,
    Message,
    Outcome,
    RateLimitPolicy,
    Schedule,
    ScheduledRun,
    plugin_metadata,
)

items = Table(
    "sample_items", plugin_metadata(), Column("id", Integer, primary_key=True)
)


class FetchMatch(ContractModel):
    match_id: int


class MatchAnalyzed(Event):
    type: Literal["example.match_analyzed"] = "example.match_analyzed"
    schema_version: Literal[1] = 1
    match_id: int


def make_event(match_id: int = 1) -> MatchAnalyzed:
    return MatchAnalyzed(dedupe_key=f"example:match:{match_id}", match_id=match_id)


def test_values_are_immutable() -> None:
    policy = RateLimitPolicy(per_minute=60, per_day=2000)
    with pytest.raises(ValidationError, match="frozen"):
        policy.per_minute = 1  # type: ignore[misc]


def test_unknown_fields_are_ignored() -> None:
    raw = '{"dedupe_key": "example:match:1", "match_id": 1, "added_later": true}'
    event = MatchAnalyzed.model_validate_json(raw)
    assert event == make_event(1)


@pytest.mark.parametrize(("per_minute", "per_day"), [(0, 10), (10, 0), (-1, 10)])
def test_rate_limit_must_be_positive(per_minute: int, per_day: int) -> None:
    with pytest.raises(ValidationError):
        RateLimitPolicy(per_minute=per_minute, per_day=per_day)


def test_schedule_runs_per_account_by_default() -> None:
    schedule = Schedule(job_type="poll_matches", cron="*/15 * * * *")
    assert schedule.per_account


def test_job_payload_is_validated_into_its_model() -> None:
    raw = (
        '{"type": "fetch_match", "payload": {"match_id": 42},'
        ' "dedupe_key": "example:match:42", "attempt": 1}'
    )
    job = Job[FetchMatch].model_validate_json(raw)
    assert job.payload == FetchMatch(match_id=42)


def test_malformed_job_payload_is_rejected() -> None:
    raw = (
        '{"type": "fetch_match", "payload": {"match_id": "not a number"},'
        ' "dedupe_key": "example:match:42", "attempt": 1}'
    )
    with pytest.raises(ValidationError, match="match_id"):
        Job[FetchMatch].model_validate_json(raw)


def test_job_attempt_starts_at_one() -> None:
    with pytest.raises(ValidationError, match="attempt"):
        Job[FetchMatch](
            type="fetch_match",
            payload=FetchMatch(match_id=1),
            dedupe_key="example:match:1",
            attempt=0,
        )


@pytest.mark.parametrize("field", ["type", "dedupe_key"])
def test_job_spec_requires_non_empty_identifiers(field: str) -> None:
    values = {
        "type": "fetch_match",
        "payload": FetchMatch(match_id=1),
        "dedupe_key": "example:match:1",
        field: "",
    }
    with pytest.raises(ValidationError, match=field):
        JobSpec.model_validate(values)


def test_job_spec_delay_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="must not be negative"):
        JobSpec(
            type="fetch_match",
            payload=FetchMatch(match_id=1),
            dedupe_key="example:match:1",
            delay=timedelta(seconds=-1),
        )


def test_job_spec_serialises_the_full_payload() -> None:
    spec = JobSpec(
        type="fetch_match",
        payload=FetchMatch(match_id=42),
        dedupe_key="example:match:42",
    )
    assert spec.model_dump()["payload"] == {"match_id": 42}


def test_outcome_serialises_the_full_event() -> None:
    outcome = Outcome(events=(make_event(7),))
    assert outcome.model_dump()["events"][0]["match_id"] == 7


def test_outcome_that_retries_cannot_emit_events() -> None:
    with pytest.raises(
        ValidationError, match="cannot write, archive, emit events or ask"
    ):
        Outcome(retry_in=timedelta(minutes=3), events=(make_event(),))


def test_outcome_that_retries_cannot_write() -> None:
    with pytest.raises(
        ValidationError, match="cannot write, archive, emit events or ask"
    ):
        Outcome(retry_in=timedelta(minutes=3), writes=(insert(items).values(id=1),))


def test_outcome_that_retries_cannot_archive() -> None:
    with pytest.raises(ValidationError, match="cannot write, archive, emit events"):
        Outcome(
            retry_in=timedelta(minutes=3),
            archives=(Archive(kind="match", id="42", body=b"{}"),),
        )


def test_archive_keeps_the_body_as_bytes_exactly() -> None:
    body = b'{"gpm": 512.0, "kills": 7}'
    assert Archive(kind="match", id="42", body=body).body == body


def test_archive_body_is_left_out_of_its_repr() -> None:
    # Bodies can be megabytes; logging an outcome must not print them.
    archive = Archive(kind="match", id="42", body=b"x" * 10_000)
    assert "body" not in repr(archive)


def test_outcome_keeps_its_writes_in_order() -> None:
    first, second = insert(items).values(id=1), delete(items).where(items.c.id == 2)
    assert Outcome(writes=(first, second)).writes == (first, second)


def test_outcome_writes_cannot_be_selects() -> None:
    with pytest.raises(ValidationError, match="UpdateBase"):
        Outcome(writes=(select(items),))  # type: ignore[arg-type]


def test_outcome_retry_delay_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        Outcome(retry_in=timedelta(0))


def test_empty_outcome_is_a_finished_job_with_no_output() -> None:
    outcome = Outcome()
    assert (outcome.writes, outcome.events, outcome.jobs, outcome.retry_in) == (
        (),
        (),
        (),
        None,
    )


def test_a_scheduled_run_survives_the_queue_in_utc() -> None:
    run = ScheduledRun(
        scheduled_for=datetime(2026, 11, 8, 18, 0, tzinfo=UTC),
        account=LinkedAccount(id=7, discord_user_id="user-1", external_id="account-1"),
    )
    back = ScheduledRun.model_validate_json(run.model_dump_json())
    assert back == run
    assert back.account is not None
    assert back.account.id == 7
    assert back.scheduled_for.utcoffset() == timedelta(0)


def test_a_scheduled_run_without_an_account_is_for_the_whole_plugin() -> None:
    assert ScheduledRun(scheduled_for=datetime(2026, 11, 8, tzinfo=UTC)).account is None


def test_a_message_must_fit_discord() -> None:
    assert Message(content="x" * 2000).content == "x" * 2000
    with pytest.raises(ValidationError):
        Message(content="x" * 2001)
    with pytest.raises(ValidationError):
        Message(content="")


def test_an_invocation_survives_the_queue_with_its_options() -> None:
    class Options(ContractModel):
        weeks: int = 4

    invocation = Invocation[Options](
        discord_user_id="user-1",
        account=LinkedAccount(id=3, discord_user_id="user-1", external_id="42"),
        options=Options(weeks=8),
    )
    back = Invocation[Options].model_validate_json(invocation.model_dump_json())
    assert back == invocation
    assert back.options.weeks == 8


def test_a_command_outcome_needs_a_reply() -> None:
    with pytest.raises(ValidationError, match="reply"):
        CommandOutcome()  # type: ignore[call-arg]
