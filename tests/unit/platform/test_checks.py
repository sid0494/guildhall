"""Unit tests for the platform's contract checks: one test per rule."""

from typing import Any, Literal

import pytest
from pydantic import BaseModel

from guildhall.contract import (
    Context,
    ContractModel,
    Event,
    Job,
    JobSpec,
    JobType,
    Outcome,
    RateLimitPolicy,
    Schedule,
    SourcePlugin,
)
from guildhall.platform.checks import check_declarations, check_outcome


class Ping(ContractModel):
    n: int


class Pong(ContractModel):
    n: int


class LooseModel(BaseModel):
    n: int


class Pinged(Event):
    type: str = "sample.pinged"
    schema_version: Literal[1] = 1


async def handle(job: Job[Any], ctx: Context) -> Outcome:
    return Outcome()


class Sample(SourcePlugin):
    """A valid plug-in; tests override one thing at a time."""

    name = "sample"
    rate_limit = RateLimitPolicy(per_minute=1, per_day=1)

    def __init__(
        self,
        job_types: list[JobType[Any]] | None = None,
        schedules: list[Schedule] | None = None,
    ) -> None:
        self._job_types = job_types or [
            JobType("ping", Ping, handle),
            JobType("pong", Pong, handle),
        ]
        self._schedules = schedules or []

    def job_types(self) -> list[JobType[Any]]:
        return self._job_types

    def schedules(self) -> list[Schedule]:
        return self._schedules


def test_valid_plugin_has_no_problems() -> None:
    plugin = Sample(schedules=[Schedule(job_type="ping", cron="* * * * *")])
    assert check_declarations(plugin) == []


@pytest.mark.parametrize("name", ["", "Sample", "1st", "with-dash", "with space"])
def test_plugin_name_must_be_an_identifier(name: str) -> None:
    class Named(Sample):
        pass

    Named.name = name
    assert any("plug-in name" in p for p in check_declarations(Named()))


def test_unsupported_contract_version_is_reported() -> None:
    class Future(Sample):
        contract_version = 2

    assert any("contract version 2" in p for p in check_declarations(Future()))


def test_job_type_name_must_be_an_identifier() -> None:
    plugin = Sample(job_types=[JobType("Fetch-Match", Ping, handle)])
    assert any("job type name" in p for p in check_declarations(plugin))


def test_duplicate_job_type_is_reported() -> None:
    plugin = Sample(job_types=[JobType("ping", Ping, handle)] * 2)
    assert any("more than once" in p for p in check_declarations(plugin))


def test_payload_model_must_be_a_contract_model() -> None:
    plugin = Sample(job_types=[JobType("loose", LooseModel, handle)])
    assert any("ContractModel" in p for p in check_declarations(plugin))


def test_schedule_must_name_a_declared_job_type() -> None:
    plugin = Sample(schedules=[Schedule(job_type="missing", cron="* * * * *")])
    assert any("'missing'" in p for p in check_declarations(plugin))


def test_all_problems_are_reported_together() -> None:
    plugin = Sample(
        job_types=[JobType("Bad", LooseModel, handle)],
        schedules=[Schedule(job_type="missing", cron="* * * * *")],
    )
    assert len(check_declarations(plugin)) == 3


def test_namespaced_outcome_has_no_problems() -> None:
    outcome = Outcome(
        events=(Pinged(dedupe_key="sample:pinged:1"),),
        jobs=(JobSpec(type="pong", payload=Pong(n=1), dedupe_key="sample:pong:1"),),
    )
    assert check_outcome(Sample(), outcome) == []


def test_event_dedupe_key_must_be_namespaced() -> None:
    outcome = Outcome(events=(Pinged(dedupe_key="other:pinged:1"),))
    assert any("event dedupe key" in p for p in check_outcome(Sample(), outcome))


def test_event_type_must_be_namespaced() -> None:
    outcome = Outcome(events=(Pinged(type="other.pinged", dedupe_key="sample:p:1"),))
    assert any("event type" in p for p in check_outcome(Sample(), outcome))


def test_job_dedupe_key_must_be_namespaced() -> None:
    spec = JobSpec(type="ping", payload=Ping(n=1), dedupe_key="other:ping:1")
    outcome = Outcome(jobs=(spec,))
    assert any("job dedupe key" in p for p in check_outcome(Sample(), outcome))


def test_follow_up_job_type_must_be_declared() -> None:
    spec = JobSpec(type="missing", payload=Ping(n=1), dedupe_key="sample:x:1")
    outcome = Outcome(jobs=(spec,))
    assert any("undeclared type" in p for p in check_outcome(Sample(), outcome))


def test_follow_up_payload_must_match_its_job_type() -> None:
    spec = JobSpec(type="ping", payload=Pong(n=1), dedupe_key="sample:ping:1")
    outcome = Outcome(jobs=(spec,))
    assert any("expected Ping" in p for p in check_outcome(Sample(), outcome))
