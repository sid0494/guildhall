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
import json
import pkgutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import (
    DictArchive,
    FakeHttp,
    NoArchive,
    NoDatabase,
    NoHttp,
    RowsDatabase,
    ScriptedDatabase,
    no_io_context,
    response,
)
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID

import guildhall.plugins
from guildhall.contract import (
    Context,
    ContractModel,
    Job,
    LinkedAccount,
    Outcome,
    PermanentError,
    RetryableError,
    ScheduledRun,
    SourcePlugin,
)
from guildhall.platform.checks import (
    check_declarations,
    check_outcome,
    job_types_by_name,
)
from guildhall.plugins import CATALOGUE
from guildhall.plugins.gank_report import AnalyzeMatch, FetchMatch, GankReportPlugin
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


def opendota_serving(match_fixture: str) -> Callable[[], Context]:
    """A context whose OpenDota serves this recorded version of match 5001."""

    def context() -> Context:
        match = (FIXTURES_DIR / match_fixture).read_bytes()
        parse_request = (FIXTURES_DIR / "parse_request.json").read_bytes()
        http = FakeHttp(
            {
                ("GET", "https://api.opendota.com/api/matches/5001"): response(match),
                ("POST", "https://api.opendota.com/api/request/5001"): response(
                    parse_request
                ),
            }
        )
        return Context(db=NoDatabase(), archive=NoArchive(), http=http)

    return context


def fetched_and_archived(match_fixture: str | None) -> Callable[[], Context]:
    """A context where match 5001 has been fetched, with this archived body.

    None means not fetched yet: no row, and nothing archived.
    """

    def context() -> Context:
        if match_fixture is None:
            return Context(db=RowsDatabase(), archive=NoArchive(), http=NoHttp())
        body = (FIXTURES_DIR / match_fixture).read_bytes()
        baselines = [SimpleNamespace(metric="gpm", average=450.0, games=3)]
        return Context(
            db=ScriptedDatabase([SimpleNamespace(mode="normal")], baselines),
            archive=DictArchive({("match", "5001"): body}),
            http=NoHttp(),
        )

    return context


def opendota_listing_my_matches() -> Context:
    """OpenDota serving the recorded match list; nothing polled yet."""
    listed = (FIXTURES_DIR / "match_list.json").read_bytes()
    url = f"https://api.opendota.com/api/players/{OWNER_FAKE_ACCOUNT_ID}/matches"
    return Context(
        db=RowsDatabase(),
        archive=NoArchive(),
        http=FakeHttp({("GET", url): response(listed)}),
    )


TICK = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


def just_after_the_recorded_match() -> datetime:
    """A clock an hour after match 5001 ended, so its analysis posts."""
    match = json.loads((FIXTURES_DIR / "match_parsed.json").read_bytes())
    ended = match["start_time"] + match["duration"]
    return datetime.fromtimestamp(ended + 3600, UTC)


ME = LinkedAccount(
    id=1, discord_user_id="user-1", external_id=str(OWNER_FAKE_ACCOUNT_ID)
)

CASES = [
    PluginCase(
        GankReportPlugin(clock=just_after_the_recorded_match),
        {
            "fetch_match": [
                Example(
                    FetchMatch(match_id=5001), opendota_serving("match_parsed.json")
                ),
                Example(
                    FetchMatch(match_id=5001), opendota_serving("match_unparsed.json")
                ),
                Example(
                    FetchMatch(match_id=5001),
                    opendota_serving("match_unparsed.json"),
                    attempt=3,
                ),
            ],
            "poll_matches": [
                Example(
                    ScheduledRun(scheduled_for=TICK, account=ME),
                    opendota_listing_my_matches,
                ),
                Example(ScheduledRun(scheduled_for=TICK)),
            ],
            "backfill": [
                Example(ME, opendota_listing_my_matches),
            ],
            "analyze_match": [
                Example(
                    AnalyzeMatch(match_id=5001, account=ME),
                    fetched_and_archived("match_parsed.json"),
                ),
                Example(
                    AnalyzeMatch(match_id=5001, account=ME),
                    fetched_and_archived(None),
                ),
                Example(
                    AnalyzeMatch(match_id=5001, account=ME),
                    fetched_and_archived(None),
                    attempt=24,
                ),
            ],
        },
    ),
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
