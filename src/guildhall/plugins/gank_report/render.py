"""The post-match report, as Discord shows it (docs/adr/0074).

Pure formatting of a `MatchAnalyzed` event: no I/O, so the platform can render
posts without waiting on anything. The hero's icon and name head the card, the
title gives the result, and each stat has its own full-width field comparing it
with the player's average on that hero in the same kind of game.
"""

from typing import Final

from guildhall.contract import Embed, EmbedAuthor, EmbedField, Message
from guildhall.plugins.gank_report.events import MatchAnalyzed, MetricResult
from guildhall.plugins.gank_report.heroes import hero_icon_url, hero_name
from guildhall.plugins.gank_report.metrics import METRICS_BY_NAME, Metric

WIN_COLOUR: Final = 0x2ECC71
LOSS_COLOUR: Final = 0xE74C3C
KIND: Final = {"normal": "Normal", "turbo": "Turbo"}
BETTER: Final = "🟢"
WORSE: Final = "🔴"
NEUTRAL: Final = "⚪"


def render_match(event: MatchAnalyzed, mention: bool = True) -> Message:
    """The report; `mention=False` for a command's reply, where the person
    asking needs no notification."""
    hero = hero_name(event.hero_id)
    kind = KIND[event.mode]
    fields: list[EmbedField] = []
    unparsed: list[str] = []
    for result in event.metrics:
        metric = METRICS_BY_NAME.get(result.name)
        if metric is None:
            continue  # a metric since removed
        if result.value is None and metric.needs_parse and not event.parsed:
            unparsed.append(metric.label)
            continue
        fields.append(
            EmbedField(
                name=f"{metric.emoji} {metric.label}",
                value=_value(metric, result),
                inline=False,  # one stat per row, on desktop as on phones
            )
        )

    compared = any(result.baseline_games for result in event.metrics)
    context = (
        f"vs. your {kind} games on {hero}"
        if compared
        else f"Your first {kind} game on {hero} that Gank Report has seen"
    )
    if unparsed:
        context += f" · not parsed, so no {' or '.join(unparsed)}"
    title = " · ".join(
        part
        for part in (
            "🏆 Win" if event.won else "💀 Loss",
            _duration(event.duration_seconds),
            kind,
        )
        if part
    )
    return Message(
        content=f"<@{event.discord_user_id}>" if mention else "",
        mentions=(event.discord_user_id,) if mention else (),
        embeds=(
            Embed(
                author=EmbedAuthor(name=hero, icon_url=hero_icon_url(event.hero_id)),
                title=title,
                fields=tuple(fields),
                colour=WIN_COLOUR if event.won else LOSS_COLOUR,
                footer=context,
                timestamp=event.played_at,
            ),
        ),
    )


def _value(metric: Metric, result: MetricResult) -> str:
    if result.value is None:
        return "—"  # e.g. no last hits at 10 in a short game
    shown = _number(result.value, metric.decimals)
    if result.baseline is None:
        return f"{shown} {NEUTRAL}· first"
    average = _number(result.baseline, metric.decimals)
    return f"{shown} {_marker(metric, shown, average, result)} (avg {average})"


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def _marker(metric: Metric, shown: str, average: str, result: MetricResult) -> str:
    """Green for better, red for worse, with the actual direction: fewer
    deaths is 🟢▼. Colour alone would fail red-green colour blindness; an arrow
    alone reads "down" as "worse" for deaths. Compared as displayed, so
    equal-looking numbers are ⚪─."""
    if shown == average or result.value is None or result.baseline is None:
        return NEUTRAL + "─"
    higher = result.value > result.baseline
    colour = BETTER if higher == metric.higher_is_better else WORSE
    return colour + ("▲" if higher else "▼")


def _number(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}"
