"""Unit tests for the plug-in interface."""

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import Row, Select

from guildhall.contract import (
    Context,
    ContractModel,
    Job,
    JobType,
    Outcome,
    RateLimitPolicy,
    SourcePlugin,
    plugin_metadata,
)
from guildhall.contract.storage import NAMING_CONVENTION


class NoDatabase:
    """`Context.db` for code that must not read: fails the test if it does."""

    async def fetch(self, statement: Select[Any]) -> Sequence[Row[Any]]:
        raise AssertionError("unexpected database read")


class NoArchive:
    """`Context.archive` for code that must not read archives."""

    async def get(self, kind: str, id: str) -> bytes | None:
        raise AssertionError("unexpected archive read")


class Ping(ContractModel):
    message: str


class MinimalPlugin(SourcePlugin):
    name = "minimal"
    rate_limit = RateLimitPolicy(per_minute=1, per_day=1)

    def job_types(self) -> list[JobType[Any]]:
        return [JobType("ping", Ping, self.ping)]

    async def ping(self, job: Job[Ping], ctx: Context) -> Outcome:
        return Outcome()


def test_plugin_missing_required_members_cannot_be_created() -> None:
    class Incomplete(SourcePlugin):
        name = "incomplete"

    with pytest.raises(TypeError, match="job_types") as error:
        Incomplete()  # type: ignore[abstract]
    assert "rate_limit" in str(error.value)


def test_plugin_declares_name_and_rate_limit_as_class_attributes() -> None:
    plugin = MinimalPlugin()
    assert plugin.name == "minimal"
    assert plugin.rate_limit == RateLimitPolicy(per_minute=1, per_day=1)


def test_contract_version_is_one() -> None:
    assert MinimalPlugin.contract_version == 1


def test_plugin_has_no_schedules_by_default() -> None:
    assert MinimalPlugin().schedules() == []


def test_job_type_handler_runs_with_a_typed_job() -> None:
    (job_type,) = MinimalPlugin().job_types()
    job = Job[Ping](
        type="ping", payload=Ping(message="hi"), dedupe_key="minimal:1", attempt=1
    )
    outcome = asyncio.run(
        job_type.handler(job, Context(db=NoDatabase(), archive=NoArchive()))
    )
    assert outcome == Outcome()


def test_plugin_owns_no_tables_by_default() -> None:
    tables = MinimalPlugin().tables
    assert tables.tables == {}
    assert dict(tables.naming_convention) == NAMING_CONVENTION


def test_plugin_metadata_applies_the_platform_naming_convention() -> None:
    assert dict(plugin_metadata().naming_convention) == NAMING_CONVENTION
