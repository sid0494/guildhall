"""The metrics Gank Report tracks: one small function each (docs/adr/0053).

A metric is a value per match and player; comparisons with a player's own
history happen when reports are built. A function returns None when the match
cannot give a value, such as a parsed-only metric on an unparsed match.
Adding a metric is a function and a line in `METRICS`, with no migration
(docs/adr/0054).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from guildhall.plugins.gank_report.opendota import Match, Player


@dataclass(frozen=True)
class Metric:
    name: str
    """Stable, stored in `gank_report_metrics.metric`; never rename one."""
    label: str
    """How reports show it."""
    higher_is_better: bool
    """Which way an arrow against the average counts as better."""
    needs_parse: bool
    """Whether only a replay-parsed match gives a value."""
    compute: Callable[[Match, Player], float | None]


def last_hits_at_10(match: Match, player: Player) -> float | None:
    # `lh_t` holds the running total at each minute, from minute 0.
    if player.lh_t is None or len(player.lh_t) <= 10:
        return None
    return float(player.lh_t[10])


def gold_per_minute(match: Match, player: Player) -> float | None:
    return float(player.gold_per_min)


def deaths_per_10_minutes(match: Match, player: Player) -> float | None:
    if match.duration <= 0:
        return None
    return player.deaths / (match.duration / 600)


def hero_damage_per_minute(match: Match, player: Player) -> float | None:
    if match.duration <= 0:
        return None
    return player.hero_damage / (match.duration / 60)


def observer_wards_placed(match: Match, player: Player) -> float | None:
    return None if player.obs_placed is None else float(player.obs_placed)


METRICS: Final = (
    Metric("last_hits_10", "Last hits at 10 min", True, True, last_hits_at_10),
    Metric("gpm", "GPM", True, False, gold_per_minute),
    Metric("deaths_per_10", "Deaths per 10 min", False, False, deaths_per_10_minutes),
    Metric(
        "hero_damage_per_min",
        "Hero damage per min",
        True,
        False,
        hero_damage_per_minute,
    ),
    Metric(
        "observer_wards", "Observer wards placed", True, True, observer_wards_placed
    ),
)

METRICS_BY_NAME: Final[Mapping[str, Metric]] = MappingProxyType(
    {metric.name: metric for metric in METRICS}
)
