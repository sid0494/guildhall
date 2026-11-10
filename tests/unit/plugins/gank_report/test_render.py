"""The post-match report's embed, case by case."""

from datetime import UTC, datetime
from typing import Any

import pytest

from guildhall.contract import Embed, EmbedAuthor
from guildhall.plugins.gank_report import GankReportPlugin
from guildhall.plugins.gank_report.events import MatchAnalyzed, MetricResult
from guildhall.plugins.gank_report.heroes import hero_icon_url
from guildhall.plugins.gank_report.render import LOSS_COLOUR, WIN_COLOUR, render_match

PLAYED = datetime(2026, 11, 10, 18, 30, tzinfo=UTC)


def result(
    name: str, value: float | None, baseline: float | None = None, games: int = 0
) -> MetricResult:
    return MetricResult(name=name, value=value, baseline=baseline, baseline_games=games)


def event(**change: Any) -> MatchAnalyzed:
    fields: dict[str, Any] = {
        "dedupe_key": "gank_report:match_analyzed:5001:1",
        "match_id": 5001,
        "discord_user_id": "1234",
        "hero_id": 42,
        "won": True,
        "mode": "normal",
        "parsed": True,
        "played_at": PLAYED,
        "duration_seconds": 1729,
        "metrics": (result("gpm", 499.0, 471.0, 12),),
    }
    return MatchAnalyzed(**(fields | change))


def embed_of(analysed: MatchAnalyzed) -> Embed:
    [embed] = render_match(analysed).embeds
    return embed


def values(analysed: MatchAnalyzed) -> dict[str, str]:
    return {field.name: field.value for field in embed_of(analysed).fields}


def test_the_post_mentions_the_player_and_only_them() -> None:
    message = render_match(event())
    assert (message.content, message.mentions) == ("<@1234>", ("1234",))


def test_a_win_is_green_with_hero_icon_result_duration_and_kind() -> None:
    embed = embed_of(event())
    assert embed.author == EmbedAuthor(name="Wraith King", icon_url=hero_icon_url(42))
    assert embed.title == "🏆 Win · 28:49 · Normal"
    assert embed.colour == WIN_COLOUR
    assert embed.timestamp == PLAYED
    assert embed.description == "Compared with your recent normal games on Wraith King."


def test_a_turbo_loss_is_red() -> None:
    embed = embed_of(event(won=False, mode="turbo"))
    assert (embed.title, embed.colour) == ("💀 Loss · 28:49 · Turbo", LOSS_COLOUR)


@pytest.mark.parametrize(
    ("metric", "value", "baseline", "shown"),
    [
        ("gpm", 499.0, 471.0, "499 ▲ (avg 471)"),
        ("gpm", 430.0, 471.0, "430 ▼ (avg 471)"),
        ("deaths_per_10", 1.4, 1.1, "1.4 ▼ (avg 1.1)"),  # more deaths is worse
        ("deaths_per_10", 0.9, 1.1, "0.9 ▲ (avg 1.1)"),
        ("gpm", 499.4, 499.0, "499 ─ (avg 499)"),  # looks equal, so "same"
        ("hero_damage_per_min", 1240.0, 1110.0, "1,240 ▲ (avg 1,110)"),
    ],
    ids=[
        "higher-better",
        "lower-worse",
        "deaths-up",
        "deaths-down",
        "same",
        "thousands",
    ],
)
def test_each_metric_is_compared_with_the_players_average(
    metric: str, value: float, baseline: float, shown: str
) -> None:
    analysed = event(metrics=(result(metric, value, baseline, 12),))
    [field] = embed_of(analysed).fields
    assert field.value == shown
    assert field.inline


def test_metric_names_carry_their_emoji_and_label() -> None:
    analysed = event(metrics=(result("last_hits_10", 60.0, 52.0, 4),))
    assert list(values(analysed)) == ["🗡️ Last hits at 10 min"]


def test_a_first_game_on_the_hero_says_so() -> None:
    analysed = event(metrics=(result("gpm", 499.0),))
    assert values(analysed) == {"💰 GPM": "499 (first game)"}
    assert embed_of(analysed).description == (
        "Your first normal game on Wraith King that Gank Report has seen."
    )


def test_a_parse_only_metric_on_an_unparsed_match_says_why_it_is_missing() -> None:
    analysed = event(parsed=False, metrics=(result("observer_wards", None),))
    assert values(analysed) == {"👁️ Observer wards placed": "— (needs a parsed match)"}


def test_a_missing_value_on_a_parsed_match_is_a_dash() -> None:
    analysed = event(metrics=(result("last_hits_10", None),))  # under 10 minutes
    assert values(analysed) == {"🗡️ Last hits at 10 min": "—"}


def test_a_metric_since_removed_is_left_out() -> None:
    analysed = event(metrics=(result("retired_metric", 3.0), result("gpm", 499.0)))
    assert list(values(analysed)) == ["💰 GPM"]


def test_an_event_stored_before_durations_still_renders() -> None:
    analysed = MatchAnalyzed.model_validate(
        event().model_dump(mode="json", exclude={"duration_seconds"})
    )
    assert embed_of(analysed).title == "🏆 Win · Normal"


def test_a_hero_newer_than_the_table_renders_by_number_without_an_icon() -> None:
    assert embed_of(event(hero_id=9999)).author == EmbedAuthor(name="Hero 9999")


def test_the_plugin_renders_its_own_events_and_nothing_else() -> None:
    plugin = GankReportPlugin()
    assert plugin.render(event()) == render_match(event())


def test_a_parse_only_metric_without_an_average_is_the_first_parsed_game() -> None:
    # Earlier games on the hero exist (GPM has an average) but none was parsed.
    analysed = event(
        metrics=(result("last_hits_10", 56.0), result("gpm", 1599.0, 1244.0, 9))
    )
    assert values(analysed) == {
        "🗡️ Last hits at 10 min": "56 (first parsed game)",
        "💰 GPM": "1,599 ▲ (avg 1,244)",
    }
