"""The fixture recorder trims responses, fakes IDs and follows a match to its parse."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from record_opendota import (
    FAKE_ACCOUNT_IDS,
    FAKE_MATCH_IDS,
    FIXTURE_SHAPES,
    KEEP,
    OWNER_FAKE_ACCOUNT_ID,
    PARSE_MAX_POLLS,
    Pseudonyms,
    RecordingError,
    ShapeError,
    record,
    scrub,
    trim,
    write_fixtures,
)

REAL_OWNER = 123456789
REAL_STRANGER = 987654321
REAL_MATCH = 8000000001
OLDER_MATCH = 8000000000


class TestTrim:
    def test_keeps_only_named_fields(self) -> None:
        assert trim({"a": 1, "secret": "x"}, {"a": KEEP}) == {"a": 1}

    def test_never_adds_a_missing_field(self) -> None:
        assert trim({"a": 1}, {"a": KEEP, "lh_t": KEEP}) == {"a": 1}

    def test_none_stays_none(self) -> None:
        assert trim(
            {"a": None, "nested": None}, {"a": KEEP, "nested": {"b": KEEP}}
        ) == {
            "a": None,
            "nested": None,
        }

    def test_trims_nested_objects_and_each_list_item(self) -> None:
        value = {
            "players": [{"hero_id": 1, "personaname": "x"}, {"hero_id": 2, "name": "y"}]
        }
        assert trim(value, {"players": {"hero_id": KEEP}}) == {
            "players": [{"hero_id": 1}, {"hero_id": 2}]
        }

    def test_a_top_level_list_is_trimmed_item_by_item(self) -> None:
        assert trim([{"a": 1, "b": 2}], {"a": KEEP}) == [{"a": 1}]

    def test_keeps_a_list_of_scalars(self) -> None:
        assert trim({"lh_t": [0, 2, 5]}, {"lh_t": KEEP}) == {"lh_t": [0, 2, 5]}

    @pytest.mark.parametrize("value", [{"chat": "hi"}, [{"chat": "hi"}]])
    def test_refuses_to_keep_an_object_whole(self, value: Any) -> None:
        with pytest.raises(ShapeError, match="'od_data' holds an object"):
            trim({"od_data": value}, {"od_data": KEEP})

    def test_refuses_a_scalar_where_an_object_belongs(self) -> None:
        with pytest.raises(ShapeError, match="expected an object, got str"):
            trim({"od_data": "x"}, {"od_data": {"has_parsed": KEEP}})


class TestPseudonyms:
    def test_the_owner_gets_the_owner_fake(self) -> None:
        pseudonyms = Pseudonyms(REAL_OWNER)
        assert pseudonyms.apply({"account_id": REAL_OWNER}) == {
            "account_id": OWNER_FAKE_ACCOUNT_ID
        }

    def test_the_same_id_gets_the_same_fake_at_any_depth(self) -> None:
        pseudonyms = Pseudonyms(REAL_OWNER)
        first = pseudonyms.apply({"match_id": REAL_MATCH})
        nested = pseudonyms.apply([{"players": [{"account_id": REAL_STRANGER}]}])
        again = pseudonyms.apply({"match_id": REAL_MATCH, "account_id": REAL_STRANGER})
        assert first == {"match_id": FAKE_MATCH_IDS[0]}
        assert nested == [{"players": [{"account_id": FAKE_ACCOUNT_IDS[1]}]}]
        assert again == {
            "match_id": FAKE_MATCH_IDS[0],
            "account_id": FAKE_ACCOUNT_IDS[1],
        }

    def test_different_ids_get_different_fakes(self) -> None:
        pseudonyms = Pseudonyms(REAL_OWNER)
        assert pseudonyms.apply(
            [{"match_id": REAL_MATCH}, {"match_id": OLDER_MATCH}]
        ) == [
            {"match_id": FAKE_MATCH_IDS[0]},
            {"match_id": FAKE_MATCH_IDS[1]},
        ]

    def test_an_anonymous_player_stays_anonymous(self) -> None:
        assert Pseudonyms(REAL_OWNER).apply({"account_id": None}) == {
            "account_id": None
        }

    def test_leaves_other_fields_alone(self) -> None:
        assert Pseudonyms(REAL_OWNER).apply({"hero_id": 5, "version": 22}) == {
            "hero_id": 5,
            "version": 22,
        }

    def test_refuses_an_id_that_is_not_an_integer(self) -> None:
        with pytest.raises(ShapeError, match="expected an integer ID, got str"):
            Pseudonyms(REAL_OWNER).apply({"match_id": "8000000001"})

    def test_fails_when_the_fakes_run_out(self) -> None:
        pseudonyms = Pseudonyms(REAL_OWNER)
        many = [{"match_id": real} for real in range(len(FAKE_MATCH_IDS) + 1)]
        with pytest.raises(ShapeError, match="ran out of fake IDs"):
            pseudonyms.apply(many)


def raw_match(*, parsed: bool) -> dict[str, Any]:
    """A match as OpenDota sends it, identifying fields included."""
    player: dict[str, Any] = {
        "account_id": REAL_OWNER,
        "personaname": "real-name",
        "player_slot": 0,
        "hero_id": 8,
        "gold_per_min": 500,
    }
    if parsed:
        player |= {"lh_t": [0, 4, 9], "obs_placed": 3}
    # Like OpenDota, an unparsed match has no `version`, and a player who hides
    # their match data has no `account_id`.
    match: dict[str, Any] = {
        "match_id": REAL_MATCH,
        "replay_salt": 42,
        "od_data": {"has_parsed": parsed, "has_api": True},
        "chat": [{"type": "chat", "key": "gg"}],
        "players": [player, {"player_slot": 128, "hero_id": 1}],
    }
    if parsed:
        match["version"] = 22
    return match


MATCH_LIST: list[dict[str, Any]] = [
    {"match_id": REAL_MATCH, "version": None, "hero_id": 8, "party_size": 1},
    {"match_id": OLDER_MATCH, "version": 22, "hero_id": 1, "party_size": 1},
]


def opendota(
    polls_until_parsed: int, match_list: list[dict[str, Any]] = MATCH_LIST
) -> tuple[httpx.Client, list[str]]:
    """A fake OpenDota whose match parses on the given poll, and its request log."""
    seen: list[str] = []
    match_fetches = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal match_fetches
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == f"/api/players/{REAL_OWNER}/matches":
            assert request.url.params["limit"] == "20"
            return httpx.Response(200, json=match_list)
        if request.url.path == f"/api/request/{REAL_MATCH}":
            return httpx.Response(200, json={"job": {"jobId": 77}})
        if request.url.path == f"/api/matches/{REAL_MATCH}":
            match_fetches += 1
            # The first fetch is before the parse request; later ones are polls.
            return httpx.Response(
                200, json=raw_match(parsed=match_fetches > polls_until_parsed)
            )
        return httpx.Response(404)

    client = httpx.Client(
        base_url="https://api.opendota.com/api", transport=httpx.MockTransport(handle)
    )
    return client, seen


def no_sleep(seconds: float) -> None:
    del seconds


def quiet(message: str) -> None:
    del message


class TestRecord:
    def test_records_the_latest_unparsed_match_before_and_after_its_parse(self) -> None:
        client, seen = opendota(polls_until_parsed=2)
        responses = record_with(client)
        assert seen == [
            f"GET /api/players/{REAL_OWNER}/matches",
            f"GET /api/matches/{REAL_MATCH}",
            f"POST /api/request/{REAL_MATCH}",
            f"GET /api/matches/{REAL_MATCH}",
            f"GET /api/matches/{REAL_MATCH}",
        ]
        assert list(responses) == list(FIXTURE_SHAPES)
        assert "version" not in responses["match_unparsed.json"]
        assert responses["match_parsed.json"]["version"] == 22
        assert responses["parse_request.json"] == {"job": {"jobId": 77}}

    def test_waits_between_polls(self) -> None:
        client, _ = opendota(polls_until_parsed=3)
        sleeps: list[float] = []
        record_with(client, sleep=sleeps.append)
        assert sleeps == [30, 30, 30]

    def test_an_empty_match_list_points_at_the_privacy_setting(self) -> None:
        client, _ = opendota(polls_until_parsed=1, match_list=[])
        with pytest.raises(RecordingError, match="Expose Public Match Data"):
            record_with(client)

    def test_fails_when_every_recent_match_is_parsed(self) -> None:
        client, _ = opendota(polls_until_parsed=1, match_list=[MATCH_LIST[1]])
        with pytest.raises(RecordingError, match="all parsed already"):
            record_with(client)

    def test_gives_up_on_a_match_that_never_parses(self) -> None:
        client, seen = opendota(polls_until_parsed=PARSE_MAX_POLLS + 1)
        with pytest.raises(RecordingError, match="not parsed within 15 minutes"):
            record_with(client)
        assert seen.count(f"GET /api/matches/{REAL_MATCH}") == PARSE_MAX_POLLS + 1

    def test_an_error_status_names_neither_account_nor_match(self) -> None:
        client = httpx.Client(
            base_url="https://api.opendota.com/api",
            transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        )
        with pytest.raises(RecordingError) as raised:
            record_with(client)
        assert str(raised.value) == "OpenDota answered GET with 500"


def record_with(
    client: httpx.Client, sleep: Callable[[float], None] = no_sleep
) -> dict[str, Any]:
    return record(client, REAL_OWNER, sleep=sleep, progress=quiet)


class TestScrub:
    def test_scrubbed_fixtures_hold_no_real_identity(self, tmp_path: Path) -> None:
        client, _ = opendota(polls_until_parsed=1)
        write_fixtures(scrub(record_with(client), REAL_OWNER), tmp_path)
        written = "".join(path.read_text() for path in tmp_path.iterdir())
        for real in (
            REAL_OWNER,
            REAL_MATCH,
            OLDER_MATCH,
            "real-name",
            "replay_salt",
            "chat",
        ):
            assert str(real) not in written

    def test_both_versions_of_the_match_share_its_list_fake(self) -> None:
        client, _ = opendota(polls_until_parsed=1)
        fixtures = scrub(record_with(client), REAL_OWNER)
        listed = fixtures["match_list.json"][0]["match_id"]
        assert listed == FAKE_MATCH_IDS[0]
        assert fixtures["match_unparsed.json"]["match_id"] == listed
        assert fixtures["match_parsed.json"]["match_id"] == listed
        players = fixtures["match_parsed.json"]["players"]
        assert players[0]["account_id"] == OWNER_FAKE_ACCOUNT_ID
        assert "account_id" not in players[1]

    def test_the_unparsed_match_keeps_lacking_parsed_fields(self) -> None:
        client, _ = opendota(polls_until_parsed=1)
        fixtures = scrub(record_with(client), REAL_OWNER)
        assert "lh_t" not in fixtures["match_unparsed.json"]["players"][0]
        assert fixtures["match_parsed.json"]["players"][0]["lh_t"] == [0, 4, 9]
        assert fixtures["match_parsed.json"]["od_data"] == {"has_parsed": True}
