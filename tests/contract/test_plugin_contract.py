"""The contract suite: every check runs against every plug-in.

Each plug-in supplies examples for its job types in `CASES`: a payload, and
the services its handler gets (by default, ones that fail if used). A fake
platform runs each example the way the worker will: the payload makes a JSON
round trip, is validated into its model, and the handler's outcome or error is
checked with the platform's own contract checks.
"""

import asyncio
import importlib
import inspect
import pkgutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from fakes import no_io_context

import guildhall.plugins
from guildhall.contract import (
    Context,
    ContractModel,
    Job,
    Outcome,
    PermanentError,
    RetryableError,
    SourcePlugin,
)
from guildhall.platform.checks import (
    check_declarations,
    check_outcome,
    job_types_by_name,
)
from guildhall.plugins import CATALOGUE
from guildhall.plugins.gank_report import GankReportPlugin
from guildhall.plugins.synthetic import SyntheticPlugin, SyntheticWork


@dataclass(frozen=True)
class Example:
    """One payload for a job type, the services its handler gets, and its attempt.

    `context` is called for each run, so fakes that record calls start fresh.
    """

    payload: ContractModel
    context: Callable[[], Context] = no_io_context
    attempt: int = 1


@dataclass(frozen=True)
class PluginCase:
    plugin: SourcePlugin
    examples: Mapping[str, Sequence[Example]]


CASES = [
    PluginCase(GankReportPlugin(), {}),
    PluginCase(
        SyntheticPlugin(),
        {
            "work": [
                Example(SyntheticWork(work_id="plain")),
                Example(SyntheticWork(work_id="busy", fan_out=3, emit_events=2)),
                Example(SyntheticWork(work_id="kept", record=True)),
                Example(SyntheticWork(work_id="archived.1", archive=True)),
                Example(SyntheticWork(work_id="waiting", not_ready_for=1)),
                Example(SyntheticWork(work_id="flaky", fail="retryable")),
                Example(SyntheticWork(work_id="doomed", fail="permanent")),
            ]
        },
    ),
]

EXAMPLES = [
    pytest.param(case, job_type, example, id=f"{case.plugin.name}-{job_type}-{i}")
    for case in CASES
    for job_type, examples in case.examples.items()
    for i, example in enumerate(examples)
]


def run_job(plugin: SourcePlugin, job_type_name: str, example: Example) -> Outcome:
    """Run one job as the worker would, after a JSON round trip of the payload."""
    job_type = job_types_by_name(plugin)[job_type_name]
    validated = job_type.payload_model.model_validate_json(
        example.payload.model_dump_json()
    )
    job = Job[Any](
        type=job_type_name,
        payload=validated,
        dedupe_key=f"{plugin.name}:contract-suite",
        attempt=example.attempt,
    )
    return asyncio.run(job_type.handler(job, example.context()))


def discovered_plugin_classes() -> set[type[SourcePlugin]]:
    """Every concrete plug-in class defined under `guildhall.plugins`."""
    for module in pkgutil.walk_packages(
        guildhall.plugins.__path__, f"{guildhall.plugins.__name__}."
    ):
        importlib.import_module(module.name)
    return {
        cls
        for cls in _all_subclasses(SourcePlugin)
        if issubclass(cls, SourcePlugin)
        and cls.__module__.startswith(f"{guildhall.plugins.__name__}.")
        and not inspect.isabstract(cls)
    }


def _all_subclasses(cls: type[object]) -> set[type[object]]:
    direct: set[type[object]] = set(cls.__subclasses__())
    return direct.union(*(_all_subclasses(sub) for sub in direct))


def test_every_plugin_has_a_contract_case() -> None:
    covered = {type(case.plugin) for case in CASES}
    assert discovered_plugin_classes() <= covered


def test_every_plugin_is_in_the_catalogue() -> None:
    assert discovered_plugin_classes() <= set(CATALOGUE.values())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.plugin.name)
def test_declarations_satisfy_the_contract(case: PluginCase) -> None:
    assert check_declarations(case.plugin) == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.plugin.name)
def test_every_job_type_has_an_example(case: PluginCase) -> None:
    declared = set(job_types_by_name(case.plugin))
    with_examples = {name for name, examples in case.examples.items() if examples}
    assert declared == with_examples


@pytest.mark.parametrize(("case", "job_type", "example"), EXAMPLES)
def test_example_matches_its_payload_model(
    case: PluginCase, job_type: str, example: Example
) -> None:
    model = job_types_by_name(case.plugin)[job_type].payload_model
    assert type(example.payload) is model


@pytest.mark.parametrize(("case", "job_type", "example"), EXAMPLES)
def test_example_outcome_satisfies_the_contract(
    case: PluginCase, job_type: str, example: Example
) -> None:
    try:
        outcome = run_job(case.plugin, job_type, example)
    except RetryableError, PermanentError:
        return
    assert check_outcome(case.plugin, outcome) == []
