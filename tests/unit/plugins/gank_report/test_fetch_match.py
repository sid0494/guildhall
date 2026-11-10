"""fetch_match waits for OpenDota's parse, then archives and records the match."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import Call, FakeHttp, NoArchive, NoDatabase, response
from record_opendota import FIXTURES_DIR
from sqlalchemy.sql.dml import UpdateBase

from guildhall.contract import (
    Context,
    HttpResponse,
    Job,
    Outcome,
    PermanentError,
)
from guildhall.plugins.gank_report import FetchMatch, GankReportPlugin

MATCH_ID = 5001
MATCH_URL = f"https://api.opendota.com/api/matches/{MATCH_ID}"
REQUEST_URL = f"https://api.opendota.com/api/request/{MATCH_ID}"
PARSED = (FIXTURES_DIR / "match_parsed.json").read_bytes()
UNPARSED = (FIXTURES_DIR / "match_unparsed.json").read_bytes()
PARSE_REQUESTED = (FIXTURES_DIR / "parse_request.json").read_bytes()


def opendota(
    match: HttpResponse, parse_request: HttpResponse | None = None
) -> FakeHttp:
    return FakeHttp(
        {
            ("GET", MATCH_URL): match,
            ("POST", REQUEST_URL): parse_request or response(PARSE_REQUESTED),
        }
    )


async def fetch(http: FakeHttp, attempt: int = 1) -> Outcome:
    job = Job[FetchMatch](
        type="fetch_match",
        payload=FetchMatch(match_id=MATCH_ID),
        dedupe_key=f"gank_report:match:{MATCH_ID}",
        attempt=attempt,
    )
    context = Context(db=NoDatabase(), archive=NoArchive(), http=http)
    return await GankReportPlugin().fetch_match(job, context)


def written(write: UpdateBase) -> dict[str, Any]:
    """The values a write inserts, by column, as Postgres would receive them."""
    params = write.compile().params
    return {name: value for name, value in params.items() if name in COLUMNS}


def sql(write: UpdateBase) -> str:
    return str(write.compile())


COLUMNS = {
    "match_id",
    "played_at",
    "duration_seconds",
    "game_mode",
    "lobby_type",
    "mode",
    "parsed",
}


async def test_a_parsed_match_is_archived_and_recorded_at_once() -> None:
    http = opendota(response(PARSED))
    outcome = await fetch(http)
    assert http.calls == [Call("GET", MATCH_URL)]
    assert outcome.retry_in is None
    [archive] = outcome.archives
    assert (archive.kind, archive.id) == ("match", str(MATCH_ID))
    assert archive.body == PARSED  # exactly as received
    [write] = outcome.writes
    match = json.loads(PARSED)
    assert written(write) == {
        "match_id": MATCH_ID,
        "played_at": datetime.fromtimestamp(match["start_time"], UTC),
        "duration_seconds": match["duration"],
        "game_mode": match["game_mode"],
        "lobby_type": match["lobby_type"],
        "mode": "normal",  # unranked Single Draft
        "parsed": True,
    }
    # Fetching a match again updates its row instead of failing.
    assert "ON CONFLICT (match_id) DO UPDATE SET" in sql(write)


@pytest.mark.parametrize(
    ("attempt", "wait"), [(1, timedelta(minutes=5)), (2, timedelta(minutes=10))]
)
async def test_an_unparsed_match_asks_for_a_parse_and_waits(
    attempt: int, wait: timedelta
) -> None:
    http = opendota(response(UNPARSED))
    outcome = await fetch(http, attempt)
    assert http.calls == [Call("GET", MATCH_URL), Call("POST", REQUEST_URL)]
    assert outcome == Outcome(retry_in=wait)


async def test_the_last_attempt_records_the_match_unparsed() -> None:
    http = opendota(response(UNPARSED))
    outcome = await fetch(http, attempt=3)
    assert http.calls == [Call("GET", MATCH_URL)]  # no point asking again
    assert outcome.retry_in is None
    assert [archive.body for archive in outcome.archives] == [UNPARSED]
    [write] = outcome.writes
    assert written(write)["parsed"] is False


async def test_a_failed_parse_request_still_waits() -> None:
    http = opendota(response(UNPARSED), parse_request=response({}, status=400))
    assert await fetch(http) == Outcome(retry_in=timedelta(minutes=5))


async def test_a_missing_match_fails_permanently() -> None:
    http = opendota(response({"error": "Not Found"}, status=404))
    with pytest.raises(PermanentError, match="match not found"):
        await fetch(http)


async def test_a_malformed_match_fails_permanently() -> None:
    http = opendota(response({"match_id": MATCH_ID}))
    with pytest.raises(PermanentError, match="unexpected OpenDota response"):
        await fetch(http)


async def test_a_different_match_fails_permanently() -> None:
    other = json.loads(PARSED) | {"match_id": MATCH_ID + 1}
    http = opendota(response(other))
    with pytest.raises(PermanentError, match="different match"):
        await fetch(http)


@pytest.mark.parametrize(
    "change",
    [{"game_mode": 18}, {"lobby_type": 1}, {"duration": 120}],
    ids=["ability-draft", "private-lobby", "two-minutes"],
)
async def test_an_untracked_match_fails_before_asking_for_a_parse(
    change: dict[str, int],
) -> None:
    http = opendota(response(json.loads(UNPARSED) | change))
    with pytest.raises(PermanentError, match="not in a tracked mode"):
        await fetch(http)
    assert http.calls == [Call("GET", MATCH_URL)]


async def test_a_turbo_match_is_recorded_as_turbo() -> None:
    turbo = json.loads(PARSED) | {"game_mode": 23, "lobby_type": 0}
    outcome = await fetch(opendota(response(turbo)))
    [write] = outcome.writes
    assert written(write)["mode"] == "turbo"
