"""Unit tests for the plug-in registry."""

from typing import Any

import pytest

from guildhall.contract import (
    Context,
    ContractModel,
    Job,
    JobType,
    Outcome,
    RateLimitPolicy,
    Schedule,
    SourcePlugin,
)
from guildhall.platform.registry import RegistryError, build_registry


class Ping(ContractModel):
    n: int


async def handle(job: Job[Ping], ctx: Context) -> Outcome:
    return Outcome()


class Alpha(SourcePlugin):
    name = "alpha"
    rate_limit = RateLimitPolicy(per_minute=1, per_day=1)

    def job_types(self) -> list[JobType[Any]]:
        return [JobType("ping", Ping, handle)]


class Beta(Alpha):
    name = "beta"


class BadSchedule(Alpha):
    name = "bad_schedule"

    def schedules(self) -> list[Schedule]:
        return [Schedule(job_type="missing", cron="* * * * *")]


CATALOGUE: dict[str, type[SourcePlugin]] = {
    "alpha": Alpha,
    "beta": Beta,
    "bad_schedule": BadSchedule,
    "misnamed": Alpha,
}


def problems_for(enabled: list[str]) -> tuple[str, ...]:
    with pytest.raises(RegistryError) as error:
        build_registry(CATALOGUE, enabled)
    return error.value.problems


def test_enabled_plugins_are_instantiated_by_name() -> None:
    registry = build_registry(CATALOGUE, ["alpha", "beta"])
    assert list(registry) == ["alpha", "beta"]
    assert isinstance(registry["beta"], Beta)


def test_registry_cannot_be_modified() -> None:
    registry = build_registry(CATALOGUE, ["alpha"])
    with pytest.raises(TypeError):
        registry["beta"] = Beta()  # type: ignore[index]


def test_unknown_plugin_lists_what_is_available() -> None:
    (problem,) = problems_for(["gamma"])
    assert problem == (
        "unknown plug-in 'gamma'; available: alpha, bad_schedule, beta, misnamed"
    )


def test_plugin_enabled_twice_is_reported() -> None:
    assert problems_for(["alpha", "alpha"]) == (
        "plug-in 'alpha' is enabled more than once",
    )


def test_catalogue_key_must_match_the_plugin_name() -> None:
    assert problems_for(["misnamed"]) == (
        "catalogue entry 'misnamed' holds a plug-in named 'alpha'",
    )


def test_declaration_problems_are_reported_per_plugin() -> None:
    (problem,) = problems_for(["bad_schedule"])
    assert problem.startswith("bad_schedule: schedule names undeclared job type")


def test_all_problems_are_reported_together() -> None:
    assert len(problems_for(["gamma", "bad_schedule", "misnamed"])) == 3


def test_error_message_lists_every_problem() -> None:
    with pytest.raises(RegistryError, match=r"(?s)cannot load.*- unknown.*- unknown"):
        build_registry(CATALOGUE, ["gamma", "delta"])
