"""The post-match report, as Discord shows it (docs/adr/0074).

Pure formatting of a `MatchAnalyzed` event: no I/O, so the platform can render
posts without waiting on anything. The hero's icon and name head the card, the
title gives the result, and a compact table compares each stat with the
player's average on that hero in the same kind of game.
"""

from dataclasses import dataclass
from typing import Final

from guildhall.contract import Embed, EmbedAuthor, Message
from guildhall.plugins.gank_report.events import MatchAnalyzed, MetricResult
from guildhall.plugins.gank_report.heroes import hero_icon_url, hero_name
from guildhall.plugins.gank_report.metrics import METRICS_BY_NAME, Metric

WIN_COLOUR: Final = 0x2ECC71
LOSS_COLOUR: Final = 0xE74C3C
KIND: Final = {"normal": "Normal", "turbo": "Turbo"}
EMOJI_WIDTH: Final = 2
"""Columns an emoji takes in Discord's monospace blocks; the header line,
which has none, is indented by this much so its titles line up."""
BETTER: Final = "🟢"
WORSE: Final = "🔴"
NEUTRAL: Final = "⚪"
MARKER_WIDTH: Final = EMOJI_WIDTH + 1
"""A colour (better or worse) and an arrow (the actual direction): colour
alone fails red-green colour blindness, and an arrow alone confuses "down"
with "worse" for deaths."""


@dataclass(frozen=True)
class _Row:
    metric: Metric
    value: str
    arrow: str
    average: str


def render_match(event: MatchAnalyzed, mention: bool = True) -> Message:
    """The report; `mention=False` for a command's reply, where the person
    asking needs no notification."""
    hero = hero_name(event.hero_id)
    kind = KIND[event.mode]
    rows: list[_Row] = []
    unparsed: list[str] = []
    for result in event.metrics:
        metric = METRICS_BY_NAME.get(result.name)
        if metric is None:
            continue  # a metric since removed
        if result.value is None and metric.needs_parse and not event.parsed:
            unparsed.append(metric.label)
            continue
        rows.append(_row(metric, result))

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
                description=f"```\n{_table(rows)}\n```" if rows else None,
                colour=WIN_COLOUR if event.won else LOSS_COLOUR,
                footer=context,
                timestamp=event.played_at,
            ),
        ),
    )


def _row(metric: Metric, result: MetricResult) -> _Row:
    if result.value is None:
        return _Row(metric, "—", "", "")  # e.g. no last hits at 10 in a short game
    shown = _number(result.value, metric.decimals)
    if result.baseline is None:
        return _Row(metric, shown, NEUTRAL + "·", "first")
    average = _number(result.baseline, metric.decimals)
    return _Row(metric, shown, _marker(metric, shown, average, result), average)


def _table(rows: list[_Row]) -> str:
    """Aligned columns: emoji and label, your value, marker, your average."""
    label = max(len(row.metric.label) for row in rows)
    value = max(len("you"), *(len(row.value) for row in rows))
    average = max(len("avg"), *(len(row.average) for row in rows))
    indent = " " * (EMOJI_WIDTH + 1 + label + 2)
    gap = " " * (2 + MARKER_WIDTH + 2)
    header = f"{indent}{'you':>{value}}{gap}{'avg':>{average}}"
    lines = [header] + [
        f"{row.metric.emoji} {row.metric.label:<{label}}  {row.value:>{value}}"
        f"  {row.arrow}  {row.average:>{average}}".rstrip()
        for row in rows
    ]
    return "\n".join(lines)


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def _marker(metric: Metric, shown: str, average: str, result: MetricResult) -> str:
    """Green for better, red for worse, with the actual direction: fewer
    deaths is 🟢▼. Compared as displayed, so equal-looking numbers are ⚪─."""
    if shown == average or result.value is None or result.baseline is None:
        return NEUTRAL + "─"
    higher = result.value > result.baseline
    colour = BETTER if higher == metric.higher_is_better else WORSE
    return colour + ("▲" if higher else "▼")


def _number(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}"
