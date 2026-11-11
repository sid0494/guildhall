"""The post-match report: a compact table in an embed, case by case."""

from datetime import UTC, datetime
from typing import Any

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


SNIPER_WIN = (
    result("last_hits_10", 56.0),
    result("gpm", 1599.0, 1244.0, 9),
    result("deaths_per_10", 0.5, 2.2, 9),
    result("hero_damage_per_min", 1529.0, 1224.0, 9),
    result("observer_wards", 1.0),
)


def event(**change: Any) -> MatchAnalyzed:
    fields: dict[str, Any] = {
        "dedupe_key": "gank_report:match_analyzed:5001:1",
        "match_id": 5001,
        "discord_user_id": "1234",
        "hero_id": 35,
        "won": True,
        "mode": "turbo",
        "parsed": True,
        "played_at": PLAYED,
        "duration_seconds": 1300,
        "metrics": SNIPER_WIN,
    }
    return MatchAnalyzed(**(fields | change))


def embed_of(analysed: MatchAnalyzed, mention: bool = True) -> Embed:
    [embed] = render_match(analysed, mention).embeds
    return embed


def values(analysed: MatchAnalyzed) -> dict[str, str]:
    return {field.name: field.value for field in embed_of(analysed).fields}


def test_the_post_mentions_the_player_and_only_them() -> None:
    message = render_match(event())
    assert (message.content, message.mentions) == ("<@1234>", ("1234",))


def test_a_reply_mentions_nobody() -> None:
    message = render_match(event(), mention=False)
    assert (message.content, message.mentions) == ("", ())


def test_the_card_has_the_hero_result_duration_kind_and_comparison() -> None:
    embed = embed_of(event())
    assert embed.author == EmbedAuthor(name="Sniper", icon_url=hero_icon_url(35))
    assert embed.title == "🏆 Win · 21:40 · Turbo"
    assert embed.colour == WIN_COLOUR
    assert embed.timestamp == PLAYED
    assert embed.footer == "vs. your Turbo games on Sniper"
    assert embed.description is None


def test_a_loss_is_red() -> None:
    embed = embed_of(event(won=False, mode="normal"))
    assert (embed.title, embed.colour) == ("💀 Loss · 21:40 · Normal", LOSS_COLOUR)


def test_each_stat_has_its_own_full_width_row() -> None:
    assert values(event()) == {
        "🗡️ Last Hits @ 10 min": "56 ⚪· first",
        "💰 Gold per min": "1,599 🟢▲ (avg 1,244)",
        "💀 Deaths per 10 min": "0.5 🟢▼ (avg 2.2)",  # fewer deaths: down, and good
        "💥 Hero Damage per min": "1,529 🟢▲ (avg 1,224)",
        "👁️ Observer Wards": "1 ⚪· first",
    }
    assert not any(field.inline for field in embed_of(event()).fields)


def test_colour_says_better_or_worse_and_the_arrow_says_which_way() -> None:
    analysed = event(
        metrics=(
            result("gpm", 430.0, 471.0, 5),  # lower gold is worse
            result("deaths_per_10", 1.4, 1.1, 5),  # more deaths is worse
            result("hero_damage_per_min", 499.4, 499.0, 5),  # looks equal: same
        )
    )
    assert list(values(analysed).values()) == [
        "430 🔴▼ (avg 471)",
        "1.4 🔴▲ (avg 1.1)",
        "499 ⚪─ (avg 499)",
    ]


def test_stats_an_unparsed_match_lacks_are_left_out_and_named_in_the_footer() -> None:
    analysed = event(
        parsed=False,
        metrics=(
            result("last_hits_10", None),
            result("gpm", 499.0, 471.0, 5),
            result("observer_wards", None),
        ),
    )
    assert values(analysed) == {"💰 Gold per min": "499 🟢▲ (avg 471)"}
    assert embed_of(analysed).footer == (
        "vs. your Turbo games on Sniper · not parsed, so no Last Hits @ 10 min"
        " or Observer Wards"
    )


def test_a_missing_value_on_a_parsed_match_shows_a_dash() -> None:
    analysed = event(
        metrics=(result("last_hits_10", None), result("gpm", 499.0, 471.0, 5))
    )
    assert values(analysed)["🗡️ Last Hits @ 10 min"] == "—"


def test_a_first_game_on_the_hero_says_so() -> None:
    analysed = event(metrics=(result("gpm", 499.0),))
    assert values(analysed) == {"💰 Gold per min": "499 ⚪· first"}
    assert embed_of(analysed).footer == (
        "Your first Turbo game on Sniper that Gank Report has seen"
    )


def test_a_metric_since_removed_is_left_out() -> None:
    analysed = event(metrics=(result("retired_metric", 3.0), result("gpm", 499.0)))
    assert list(values(analysed)) == ["💰 Gold per min"]


def test_an_event_stored_before_durations_still_renders() -> None:
    analysed = MatchAnalyzed.model_validate(
        event().model_dump(mode="json", exclude={"duration_seconds"})
    )
    assert embed_of(analysed).title == "🏆 Win · Turbo"


def test_a_hero_newer_than_the_table_renders_by_number_without_an_icon() -> None:
    assert embed_of(event(hero_id=9999)).author == EmbedAuthor(name="Hero 9999")


def test_the_plugin_renders_its_own_events() -> None:
    assert GankReportPlugin().render(event()) == render_match(event())
