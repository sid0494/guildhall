"""Contract rules that types cannot express, checked by the platform.

The registry runs `check_declarations` on every enabled plug-in at startup and
the worker runs `check_outcome` on every job's result. The contract test suite
calls the same functions, so CI and production enforce identical rules. Each
function returns every problem found, so one run reports them all.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from cronsim import CronSim, CronSimError
from sqlalchemy import Select
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ColumnClause, TextClause
from sqlalchemy.sql.expression import UpdateBase
from sqlalchemy.sql.selectable import TableClause

from guildhall.contract import (
    AccountLink,
    Command,
    ContractModel,
    JobType,
    LinkedAccount,
    Outcome,
    Schedule,
    ScheduledRun,
    SourcePlugin,
)
from guildhall.contract.storage import NAMING_CONVENTION
from guildhall.platform.command_schema import OptionError, options_schema

SUPPORTED_CONTRACT_VERSIONS = frozenset({1})

MAX_QUEUE_DELAY = timedelta(minutes=15)
"""SQS rejects message delays longer than 15 minutes."""

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
_COMMAND_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
"""Discord allows 1 to 32 characters; we keep to lower-case ASCII."""
PLATFORM_COMMANDS = frozenset({"guildhall", "link"})
"""Top-level command names the platform keeps for itself (docs/adr/0062)."""
MAX_DESCRIPTION = 100
_ARCHIVE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
"""Starts with a letter or digit and has no slash, so it cannot leave the prefix."""


class ContractViolationError(Exception):
    """A plug-in broke a contract rule while running; dead-lettered, never retried."""


def job_types_by_name(plugin: SourcePlugin) -> dict[str, JobType[Any]]:
    """The plug-in's job types keyed by name; later duplicates win."""
    return {job_type.name: job_type for job_type in plugin.job_types()}


def check_declarations(plugin: SourcePlugin) -> list[str]:
    """Problems with what a plug-in declares, independent of running it."""
    problems: list[str] = []
    if not _IDENTIFIER.fullmatch(plugin.name):
        problems.append(
            f"plug-in name {plugin.name!r} must match {_IDENTIFIER.pattern}"
        )
    if plugin.contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        problems.append(f"contract version {plugin.contract_version} is not supported")

    seen: set[str] = set()
    for job_type in plugin.job_types():
        if not _IDENTIFIER.fullmatch(job_type.name):
            problems.append(
                f"job type name {job_type.name!r} must match {_IDENTIFIER.pattern}"
            )
        if job_type.name in seen:
            problems.append(f"job type {job_type.name!r} is declared more than once")
        seen.add(job_type.name)
        if not issubclass(job_type.payload_model, ContractModel):
            problems.append(
                f"job type {job_type.name!r} payload model must derive from"
                " ContractModel"
            )

    declared = job_types_by_name(plugin)
    link = plugin.account_link()
    if link is not None:
        problems.extend(_check_account_link(link, declared))
    scheduled: set[str] = set()
    for schedule in plugin.schedules():
        problems.extend(_check_schedule(schedule, declared.get(schedule.job_type)))
        if schedule.job_type in scheduled:
            problems.append(
                f"job type {schedule.job_type!r} is scheduled more than once"
            )
        scheduled.add(schedule.job_type)

    problems.extend(_check_commands(plugin.commands()))

    tables = plugin.tables
    if dict(tables.naming_convention) != NAMING_CONVENTION:
        problems.append("tables must be declared in a plugin_metadata()")
    for table_name in tables.tables:
        if not table_name.startswith(f"{plugin.name}_"):
            problems.append(
                f"table {table_name!r} must be named {plugin.name + '_'!r}..."
            )
    return problems


def _check_commands(commands: list[Command[Any]]) -> list[str]:
    problems: list[str] = []
    paths: set[tuple[str, ...]] = set()
    for command in commands:
        name = "/" + " ".join(command.path)
        if not 1 <= len(command.path) <= 2:
            problems.append(f"command {name!r} must have one or two names")
        for part in command.path:
            if not _COMMAND_NAME.fullmatch(part):
                problems.append(
                    f"command {name!r}: {part!r} must match {_COMMAND_NAME.pattern}"
                )
        if command.path and command.path[0] in PLATFORM_COMMANDS:
            problems.append(f"command {name!r}: /{command.path[0]} is the platform's")
        if not 1 <= len(command.description) <= MAX_DESCRIPTION:
            problems.append(
                f"command {name!r} needs a description of 1 to {MAX_DESCRIPTION}"
                " characters"
            )
        if not issubclass(command.options, ContractModel):
            problems.append(
                f"command {name!r} options model must derive from ContractModel"
            )
        try:
            options_schema(command.options)
        except OptionError as error:
            problems.append(f"command {name!r}: {error}")
        if command.path in paths:
            problems.append(f"command {name!r} is declared more than once")
        paths.add(command.path)
    for path in paths:
        if len(path) == 2 and (path[0],) in paths:
            problems.append(f"/{path[0]} is both a command and a group of subcommands")
    return problems


def _check_account_link(
    link: AccountLink, declared: dict[str, JobType[Any]]
) -> list[str]:
    problems: list[str] = []
    if not _COMMAND_NAME.fullmatch(link.kind):
        problems.append(
            f"account link kind {link.kind!r} must match {_COMMAND_NAME.pattern}"
        )
    if not 1 <= len(link.description) <= MAX_DESCRIPTION:
        problems.append(
            f"account link {link.kind!r} needs a description of 1 to"
            f" {MAX_DESCRIPTION} characters"
        )
    if link.on_link is not None:
        job_type = declared.get(link.on_link)
        if job_type is None:
            problems.append(
                f"account link {link.kind!r}: on_link {link.on_link!r} is not a"
                " declared job type"
            )
        elif job_type.payload_model is not LinkedAccount:
            problems.append(
                f"account link {link.kind!r}: job type {link.on_link!r} must take"
                " LinkedAccount as its payload"
            )
    return problems


def check_link_kinds(links: dict[str, AccountLink]) -> list[str]:
    """Problems across plug-ins: each `/link` kind has one owner.

    `links` maps plug-in names to their account links.
    """
    owners: dict[str, list[str]] = {}
    for plugin, link in links.items():
        owners.setdefault(link.kind, []).append(plugin)
    return [
        f"/link {kind} is declared by more than one plug-in: {', '.join(sorted(names))}"
        for kind, names in sorted(owners.items())
        if len(names) > 1
    ]


def check_command_owners(commands: dict[str, list[Command[Any]]]) -> list[str]:
    """Problems across plug-ins: each top-level command name has one owner.

    `commands` maps plug-in names to their commands.
    """
    owners: dict[str, set[str]] = {}
    for plugin, declared in commands.items():
        for command in declared:
            if command.path:
                owners.setdefault(command.path[0], set()).add(plugin)
    return [
        f"/{top} is declared by more than one plug-in: {', '.join(sorted(names))}"
        for top, names in sorted(owners.items())
        if len(names) > 1
    ]


def _check_schedule(schedule: Schedule, job_type: JobType[Any] | None) -> list[str]:
    problems: list[str] = []
    if job_type is None:
        problems.append(f"schedule names undeclared job type {schedule.job_type!r}")
    elif job_type.payload_model is not ScheduledRun:
        problems.append(
            f"scheduled job type {schedule.job_type!r} must take ScheduledRun as its"
            " payload"
        )
    try:
        CronSim(schedule.cron, datetime.now(UTC))
    except CronSimError:
        problems.append(
            f"schedule for {schedule.job_type!r} has an invalid cron expression"
            f" {schedule.cron!r}"
        )
    return problems


def check_statement(plugin: SourcePlugin, statement: object) -> list[str]:
    """Problems with a statement a plug-in reads or writes with.

    Every table it touches, including in subqueries, must be one the plug-in
    declares, and it may not contain raw SQL text, which cannot be inspected.
    This catches mistakes in our own plug-ins; it is not a sandbox.
    """
    if not isinstance(statement, Select | UpdateBase):
        return [
            f"expected a select, insert, update or delete, got"
            f" {type(statement).__name__}"
        ]
    owned = set(plugin.tables.tables.values())
    problems: list[str] = []
    foreign: set[str] = set()
    for element in visitors.iterate(statement):
        if isinstance(element, TableClause) and element not in owned:
            foreign.add(element.name)
        elif isinstance(element, TextClause) or (
            # `func.count()` renders its `*` as a literal column; nothing else may
            isinstance(element, ColumnClause)
            and element.is_literal
            and element.name != "*"
        ):
            problems.append("raw SQL text is not allowed; build the statement")
    problems.extend(
        f"touches table {name!r}, which {plugin.name!r} does not own"
        for name in sorted(foreign)
    )
    return sorted(set(problems))


def check_read(plugin: SourcePlugin, statement: object) -> list[str]:
    """Problems with a statement a plug-in reads with: a select on its own tables."""
    problems = check_statement(plugin, statement)
    if isinstance(statement, UpdateBase):
        problems.append("reads must be select statements")
    return problems


def check_archive_key(kind: str, id: str) -> list[str]:
    """Problems with the kind and id that name an archived response."""
    problems: list[str] = []
    if not _IDENTIFIER.fullmatch(kind):
        problems.append(f"archive kind {kind!r} must match {_IDENTIFIER.pattern}")
    if not _ARCHIVE_ID.fullmatch(id):
        problems.append(f"archive id {id!r} must match {_ARCHIVE_ID.pattern}")
    return problems


def check_outcome(plugin: SourcePlugin, outcome: Outcome) -> list[str]:
    """Problems with a handler's outcome: writes, archives, namespacing, follow-ups."""
    problems: list[str] = []
    key_prefix = f"{plugin.name}:"
    type_prefix = f"{plugin.name}."
    declared = job_types_by_name(plugin)

    for event in outcome.events:
        if not event.dedupe_key.startswith(key_prefix):
            problems.append(
                f"event dedupe key {event.dedupe_key!r} must start with {key_prefix!r}"
            )
        if not event.type.startswith(type_prefix):
            problems.append(
                f"event type {event.type!r} must start with {type_prefix!r}"
            )

    for write in outcome.writes:
        problems.extend(
            f"write {problem}" for problem in check_statement(plugin, write)
        )

    archived: set[tuple[str, str]] = set()
    for archive in outcome.archives:
        problems.extend(check_archive_key(archive.kind, archive.id))
        if (archive.kind, archive.id) in archived:
            problems.append(
                f"archive {archive.kind!r} {archive.id!r} is declared more than once"
            )
        archived.add((archive.kind, archive.id))

    if outcome.retry_in is not None and outcome.retry_in > MAX_QUEUE_DELAY:
        problems.append(f"retry delay {outcome.retry_in} exceeds {MAX_QUEUE_DELAY}")

    for spec in outcome.jobs:
        if spec.delay > MAX_QUEUE_DELAY:
            problems.append(
                f"follow-up job {spec.dedupe_key!r} delay {spec.delay} exceeds"
                f" {MAX_QUEUE_DELAY}"
            )
        if not spec.dedupe_key.startswith(key_prefix):
            problems.append(
                f"job dedupe key {spec.dedupe_key!r} must start with {key_prefix!r}"
            )
        job_type = declared.get(spec.type)
        if job_type is None:
            problems.append(f"follow-up job has undeclared type {spec.type!r}")
        elif not isinstance(spec.payload, job_type.payload_model):
            problems.append(
                f"follow-up job {spec.type!r} payload is"
                f" {type(spec.payload).__name__}, expected"
                f" {job_type.payload_model.__name__}"
            )
    return problems
