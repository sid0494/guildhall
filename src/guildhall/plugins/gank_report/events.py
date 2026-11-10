"""Events Gank Report emits for the platform to deliver to Discord.

`render()` does no I/O, so an event carries everything its message shows: a
snapshot taken when the match is analysed (docs/adr/0055).
"""

from datetime import datetime
from typing import Literal

from guildhall.contract import ContractModel, Event


class MetricResult(ContractModel):
    name: str
    """A name from `metrics.METRICS`; its label and direction come from there."""
    value: float | None
    """None when the match gives no value (unparsed, or too short)."""
    baseline: float | None
    """The average over the player's previous games on this hero and kind (at
    most 20); None when there are none with a value."""
    baseline_games: int
    """How many of those games had a value."""


class MatchAnalyzed(Event):
    """A tracked match has been analysed for a linked account."""

    type: Literal["gank_report.match_analyzed"] = "gank_report.match_analyzed"
    schema_version: Literal[1] = 1
    match_id: int
    discord_user_id: str
    hero_id: int
    won: bool
    mode: Literal["normal", "turbo"]
    parsed: bool
    played_at: datetime
    duration_seconds: int | None = None
    """Added within schema version 1, so events stored before it lack it."""
    metrics: tuple[MetricResult, ...]
