"""Unit tests for the platform's contract checks: one test per rule."""

from datetime import timedelta
from typing import Any, Literal

import pytest
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    column,
    delete,
    func,
    insert,
    literal_column,
    select,
    table,
    text,
    update,
)

from guildhall.contract import (
    Archive,
    Context,
    ContractModel,
    Event,
    Job,
    JobSpec,
    JobType,
    Outcome,
    RateLimitPolicy,
    Schedule,
    ScheduledRun,
    SourcePlugin,
    plugin_metadata,
)
from guildhall.platform.checks import (
    check_archive_key,
    check_declarations,
    check_outcome,
    check_read,
    check_statement,
)


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
            JobType("tick", ScheduledRun, handle),
        ]
        self._schedules = schedules or []

    def job_types(self) -> list[JobType[Any]]:
        return self._job_types

    def schedules(self) -> list[Schedule]:
        return self._schedules


def test_valid_plugin_has_no_problems() -> None:
    plugin = Sample(schedules=[Schedule(job_type="tick", cron="*/15 * * * *")])
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


def test_a_scheduled_job_type_must_take_a_scheduled_run() -> None:
    plugin = Sample(schedules=[Schedule(job_type="ping", cron="* * * * *")])
    assert check_declarations(plugin) == [
        "scheduled job type 'ping' must take ScheduledRun as its payload"
    ]


@pytest.mark.parametrize("cron", ["61 * * * *", "* * * *", "every hour", "@daily x"])
def test_a_schedule_needs_a_valid_cron_expression(cron: str) -> None:
    plugin = Sample(schedules=[Schedule(job_type="tick", cron=cron)])
    assert check_declarations(plugin) == [
        f"schedule for 'tick' has an invalid cron expression {cron!r}"
    ]


def test_a_job_type_is_scheduled_at_most_once() -> None:
    plugin = Sample(
        schedules=[
            Schedule(job_type="tick", cron="0 * * * *"),
            Schedule(job_type="tick", cron="30 * * * *"),
        ]
    )
    assert check_declarations(plugin) == ["job type 'tick' is scheduled more than once"]


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


def test_follow_up_delay_must_fit_the_queue_limit() -> None:
    spec = JobSpec(
        type="ping",
        payload=Ping(n=1),
        dedupe_key="sample:ping:1",
        delay=timedelta(minutes=16),
    )
    outcome = Outcome(jobs=(spec,))
    assert any("exceeds 0:15:00" in p for p in check_outcome(Sample(), outcome))


def test_retry_delay_must_fit_the_queue_limit() -> None:
    outcome = Outcome(retry_in=timedelta(minutes=16))
    assert any("retry delay" in p for p in check_outcome(Sample(), outcome))


def test_delays_at_the_limit_are_allowed() -> None:
    assert check_outcome(Sample(), Outcome(retry_in=timedelta(minutes=15))) == []


owned_metadata = plugin_metadata()
items = Table("sample_items", owned_metadata, Column("id", Integer, primary_key=True))
tags = Table("sample_tags", owned_metadata, Column("item_id", Integer))
others = Table(
    "other_things", plugin_metadata(), Column("id", Integer, primary_key=True)
)


class Storing(Sample):
    tables = owned_metadata


def test_tables_must_carry_the_plugin_name() -> None:
    metadata = plugin_metadata()
    Table("misnamed", metadata, Column("id", Integer, primary_key=True))

    class Misnamed(Sample):
        tables = metadata

    assert check_declarations(Misnamed()) == [
        "table 'misnamed' must be named 'sample_'..."
    ]


def test_tables_must_use_the_platform_naming_convention() -> None:
    class Plain(Sample):
        tables = MetaData()

    assert check_declarations(Plain()) == [
        "tables must be declared in a plugin_metadata()"
    ]


def test_declared_tables_are_valid() -> None:
    assert check_declarations(Storing()) == []


recent = select(items.c.id).order_by(items.c.id.desc()).limit(20).subquery()


@pytest.mark.parametrize(
    "statement",
    [
        select(items).join(tags, tags.c.item_id == items.c.id),
        select(func.count()).where(tags.c.item_id.in_(select(recent.c.id))),
        select(select(items.c.id).cte("c").c.id),
        insert(items).values(id=1),
        update(items).where(items.c.id == 1).values(id=2),
        delete(tags).where(tags.c.item_id.in_(select(items.c.id))),
    ],
    ids=["join", "subquery", "cte", "insert", "update", "delete-with-subquery"],
)
def test_statements_on_owned_tables_are_allowed(statement: object) -> None:
    assert check_statement(Storing(), statement) == []


@pytest.mark.parametrize(
    "statement",
    [
        select(others),
        select(items).where(items.c.id.in_(select(others.c.id))),
        select(others.alias("o")),
        select(table("processed_jobs", column("dedupe_key"))),
        delete(items).where(items.c.id.in_(select(others.c.id))),
        insert(others).values(id=1),
    ],
    ids=["direct", "hidden-subquery", "alias", "lightweight", "in-a-write", "target"],
)
def test_statements_touching_other_tables_are_refused(statement: object) -> None:
    problems = check_statement(Storing(), statement)
    assert len(problems) == 1
    assert "which 'sample' does not own" in problems[0]


@pytest.mark.parametrize(
    "statement",
    [
        select(items).where(text("id in (select 1 from processed_jobs)")),
        select(literal_column("(select count(*) from processed_jobs)")),
        select(column("x")).select_from(text("processed_jobs")),
        select(literal_column("* from processed_jobs --")),
    ],
    ids=["text-in-where", "literal-column", "text-as-from", "star-lookalike"],
)
def test_raw_sql_text_is_refused(statement: object) -> None:
    assert check_statement(Storing(), statement) == [
        "raw SQL text is not allowed; build the statement"
    ]


def test_strings_are_not_statements() -> None:
    assert check_statement(Storing(), "select * from sample_items") == [
        "expected a select, insert, update or delete, got str"
    ]


def test_reads_must_be_selects() -> None:
    assert check_read(Storing(), insert(items).values(id=1)) == [
        "reads must be select statements"
    ]


def test_writes_to_owned_tables_are_allowed() -> None:
    outcome = Outcome(writes=(insert(items).values(id=1),))
    assert check_outcome(Storing(), outcome) == []


def test_writes_to_tables_the_plugin_does_not_own_are_reported() -> None:
    outcome = Outcome(writes=(insert(others).values(id=1),))
    assert check_outcome(Storing(), outcome) == [
        "write touches table 'other_things', which 'sample' does not own"
    ]


@pytest.mark.parametrize(
    ("kind", "id"),
    [("match", "8123456789"), ("price_history", "app-570.us"), ("m", "x" * 200)],
)
def test_archive_keys_that_stay_in_their_folder_are_allowed(kind: str, id: str) -> None:
    assert check_archive_key(kind, id) == []


@pytest.mark.parametrize(
    ("kind", "id", "problem"),
    [
        ("match", "../../other_plugin/match/1", "archive id"),
        ("match", "a/b", "archive id"),
        ("match", ".hidden", "archive id"),
        ("match", "..", "archive id"),
        ("match", "x" * 201, "archive id"),
        ("Match", "1", "archive kind"),
        ("../match", "1", "archive kind"),
        ("match/raw", "1", "archive kind"),
    ],
)
def test_archive_keys_that_could_leave_their_folder_are_refused(
    kind: str, id: str, problem: str
) -> None:
    (reported,) = check_archive_key(kind, id)
    assert reported.startswith(problem)


def test_outcome_archives_are_checked() -> None:
    outcome = Outcome(
        archives=(
            Archive(kind="match", id="1", body=b"{}"),
            Archive(kind="match", id="../2", body=b"{}"),
        )
    )
    (problem,) = check_outcome(Sample(), outcome)
    assert problem.startswith("archive id '../2'")


def test_archiving_the_same_response_twice_in_one_outcome_is_reported() -> None:
    first = Archive(kind="match", id="1", body=b"{}")
    second = Archive(kind="match", id="1", body=b'{"parsed": true}')
    assert check_outcome(Sample(), Outcome(archives=(first, second))) == [
        "archive 'match' '1' is declared more than once"
    ]
