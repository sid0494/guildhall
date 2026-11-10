"""Gank Report's writes against real Postgres."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes import DictArchive, FakeHttp, NoArchive, NoDatabase, NoHttp, response
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.contract import (
    Context,
    Database,
    Job,
    LinkedAccount,
    Outcome,
    ScheduledRun,
)
from guildhall.platform.storage import PluginDatabase
from guildhall.plugins.gank_report import AnalyzeMatch, FetchMatch, GankReportPlugin
from guildhall.plugins.gank_report.tables import (
    matches,
    player_matches,
    polled_matches,
)

MATCH_URL = "https://api.opendota.com/api/matches/5001"


async def fetched(fixture: str, attempt: int) -> Outcome:
    http = FakeHttp(
        {("GET", MATCH_URL): response((FIXTURES_DIR / fixture).read_bytes())}
    )
    job = Job[FetchMatch](
        type="fetch_match",
        payload=FetchMatch(match_id=5001),
        dedupe_key="gank_report:match:5001",
        attempt=attempt,
    )
    context = Context(db=NoDatabase(), archive=NoArchive(), http=http)
    return await GankReportPlugin().fetch_match(job, context)


async def run_writes(connection: AsyncConnection, outcome: Outcome) -> None:
    for write in outcome.writes:
        await connection.execute(write)


async def test_fetching_a_match_again_updates_its_row(
    connection: AsyncConnection,
) -> None:
    await run_writes(connection, await fetched("match_unparsed.json", attempt=3))
    first = (await connection.execute(select(matches))).one()
    await run_writes(connection, await fetched("match_parsed.json", attempt=1))
    rows = (await connection.execute(select(matches))).all()
    assert len(rows) == 1
    [again] = rows
    assert (first.parsed, again.parsed) == (False, True)
    assert again.played_at == first.played_at
    assert again.played_at.tzinfo is not None


@pytest.fixture
async def fetched_match(engine: AsyncEngine) -> AsyncIterator[bytes]:
    """Match 5001 fetched and committed, so a separate read can see it.

    Request it before `connection`: fixtures are torn down in reverse order,
    and deleting the match while that connection's uncommitted player row
    still references it would wait on the row lock forever.
    """
    outcome = await fetched("match_parsed.json", attempt=1)
    async with engine.begin() as connection:
        await run_writes(connection, outcome)
    yield outcome.archives[0].body
    async with engine.begin() as connection:
        await connection.execute(delete(player_matches))
        await connection.execute(delete(matches))


async def analyzed(engine: AsyncEngine, body: bytes) -> Outcome:
    me = LinkedAccount(
        id=1, discord_user_id="user-1", external_id=str(OWNER_FAKE_ACCOUNT_ID)
    )
    job = Job[AnalyzeMatch](
        type="analyze_match",
        payload=AnalyzeMatch(match_id=5001, account=me),
        dedupe_key="gank_report:analyze:5001:1",
        attempt=1,
    )
    plugin = GankReportPlugin()
    context = Context(
        db=PluginDatabase(engine, plugin),
        archive=DictArchive({("match", "5001"): body}),
        http=NoHttp(),
    )
    return await plugin.analyze_match(job, context)


async def test_analysing_reads_the_match_row_and_records_my_part_once(
    engine: AsyncEngine, fetched_match: bytes, connection: AsyncConnection
) -> None:
    outcome = await analyzed(engine, fetched_match)
    await run_writes(connection, outcome)
    await run_writes(connection, outcome)  # analysing again overwrites
    rows = (await connection.execute(select(player_matches))).all()
    assert [(row.match_id, row.account_id) for row in rows] == [
        (5001, OWNER_FAKE_ACCOUNT_ID)
    ]


async def test_a_player_row_needs_its_match_row(
    engine: AsyncEngine, fetched_match: bytes, connection: AsyncConnection
) -> None:
    outcome = await analyzed(engine, fetched_match)
    async with engine.begin() as other:
        await other.execute(delete(matches))
    with pytest.raises(IntegrityError, match="fk_gank_report_player_matches"):
        await run_writes(connection, outcome)


async def column_default(connection: AsyncConnection, table: str, column: str) -> Any:
    return await connection.scalar(
        text(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )


async def test_match_ids_come_from_opendota_never_a_sequence(
    connection: AsyncConnection,
) -> None:
    assert await column_default(connection, "gank_report_matches", "match_id") is None


async def test_postgres_refuses_a_mode_other_than_normal_or_turbo(
    connection: AsyncConnection,
) -> None:
    # Alembic cannot detect check constraints, so the drift test cannot either.
    row = {
        "match_id": 1,
        "played_at": datetime(2026, 11, 10, tzinfo=UTC),
        "duration_seconds": 1800,
        "game_mode": 1,
        "lobby_type": 0,
        "parsed": False,
    }
    await connection.execute(insert(matches).values({**row, "mode": "turbo"}))
    with pytest.raises(IntegrityError, match="ck_gank_report_matches_mode"):
        await connection.execute(
            insert(matches).values({**row, "match_id": 2, "mode": "casual"})
        )


LIST_URL = f"https://api.opendota.com/api/players/{OWNER_FAKE_ACCOUNT_ID}/matches"
LISTED = json.loads((FIXTURES_DIR / "match_list.json").read_bytes())
LISTED_IDS = [match["match_id"] for match in LISTED]


async def polled(db: Database) -> Outcome:
    me = LinkedAccount(
        id=1, discord_user_id="user-1", external_id=str(OWNER_FAKE_ACCOUNT_ID)
    )
    job = Job[ScheduledRun](
        type="poll_matches",
        payload=ScheduledRun(
            scheduled_for=datetime(2026, 11, 10, 18, 0, tzinfo=UTC), account=me
        ),
        dedupe_key="gank_report:schedule:poll_matches:2026-11-10T18:00Z:1",
        attempt=1,
    )
    http = FakeHttp({("GET", LIST_URL): response(LISTED)})
    context = Context(db=db, archive=NoArchive(), http=http)
    return await GankReportPlugin().poll_matches(job, context)


@pytest.fixture
async def some_polled(engine: AsyncEngine) -> AsyncIterator[list[int]]:
    """The first five listed matches already polled for my account, committed."""
    first = LISTED_IDS[:5]
    async with engine.begin() as connection:
        await connection.execute(
            insert(polled_matches),
            [{"account_id": OWNER_FAKE_ACCOUNT_ID, "match_id": m} for m in first],
        )
    yield first
    async with engine.begin() as connection:
        await connection.execute(delete(polled_matches))


async def test_a_poll_reads_what_was_polled_and_queues_the_rest(
    engine: AsyncEngine, some_polled: list[int]
) -> None:
    outcome = await polled(PluginDatabase(engine, GankReportPlugin()))
    queued = [job.payload for job in outcome.jobs if job.type == "fetch_match"]
    assert queued == [FetchMatch(match_id=m) for m in LISTED_IDS[5:]]


async def test_overlapping_polls_mark_each_match_once(
    engine: AsyncEngine, connection: AsyncConnection
) -> None:
    outcome = await polled(PluginDatabase(engine, GankReportPlugin()))
    await run_writes(connection, outcome)
    await run_writes(connection, outcome)  # a second poll that read before the first
    rows = (await connection.execute(select(polled_matches.c.match_id))).all()
    assert sorted(row.match_id for row in rows) == sorted(LISTED_IDS)
