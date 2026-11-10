"""Gank Report's writes against real Postgres."""

from collections.abc import AsyncIterator

import pytest
from fakes import DictArchive, FakeHttp, NoArchive, NoDatabase, NoHttp, response
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from guildhall.contract import Context, Job, LinkedAccount, Outcome
from guildhall.platform.storage import PluginDatabase
from guildhall.plugins.gank_report import AnalyzeMatch, FetchMatch, GankReportPlugin
from guildhall.plugins.gank_report.tables import matches, player_matches

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
