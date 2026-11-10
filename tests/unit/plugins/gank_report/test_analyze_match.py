"""analyze_match waits for its match's fetch, then records the account's part."""

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fakes import DictArchive, NoArchive, NoHttp, RowsDatabase
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

MATCH_ID = 5001
FETCHED = [SimpleNamespace(match_id=MATCH_ID)]
PARSED = (FIXTURES_DIR / "match_parsed.json").read_bytes()
UNPARSED = (FIXTURES_DIR / "match_unparsed.json").read_bytes()
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
) -> Outcome:
    job = Job[AnalyzeMatch](
        type="analyze_match",
        payload=AnalyzeMatch(match_id=MATCH_ID, account=account),
        dedupe_key=f"gank_report:analyze:{MATCH_ID}:{account.id}",
        attempt=attempt,
    )
    context = Context(db=RowsDatabase(rows=rows), archive=archive, http=NoHttp())
    return await GankReportPlugin().analyze_match(job, context)


def written(write: UpdateBase) -> dict[str, Any]:
    params = write.compile().params
    columns = {"match_id", "account_id", "hero_id", "player_slot", "won"}
    return {name: value for name, value in params.items() if name in columns}


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
    [write] = outcome.writes
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
