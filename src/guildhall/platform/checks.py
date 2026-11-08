"""Contract rules that types cannot express, checked by the platform.

The registry runs `check_declarations` on every enabled plug-in at startup and
the worker runs `check_outcome` on every job's result. The contract test suite
calls the same functions, so CI and production enforce identical rules. Each
function returns every problem found, so one run reports them all.
"""

import re
from datetime import timedelta
from typing import Any

from guildhall.contract import ContractModel, JobType, Outcome, SourcePlugin

SUPPORTED_CONTRACT_VERSIONS = frozenset({1})

MAX_QUEUE_DELAY = timedelta(minutes=15)
"""SQS rejects message delays longer than 15 minutes."""

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")


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

    for schedule in plugin.schedules():
        if schedule.job_type not in seen:
            problems.append(f"schedule names undeclared job type {schedule.job_type!r}")
    return problems


def check_outcome(plugin: SourcePlugin, outcome: Outcome) -> list[str]:
    """Problems with a handler's outcome: namespacing, follow-ups and delays."""
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
