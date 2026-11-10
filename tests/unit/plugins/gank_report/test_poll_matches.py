"""poll_matches queues each new tracked match of an account once."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import Call, FakeHttp, NoArchive, NoDatabase, RowsDatabase, response
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID

from guildhall.contract import (
    Context,
    Database,
    Job,
    LinkedAccount,
    Outcome,
    PermanentError,
    Schedule,
    ScheduledRun,
)
from guildhall.plugins.gank_report import AnalyzeMatch, FetchMatch, GankReportPlugin

LIST_URL = f"https://api.opendota.com/api/players/{OWNER_FAKE_ACCOUNT_ID}/matches"
LISTED: list[dict[str, Any]] = json.loads(
    (FIXTURES_DIR / "match_list.json").read_bytes()
)
LISTED_IDS = [match["match_id"] for match in LISTED]
ME = LinkedAccount(
    id=7, discord_user_id="user-1", external_id=str(OWNER_FAKE_ACCOUNT_ID)
)
TICK = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


async def poll(
    listed: list[dict[str, Any]] = LISTED,
    db: Database | None = None,
    account: LinkedAccount | None = ME,
) -> tuple[Outcome, FakeHttp]:
    http = FakeHttp({("GET", LIST_URL): response(listed)})
    job = Job[ScheduledRun](
        type="poll_matches",
        payload=ScheduledRun(scheduled_for=TICK, account=account),
        dedupe_key="gank_report:schedule:poll_matches:2026-11-10T18:00Z:7",
        attempt=1,
    )
    context = Context(db=db or RowsDatabase(), archive=NoArchive(), http=http)
    return await GankReportPlugin().poll_matches(job, context), http


def seen(*match_ids: int) -> RowsDatabase:
    return RowsDatabase(rows=[SimpleNamespace(match_id=m) for m in match_ids])


def polled_rows(outcome: Outcome) -> list[tuple[int, int]]:
    [write] = outcome.writes
    params = write.compile().params
    count = len([name for name in params if name.startswith("match_id")])
    return [
        (params[f"account_id_m{i}"], params[f"match_id_m{i}"]) for i in range(count)
    ]


def test_polls_every_15_minutes_per_account() -> None:
    assert GankReportPlugin().schedules() == [
        Schedule(job_type="poll_matches", cron="*/15 * * * *", per_account=True)
    ]


async def test_asks_for_the_20_newest_matches_of_the_past_year_turbo_included() -> None:
    _, http = await poll()
    assert http.calls == [
        Call("GET", LIST_URL, {"limit": 20, "date": 365, "significant": 0})
    ]


async def test_queues_a_fetch_and_a_delayed_analysis_for_each_new_match() -> None:
    outcome, _ = await poll()
    assert outcome.retry_in is None
    assert len(outcome.jobs) == 2 * len(LISTED_IDS)
    fetches, analyses = outcome.jobs[0::2], outcome.jobs[1::2]
    assert [job.payload for job in fetches] == [
        FetchMatch(match_id=match_id) for match_id in LISTED_IDS
    ]
    assert [job.dedupe_key for job in fetches] == [
        f"gank_report:match:{match_id}" for match_id in LISTED_IDS
    ]
    assert [job.payload for job in analyses] == [
        AnalyzeMatch(match_id=match_id, account=ME) for match_id in LISTED_IDS
    ]
    assert [job.dedupe_key for job in analyses] == [
        f"gank_report:analyze:{match_id}:7" for match_id in LISTED_IDS
    ]
    assert {job.delay for job in analyses} == {timedelta(minutes=5)}
    assert {job.delay for job in fetches} == {timedelta(0)}


async def test_dedupe_keys_never_hold_the_dota_account_id() -> None:
    outcome, _ = await poll()
    assert all(str(OWNER_FAKE_ACCOUNT_ID) not in job.dedupe_key for job in outcome.jobs)


async def test_marks_the_queued_matches_as_polled_in_the_same_outcome() -> None:
    outcome, _ = await poll()
    assert polled_rows(outcome) == [
        (OWNER_FAKE_ACCOUNT_ID, match_id) for match_id in LISTED_IDS
    ]
    [write] = outcome.writes
    assert "ON CONFLICT DO NOTHING" in str(write.compile())


async def test_skips_matches_already_polled_for_the_account() -> None:
    db = seen(*LISTED_IDS[:18])
    outcome, _ = await poll(db=db)
    assert [job.payload for job in outcome.jobs[0::2]] == [
        FetchMatch(match_id=match_id) for match_id in LISTED_IDS[18:]
    ]
    assert polled_rows(outcome) == [
        (OWNER_FAKE_ACCOUNT_ID, match_id) for match_id in LISTED_IDS[18:]
    ]
    [statement] = db.statements
    assert "gank_report_polled_matches" in str(statement)


async def test_does_nothing_when_every_match_was_polled() -> None:
    outcome, _ = await poll(db=seen(*LISTED_IDS))
    assert outcome == Outcome()


async def test_skips_untracked_matches_without_reading_the_database() -> None:
    untracked = [
        LISTED[0] | {"game_mode": 18},  # Ability Draft
        LISTED[1] | {"lobby_type": 1},  # private lobby
        LISTED[2] | {"duration": 299},  # under five minutes
    ]
    outcome, _ = await poll(listed=untracked, db=NoDatabase())
    assert outcome == Outcome()


async def test_turbo_matches_are_queued() -> None:
    turbo = [LISTED[0] | {"game_mode": 23, "lobby_type": 0}]
    outcome, _ = await poll(listed=turbo)
    assert [job.payload for job in outcome.jobs[0::2]] == [
        FetchMatch(match_id=LISTED_IDS[0])
    ]


async def test_an_empty_list_does_nothing() -> None:
    outcome, _ = await poll(listed=[], db=NoDatabase())
    assert outcome == Outcome()


async def test_a_match_listed_twice_is_queued_once() -> None:
    outcome, _ = await poll(listed=[LISTED[0], LISTED[0]])
    assert len(outcome.jobs) == 2


async def test_a_run_without_an_account_fails_permanently() -> None:
    with pytest.raises(PermanentError, match="once per linked account"):
        await poll(account=None)


async def test_a_malformed_list_fails_permanently() -> None:
    with pytest.raises(PermanentError, match="unexpected OpenDota response"):
        await poll(listed=[{"match_id": 1}])
