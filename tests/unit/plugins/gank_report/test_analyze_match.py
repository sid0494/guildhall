"""analyze_match waits for its match's fetch, records the account's part and
metrics, and posts a recent match with the player's baselines."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import DictArchive, NoArchive, NoHttp, ScriptedDatabase
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID
from sqlalchemy.sql.dml import UpdateBase

from guildhall.contract import (
    Context,
    Job,
    LinkedAccount,
    Outcome,
    PermanentError,
)
from guildhall.contract.archive import RawArchive
from guildhall.plugins.gank_report import (
    ANALYZE_MAX_ATTEMPTS,
    AnalyzeMatch,
    GankReportPlugin,
)
from guildhall.plugins.gank_report.events import MatchAnalyzed, MetricResult

MATCH_ID = 5001
FETCHED = [SimpleNamespace(mode="normal")]
PARSED = (FIXTURES_DIR / "match_parsed.json").read_bytes()
UNPARSED = (FIXTURES_DIR / "match_unparsed.json").read_bytes()
_RECORDED = json.loads(PARSED)
PLAYED_AT = datetime.fromtimestamp(_RECORDED["start_time"], UTC)
ENDED = PLAYED_AT + timedelta(seconds=_RECORDED["duration"])
SOON_AFTER = ENDED + timedelta(hours=1)
LONG_AFTER = ENDED + timedelta(days=30)
ME = LinkedAccount(
    id=1, discord_user_id="user-1", external_id=str(OWNER_FAKE_ACCOUNT_ID)
)


def archived(body: bytes) -> DictArchive:
    return DictArchive({("match", str(MATCH_ID)): body})


async def analyze(
    rows: list[Any],
    archive: RawArchive,
    attempt: int = 1,
    account: LinkedAccount = ME,
    now: datetime = LONG_AFTER,
    baselines: Sequence[Any] | None = None,
) -> Outcome:
    """Run the analysis; the database answers the match-row read with `rows`
    and, if given, the baseline read with `baselines`."""
    job = Job[AnalyzeMatch](
        type="analyze_match",
        payload=AnalyzeMatch(match_id=MATCH_ID, account=account),
        dedupe_key=f"gank_report:analyze:{MATCH_ID}:{account.id}",
        attempt=attempt,
    )
    answers = [rows] if baselines is None else [rows, baselines]
    context = Context(db=ScriptedDatabase(*answers), archive=archive, http=NoHttp())
    return await GankReportPlugin(clock=lambda: now).analyze_match(job, context)


def written(write: UpdateBase) -> dict[str, Any]:
    params = write.compile().params
    columns = {"match_id", "account_id", "hero_id", "player_slot", "won"}
    return {name: value for name, value in params.items() if name in columns}


def metric_rows(write: UpdateBase) -> dict[str, Any]:
    """The `metric: value` pairs a multi-row metrics insert writes."""
    params = write.compile().params
    count = len([name for name in params if name.startswith("metric_m")])
    assert {params[f"account_id_m{i}"] for i in range(count)} == {OWNER_FAKE_ACCOUNT_ID}
    return {params[f"metric_m{i}"]: params[f"value_m{i}"] for i in range(count)}


def average(metric: str, value: float, games: int) -> SimpleNamespace:
    return SimpleNamespace(metric=metric, average=value, games=games)


def my_player(body: bytes) -> dict[str, Any]:
    players = json.loads(body)["players"]
    [mine] = [p for p in players if p.get("account_id") == OWNER_FAKE_ACCOUNT_ID]
    return dict(mine)


@pytest.mark.parametrize("attempt", [1, ANALYZE_MAX_ATTEMPTS - 1])
async def test_waits_while_the_match_is_not_fetched(attempt: int) -> None:
    # NoArchive: nothing is read until the match row exists.
    outcome = await analyze([], NoArchive(), attempt)
    assert outcome == Outcome(retry_in=timedelta(minutes=5))


async def test_gives_up_on_a_match_that_is_never_fetched() -> None:
    with pytest.raises(PermanentError, match="match was never fetched"):
        await analyze([], NoArchive(), ANALYZE_MAX_ATTEMPTS)


@pytest.mark.parametrize("body", [PARSED, UNPARSED], ids=["parsed", "unparsed"])
async def test_records_my_part_in_the_archived_match(body: bytes) -> None:
    archive = archived(body)
    outcome = await analyze(FETCHED, archive)
    assert archive.reads == [("match", str(MATCH_ID))]
    write, _ = outcome.writes
    mine = my_player(body)
    assert written(write) == {
        "match_id": MATCH_ID,
        "account_id": OWNER_FAKE_ACCOUNT_ID,
        "hero_id": mine["hero_id"],
        "player_slot": mine["player_slot"],
        "won": bool(mine["win"]),
    }
    assert "ON CONFLICT (match_id, account_id) DO UPDATE SET" in str(write.compile())


async def test_a_recorded_match_without_its_archive_fails_permanently() -> None:
    with pytest.raises(PermanentError, match="archive is missing"):
        await analyze(FETCHED, DictArchive())


async def test_an_account_missing_from_the_match_fails_permanently() -> None:
    stranger = ME.model_copy(update={"external_id": "999"})
    with pytest.raises(PermanentError, match="not among the match's players"):
        await analyze(FETCHED, archived(PARSED), account=stranger)


async def test_a_linked_account_that_is_not_a_dota_id_fails_permanently() -> None:
    odd = ME.model_copy(update={"external_id": "not-a-number"})
    with pytest.raises(PermanentError, match="not a Dota account ID"):
        await analyze(FETCHED, archived(PARSED), account=odd)


async def test_a_corrupt_archive_fails_permanently() -> None:
    with pytest.raises(PermanentError, match="unexpected OpenDota response"):
        await analyze(FETCHED, archived(b"{}"))


async def test_writes_every_metric_with_null_where_the_match_gives_none() -> None:
    parsed = await analyze(FETCHED, archived(PARSED))
    unparsed = await analyze(FETCHED, archived(UNPARSED))
    _, write = parsed.writes
    assert metric_rows(write) == {
        "last_hits_10": 60.0,
        "gpm": 499.0,
        "deaths_per_10": pytest.approx(4 / (1729 / 600)),
        "hero_damage_per_min": pytest.approx(27_327 / (1729 / 60)),
        "observer_wards": 0.0,
    }
    _, write = unparsed.writes
    rows = metric_rows(write)
    assert (rows["last_hits_10"], rows["observer_wards"]) == (None, None)
    assert rows["gpm"] == 499.0
    assert "ON CONFLICT (match_id, account_id, metric) DO UPDATE" in str(
        write.compile()
    )


async def test_an_old_match_is_recorded_without_a_post_or_a_baseline_read() -> None:
    # The script holds only the match-row answer: a baseline read would fail.
    outcome = await analyze(FETCHED, archived(PARSED), now=LONG_AFTER)
    assert len(outcome.writes) == 2
    assert outcome.events == ()


async def test_a_recent_match_is_posted_with_my_baselines() -> None:
    baselines = [average("gpm", 450.0, 12), average("last_hits_10", 52.5, 4)]
    outcome = await analyze(
        FETCHED, archived(PARSED), now=SOON_AFTER, baselines=baselines
    )
    [event] = outcome.events
    assert isinstance(event, MatchAnalyzed)
    assert event.dedupe_key == f"gank_report:match_analyzed:{MATCH_ID}:1"
    assert (event.match_id, event.discord_user_id, event.hero_id) == (
        MATCH_ID,
        "user-1",
        42,
    )
    assert (event.won, event.mode, event.parsed) == (False, "normal", True)
    assert event.played_at == PLAYED_AT
    assert [m.name for m in event.metrics] == [
        "last_hits_10",
        "gpm",
        "deaths_per_10",
        "hero_damage_per_min",
        "observer_wards",
    ]
    by_name = {m.name: m for m in event.metrics}
    assert by_name["gpm"] == MetricResult(
        name="gpm", value=499.0, baseline=450.0, baseline_games=12
    )
    assert by_name["last_hits_10"].baseline == 52.5
    # No earlier game with a value: no baseline, and render can say so.
    assert by_name["observer_wards"] == MetricResult(
        name="observer_wards", value=0.0, baseline=None, baseline_games=0
    )


async def test_a_baseline_with_no_valued_games_is_none() -> None:
    outcome = await analyze(
        FETCHED,
        archived(PARSED),
        now=SOON_AFTER,
        baselines=[average("last_hits_10", 0.0, 0)],
    )
    [event] = outcome.events
    assert isinstance(event, MatchAnalyzed)
    [last_hits] = [m for m in event.metrics if m.name == "last_hits_10"]
    assert (last_hits.baseline, last_hits.baseline_games) == (None, 0)


@pytest.mark.parametrize(
    ("after_end", "posted"),
    [(timedelta(hours=12), True), (timedelta(hours=12, seconds=1), False)],
    ids=["exactly-12-hours", "just-over"],
)
async def test_the_post_window_is_12_hours_from_the_end(
    after_end: timedelta, posted: bool
) -> None:
    outcome = await analyze(
        FETCHED, archived(PARSED), now=ENDED + after_end, baselines=[]
    )
    assert bool(outcome.events) is posted


async def test_the_post_compares_within_the_matchs_mode() -> None:
    db_answers: list[Sequence[Any]] = [[SimpleNamespace(mode="turbo")], []]
    database = ScriptedDatabase(*db_answers)
    job = Job[AnalyzeMatch](
        type="analyze_match",
        payload=AnalyzeMatch(match_id=MATCH_ID, account=ME),
        dedupe_key=f"gank_report:analyze:{MATCH_ID}:1",
        attempt=1,
    )
    context = Context(db=database, archive=archived(PARSED), http=NoHttp())
    plugin = GankReportPlugin(clock=lambda: SOON_AFTER)
    outcome = await plugin.analyze_match(job, context)
    [event] = outcome.events
    assert isinstance(event, MatchAnalyzed)
    assert event.mode == "turbo"
    baseline_query = str(database.statements[1].compile())
    assert "gank_report_matches.mode =" in baseline_query
    assert database.statements[1].compile().params["mode_1"] == "turbo"
