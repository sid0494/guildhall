"""`/link dota`: reading the ID, checking the account, and the backfill."""

import json
from typing import Any

import pytest
from fakes import Call, FakeHttp, NoArchive, NoDatabase, RowsDatabase, response
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID

from guildhall.contract import (
    Accepted,
    Context,
    Job,
    LinkedAccount,
    LinkRequest,
    PermanentError,
    Rejected,
)
from guildhall.plugins.gank_report import (
    NO_MATCHES_VISIBLE,
    NOT_AN_ACCOUNT,
    FetchMatch,
    GankReportPlugin,
)
from guildhall.plugins.gank_report.account_ids import STEAM_ID_OFFSET, dota_account_id

LIST_URL = f"https://api.opendota.com/api/players/{OWNER_FAKE_ACCOUNT_ID}/matches"
LISTED: list[dict[str, Any]] = json.loads(
    (FIXTURES_DIR / "match_list.json").read_bytes()
)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("123456789", 123456789),
        ("  123456789 ", 123456789),
        (str(STEAM_ID_OFFSET + 123456789), 123456789),  # a 17-digit Steam ID
        ("4294967295", 4294967295),  # the largest account ID
        ("4294967296", None),
        ("0", None),
        ("", None),
        ("12a", None),
        ("-5", None),
        ("123 456", None),
        ("\uff11\uff12\uff13", None),  # full-width digits: decimal to Python, not us
    ],
)
def test_reading_an_account_id(typed: str, expected: int | None) -> None:
    assert dota_account_id(typed) == expected


async def verify(typed: str, listed: list[dict[str, Any]]) -> tuple[Any, FakeHttp]:
    http = FakeHttp({("GET", LIST_URL): response(listed)})
    context = Context(db=NoDatabase(), archive=NoArchive(), http=http)
    request = LinkRequest(discord_user_id="user-1", external_id=typed)
    return await GankReportPlugin().verify_account(request, context), http


async def test_an_account_with_visible_matches_is_accepted_normalised() -> None:
    steam_id = str(STEAM_ID_OFFSET + OWNER_FAKE_ACCOUNT_ID)
    verdict, http = await verify(steam_id, LISTED[:1])
    assert verdict == Accepted(external_id=str(OWNER_FAKE_ACCOUNT_ID))
    assert http.calls == [Call("GET", LIST_URL, {"limit": 1, "significant": 0})]


async def test_an_account_without_visible_matches_is_rejected_with_the_fix() -> None:
    verdict, _ = await verify(str(OWNER_FAKE_ACCOUNT_ID), [])
    assert verdict == Rejected(reason=NO_MATCHES_VISIBLE)
    assert "Expose Public Match Data" in NO_MATCHES_VISIBLE


async def test_something_that_is_not_an_id_is_rejected_without_a_call() -> None:
    verdict, http = await verify("my-gamer-tag", LISTED)
    assert verdict == Rejected(reason=NOT_AN_ACCOUNT)
    assert http.calls == []


async def test_an_opendota_error_fails_the_link_permanently() -> None:
    http = FakeHttp({("GET", LIST_URL): response({"error": "x"}, status=400)})
    context = Context(db=NoDatabase(), archive=NoArchive(), http=http)
    request = LinkRequest(
        discord_user_id="user-1", external_id=str(OWNER_FAKE_ACCOUNT_ID)
    )
    with pytest.raises(PermanentError):
        await GankReportPlugin().verify_account(request, context)


def test_dota_links_are_declared_with_a_backfill() -> None:
    link = GankReportPlugin().account_link()
    assert link is not None
    assert (link.kind, link.on_link) == ("dota", "backfill")
    assert len(link.description) <= 100


async def test_the_backfill_queues_the_100_newest_tracked_matches_not_yet_polled() -> (
    None
):
    me = LinkedAccount(
        id=3, discord_user_id="user-1", external_id=str(OWNER_FAKE_ACCOUNT_ID)
    )
    http = FakeHttp({("GET", LIST_URL): response(LISTED)})
    context = Context(db=RowsDatabase(), archive=NoArchive(), http=http)
    job = Job[LinkedAccount](
        type="backfill", payload=me, dedupe_key="gank_report:on_link:i-1", attempt=1
    )
    outcome = await GankReportPlugin().backfill(job, context)
    assert http.calls == [Call("GET", LIST_URL, {"limit": 100, "significant": 0})]
    assert [spec.payload for spec in outcome.jobs[0::2]] == [
        FetchMatch(match_id=match["match_id"]) for match in LISTED
    ]
    assert [spec.dedupe_key for spec in outcome.jobs[1::2]] == [
        f"gank_report:analyze:{match['match_id']}:3" for match in LISTED
    ]
    assert len(outcome.writes) == 1  # marked polled with the jobs
