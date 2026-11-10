"""Gank Report reads the recorded OpenDota responses, and fails clearly on others."""

import json
from datetime import UTC, datetime

import pytest
from fakes import response
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID

from guildhall.contract import PermanentError
from guildhall.plugins.gank_report.opendota import (
    Match,
    check_status,
    match_url,
    parse,
    parse_request_url,
)


def fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_a_parsed_match_is_read() -> None:
    match = parse(Match, fixture("match_parsed.json"))
    assert match.parsed
    assert len(match.players) == 10
    assert match.played_at.tzinfo is UTC


def test_an_unparsed_match_is_read_although_it_has_no_version() -> None:
    match = parse(Match, fixture("match_unparsed.json"))
    assert not match.parsed
    assert len(match.players) == 10


def test_my_player_is_found_in_either_version() -> None:
    for name in ("match_unparsed.json", "match_parsed.json"):
        mine = parse(Match, fixture(name)).player(OWNER_FAKE_ACCOUNT_ID)
        assert mine is not None
        assert isinstance(mine.win, bool)


def test_a_player_who_hides_their_data_has_no_account() -> None:
    players = parse(Match, fixture("match_unparsed.json")).players
    assert any(player.account_id is None for player in players)


def test_a_missing_player_is_none() -> None:
    assert parse(Match, fixture("match_parsed.json")).player(999) is None


def test_played_at_is_the_start_time_in_utc() -> None:
    body = json.loads(fixture("match_parsed.json"))
    body["start_time"] = 1_700_000_000
    match = parse(Match, json.dumps(body).encode())
    assert match.played_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


def test_an_unexpected_body_fails_permanently_naming_fields_not_values() -> None:
    body = json.loads(fixture("match_parsed.json"))
    del body["players"][3]["hero_id"]
    body["duration"] = "secret-value"
    with pytest.raises(PermanentError) as raised:
        parse(Match, json.dumps(body).encode())
    message = str(raised.value)
    assert message.startswith("unexpected OpenDota response: ")
    assert "players.3.hero_id: Field required" in message
    assert "duration:" in message
    assert "secret-value" not in message
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_a_body_that_is_not_json_fails_permanently() -> None:
    with pytest.raises(PermanentError, match="unexpected OpenDota response"):
        parse(Match, b"<html>busy</html>")


def test_success_statuses_pass() -> None:
    check_status(response({}, status=200), "match")


@pytest.mark.parametrize(
    ("status", "message"),
    [(404, "match not found"), (400, "OpenDota answered 400 for match")],
)
def test_other_statuses_fail_permanently(status: int, message: str) -> None:
    with pytest.raises(PermanentError, match=message):
        check_status(response({"error": "x"}, status=status), "match")


def test_urls() -> None:
    assert match_url(5001) == "https://api.opendota.com/api/matches/5001"
    assert parse_request_url(5001) == "https://api.opendota.com/api/request/5001"
