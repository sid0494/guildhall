"""Gank Report's writes against real Postgres."""

from fakes import FakeHttp, NoArchive, NoDatabase, response
from record_opendota import FIXTURES_DIR
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from guildhall.contract import Context, Job, Outcome
from guildhall.plugins.gank_report import FetchMatch, GankReportPlugin
from guildhall.plugins.gank_report.tables import matches

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
