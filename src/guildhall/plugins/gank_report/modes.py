"""Which matches Gank Report tracks, and as which kind (docs/adr/0051).

Ranked and unranked games are compared together as `normal`; Turbo, with its
different economy and pace, is kept apart. Everything else (Ability Draft,
1v1, events, bot games, private and tournament lobbies) and games shorter than
five minutes (remakes, early abandons) are not tracked.
"""

from enum import StrEnum
from typing import Final


class Mode(StrEnum):
    NORMAL = "normal"
    TURBO = "turbo"


NORMAL_GAME_MODES: Final = frozenset(
    {
        1,  # All Pick
        2,  # Captains Mode
        3,  # Random Draft
        4,  # Single Draft
        5,  # All Random
        12,  # Least Played
        16,  # Captains Draft
        17,  # Balanced Draft
        22,  # All Draft (ranked All Pick)
    }
)
"""Game modes OpenDota's constants mark as balanced."""

MATCHMAKING_LOBBIES: Final = frozenset(
    {
        0,  # unranked
        5,  # ranked team (legacy)
        6,  # ranked solo (legacy)
        7,  # ranked
        9,  # Battle Cup
    }
)

TURBO_GAME_MODE: Final = 23
UNRANKED_LOBBY: Final = 0
MIN_DURATION_SECONDS: Final = 5 * 60


def tracked_mode(game_mode: int, lobby_type: int, duration_seconds: int) -> Mode | None:
    """The kind of match this is, or None if Gank Report does not track it."""
    if duration_seconds < MIN_DURATION_SECONDS:
        return None
    if game_mode == TURBO_GAME_MODE and lobby_type == UNRANKED_LOBBY:
        return Mode.TURBO
    if game_mode in NORMAL_GAME_MODES and lobby_type in MATCHMAKING_LOBBIES:
        return Mode.NORMAL
    return None
