"""Which matches are tracked, and as normal or Turbo."""

import pytest

from guildhall.plugins.gank_report.modes import Mode, tracked_mode

LONG_ENOUGH = 30 * 60


@pytest.mark.parametrize(
    ("game_mode", "lobby_type", "expected"),
    [
        (22, 7, Mode.NORMAL),  # ranked All Draft
        (1, 0, Mode.NORMAL),  # unranked All Pick
        (4, 0, Mode.NORMAL),  # unranked Single Draft
        (2, 9, Mode.NORMAL),  # Captains Mode in Battle Cup
        (22, 6, Mode.NORMAL),  # legacy ranked solo
        (23, 0, Mode.TURBO),
        (23, 1, None),  # Turbo in a private lobby
        (1, 1, None),  # practice (private) lobby
        (2, 2, None),  # tournament lobby
        (1, 4, None),  # bots
        (18, 0, None),  # Ability Draft
        (21, 8, None),  # 1v1 Mid
        (19, 12, None),  # event
        (0, 0, None),  # unknown mode
    ],
)
def test_modes_and_lobbies(
    game_mode: int, lobby_type: int, expected: Mode | None
) -> None:
    assert tracked_mode(game_mode, lobby_type, LONG_ENOUGH) is expected


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(299, None), (300, Mode.NORMAL), (301, Mode.NORMAL)],
)
def test_five_minutes_is_the_floor(duration: int, expected: Mode | None) -> None:
    assert tracked_mode(1, 0, duration) is expected


def test_short_turbo_games_are_not_tracked_either() -> None:
    assert tracked_mode(23, 0, 299) is None
