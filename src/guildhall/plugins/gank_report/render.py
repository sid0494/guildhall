"""The post-match report, as Discord shows it (docs/adr/0071).

Pure formatting of a `MatchAnalyzed` event: no I/O, so the platform can render
posts without waiting on anything. The hero's icon and name head the card, the
title gives the result, and each metric is compared with the player's average
on that hero in the same kind of game.
"""

from typing import Final

from guildhall.contract import Embed, EmbedAuthor, EmbedField, Message
from guildhall.plugins.gank_report.events import MatchAnalyzed, MetricResult
from guildhall.plugins.gank_report.heroes import hero_icon_url, hero_name
from guildhall.plugins.gank_report.metrics import METRICS_BY_NAME, Metric

WIN_COLOUR: Final = 0x2ECC71
LOSS_COLOUR: Final = 0xE74C3C
KIND: Final = {"normal": "Normal", "turbo": "Turbo"}


def render_match(event: MatchAnalyzed, mention: bool = True) -> Message:
    """The report; `mention=False` for a command's reply, where the person
    asking needs no notification."""
    hero = hero_name(event.hero_id)
    title = " · ".join(
        part
        for part in (
            "🏆 Win" if event.won else "💀 Loss",
            _duration(event.duration_seconds),
            KIND[event.mode],
        )
        if part
    )
    fields = tuple(
        _field(METRICS_BY_NAME[result.name], result, event.parsed)
        for result in event.metrics
        if result.name in METRICS_BY_NAME  # a metric since removed is skipped
    )
    compared = any(result.baseline_games for result in event.metrics)
    description = (
        f"Compared with your recent {event.mode} games on {hero}."
        if compared
        else f"Your first {event.mode} game on {hero} that Gank Report has seen."
    )
    return Message(
        content=f"<@{event.discord_user_id}>" if mention else "",
        mentions=(event.discord_user_id,) if mention else (),
        embeds=(
            Embed(
                author=EmbedAuthor(name=hero, icon_url=hero_icon_url(event.hero_id)),
                title=title,
                description=description,
                colour=WIN_COLOUR if event.won else LOSS_COLOUR,
                fields=fields,
                timestamp=event.played_at,
            ),
        ),
    )


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def _field(metric: Metric, result: MetricResult, parsed: bool) -> EmbedField:
    return EmbedField(
        name=f"{metric.emoji} {metric.label}".strip(),
        value=_value(metric, result, parsed),
        inline=True,
    )


def _value(metric: Metric, result: MetricResult, parsed: bool) -> str:
    if result.value is None:
        if metric.needs_parse and not parsed:
            return "— (needs a parsed match)"
        return "—"
    shown = _number(result.value, metric.decimals)
    if result.baseline is None:
        # Earlier games may exist but lack this metric: parse-only metrics
        # only have values from parsed matches.
        return (
            f"{shown} (first parsed game)"
            if metric.needs_parse
            else f"{shown} (first game)"
        )
    average = _number(result.baseline, metric.decimals)
    return f"{shown} {_arrow(metric, shown, average, result)} (avg {average})"


def _arrow(metric: Metric, shown: str, average: str, result: MetricResult) -> str:
    """Compared as displayed: equal-looking numbers are "same", never ▲ or ▼."""
    if shown == average or result.value is None or result.baseline is None:
        return "─"
    higher = result.value > result.baseline
    return "▲" if higher == metric.higher_is_better else "▼"


def _number(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}"
