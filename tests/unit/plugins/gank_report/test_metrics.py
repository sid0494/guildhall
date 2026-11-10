"""Each metric's value from the recorded matches, and when it has none."""

import json
import re

import pytest
from record_opendota import FIXTURES_DIR, OWNER_FAKE_ACCOUNT_ID

from guildhall.plugins.gank_report.metrics import METRICS, METRICS_BY_NAME, Metric
from guildhall.plugins.gank_report.opendota import Match, Player, parse

PARSED = parse(Match, (FIXTURES_DIR / "match_parsed.json").read_bytes())
UNPARSED = parse(Match, (FIXTURES_DIR / "match_unparsed.json").read_bytes())


def mine(match: Match) -> Player:
    player = match.player(OWNER_FAKE_ACCOUNT_ID)
    assert player is not None
    return player


def value(name: str, match: Match = PARSED) -> float | None:
    return METRICS_BY_NAME[name].compute(match, mine(match))


def test_values_from_the_parsed_match() -> None:
    # 28:49 game: 60 last hits at 10:00, 499 GPM, 4 deaths, 27,327 hero damage,
    # no observer wards.
    assert value("last_hits_10") == 60
    assert value("gpm") == 499
    assert value("deaths_per_10") == pytest.approx(4 / (1729 / 600))
    assert value("hero_damage_per_min") == pytest.approx(27_327 / (1729 / 60))
    assert value("observer_wards") == 0  # none placed is a value, not missing


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.name)
def test_needs_parse_matches_what_an_unparsed_match_gives(metric: Metric) -> None:
    assert metric.compute(PARSED, mine(PARSED)) is not None
    unparsed = metric.compute(UNPARSED, mine(UNPARSED))
    assert (unparsed is None) is metric.needs_parse


def test_a_game_that_ends_before_ten_minutes_has_no_last_hits_at_10() -> None:
    body = json.loads((FIXTURES_DIR / "match_parsed.json").read_bytes())
    for player in body["players"]:
        player["lh_t"] = player["lh_t"][:10]  # minutes 0 to 9 only
    short = parse(Match, json.dumps(body).encode())
    assert value("last_hits_10", short) is None


def test_names_are_unique_identifiers() -> None:
    names = [metric.name for metric in METRICS]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names)


def test_only_deaths_are_better_lower() -> None:
    assert [m.name for m in METRICS if not m.higher_is_better] == ["deaths_per_10"]
