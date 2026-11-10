"""Recorded OpenDota fixtures hold only allowlisted fields and fake IDs.

The repository is public, so this guards against a scrubber bug or a hand
edit publishing a real player's or match's ID (docs/adr/0042). It reads the
files on its own terms, without the recorder's trim or pseudonym code, so a
bug there cannot hide itself. It also pins what tests may rely on: the
unparsed and parsed fixtures are one match, from the list, with "my player" in
it.
"""

import json
from collections.abc import Iterator
from functools import cache
from typing import Any

import pytest
from record_opendota import (
    FAKE_ACCOUNT_IDS,
    FAKE_MATCH_IDS,
    FIXTURE_SHAPES,
    FIXTURES_DIR,
    KEEP,
    OWNER_FAKE_ACCOUNT_ID,
    Shape,
)

FAKE_IDS = {"account_id": FAKE_ACCOUNT_IDS, "match_id": FAKE_MATCH_IDS}


@cache
def load(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def paths(value: Any, prefix: str = "") -> Iterator[str]:
    """Every field's path, with list items as `[]`: `players[].hero_id`."""
    if isinstance(value, list):
        for item in value:
            yield from paths(item, f"{prefix}[]")
    elif isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            yield path
            yield from paths(item, path)


def allowed_paths(shape: Shape, prefix: str = "") -> Iterator[str]:
    """The paths a shape allows, with `[]` for any list it may be applied to."""
    for key, inner in shape.items():
        for path in (
            (f"{prefix}.{key}", f"{prefix}[].{key}") if prefix else (key, f"[].{key}")
        ):
            yield path
            if inner is not KEEP:
                yield from allowed_paths(inner, path)


def id_values(value: Any) -> Iterator[tuple[str, Any]]:
    """Every value of a field named like an ID, at any depth."""
    if isinstance(value, list):
        for item in value:
            yield from id_values(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in FAKE_IDS:
                yield key, item
            yield from id_values(item)


def test_exactly_the_declared_fixtures_are_recorded() -> None:
    recorded = {path.name for path in FIXTURES_DIR.iterdir()}
    assert recorded == set(FIXTURE_SHAPES)


@pytest.mark.parametrize("name", FIXTURE_SHAPES)
def test_fixture_holds_only_allowlisted_fields(name: str) -> None:
    unexpected = set(paths(load(name))) - set(allowed_paths(FIXTURE_SHAPES[name]))
    assert not unexpected


@pytest.mark.parametrize("name", FIXTURE_SHAPES)
def test_fixture_ids_are_fake(name: str) -> None:
    for field, value in id_values(load(name)):
        anonymous_player = field == "account_id" and value is None
        assert anonymous_player or value in FAKE_IDS[field], f"real-looking {field}"


def test_both_match_fixtures_are_the_listed_match_before_and_after_parsing() -> None:
    unparsed, parsed = load("match_unparsed.json"), load("match_parsed.json")
    assert unparsed["match_id"] == parsed["match_id"]
    assert unparsed["match_id"] in {
        match["match_id"] for match in load("match_list.json")
    }
    # OpenDota leaves `version` out of an unparsed match rather than sending null.
    assert "version" not in unparsed
    assert unparsed["od_data"] == {"has_parsed": False}
    assert parsed["version"] is not None
    assert parsed["od_data"] == {"has_parsed": True}


@pytest.mark.parametrize("name", ["match_unparsed.json", "match_parsed.json"])
def test_my_player_is_in_the_match_in_my_listed_slot(name: str) -> None:
    match = load(name)
    listed = {listed["match_id"]: listed for listed in load("match_list.json")}
    mine = [
        player
        for player in match["players"]
        if player.get("account_id") == OWNER_FAKE_ACCOUNT_ID
    ]
    assert len(mine) == 1
    assert mine[0]["player_slot"] == listed[match["match_id"]]["player_slot"]


def test_the_unparsed_match_leaves_out_hidden_players_account_ids() -> None:
    # Players who hide their match data have no `account_id` at all until the
    # match is parsed; the fixture keeps that case for the tests that need it.
    players = load("match_unparsed.json")["players"]
    assert any("account_id" not in player for player in players)
