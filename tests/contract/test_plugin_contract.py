"""The contract suite: every check runs against every plug-in.

Each plug-in supplies example payloads for its job types in `CASES`. A fake
platform runs each example the way the worker will: the payload makes a JSON
round trip, is validated into its model, and the handler's outcome or error is
checked with the platform's own contract checks.
"""

import asyncio
import importlib
import inspect
import pkgutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

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
from guildhall.plugins.synthetic import SyntheticPlugin, SyntheticWork


@dataclass(frozen=True)
class PluginCase:
    plugin: SourcePlugin
    examples: Mapping[str, Sequence[ContractModel]]


CASES = [
    PluginCase(
        SyntheticPlugin(),
        {
            "work": [
                SyntheticWork(work_id="plain"),
                SyntheticWork(work_id="busy", fan_out=3, emit_events=2),
                SyntheticWork(work_id="waiting", not_ready_for=1),
                SyntheticWork(work_id="flaky", fail="retryable"),
                SyntheticWork(work_id="doomed", fail="permanent"),
            ]
        },
    ),
]

EXAMPLES = [
    pytest.param(case, job_type, payload, id=f"{case.plugin.name}-{job_type}-{i}")
    for case in CASES
    for job_type, payloads in case.examples.items()
    for i, payload in enumerate(payloads)
]


def run_job(
    plugin: SourcePlugin, job_type_name: str, payload: ContractModel
) -> Outcome:
    """Run one job as the worker would, after a JSON round trip of the payload."""
    job_type = job_types_by_name(plugin)[job_type_name]
    validated = job_type.payload_model.model_validate_json(payload.model_dump_json())
    job = Job[Any](
        type=job_type_name,
        payload=validated,
        dedupe_key=f"{plugin.name}:contract-suite",
        attempt=1,
    )
    return asyncio.run(job_type.handler(job, Context()))


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
    with_examples = {name for name, payloads in case.examples.items() if payloads}
    assert declared == with_examples


@pytest.mark.parametrize(("case", "job_type", "payload"), EXAMPLES)
def test_example_matches_its_payload_model(
    case: PluginCase, job_type: str, payload: ContractModel
) -> None:
    model = job_types_by_name(case.plugin)[job_type].payload_model
    assert type(payload) is model


@pytest.mark.parametrize(("case", "job_type", "payload"), EXAMPLES)
def test_example_outcome_satisfies_the_contract(
    case: PluginCase, job_type: str, payload: ContractModel
) -> None:
    try:
        outcome = run_job(case.plugin, job_type, payload)
    except RetryableError, PermanentError:
        return
    assert check_outcome(case.plugin, outcome) == []
