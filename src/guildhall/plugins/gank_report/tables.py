"""Gank Report's tables (docs/adr/0045).

A match row is written once its fetch is final, parsed or not, so its
existence is what `analyze_match` waits for. Player rows are keyed by the
Dota account ID, so a player's history belongs to the game account.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Table,
    Text,
    func,
)

from guildhall.contract import plugin_metadata

metadata = plugin_metadata()

matches = Table(
    "gank_report_matches",
    metadata,
    # OpenDota's ID: never generated here (a lone integer primary key would
    # otherwise get a sequence).
    Column("match_id", BigInteger, primary_key=True, autoincrement=False),
    Column("played_at", DateTime(timezone=True), nullable=False),
    Column("duration_seconds", Integer, nullable=False),
    Column("game_mode", Integer, nullable=False),
    Column("lobby_type", Integer, nullable=False),
    Column("mode", Text, nullable=False),
    Column("parsed", Boolean, nullable=False),
    Column(
        "fetched_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("mode IN ('normal', 'turbo')", name="mode"),
)
"""One row per match whose fetch is final. `mode` is `normal` or `turbo`
(docs/adr/0051), derived from `game_mode`, `lobby_type` and the duration."""

player_matches = Table(
    "gank_report_player_matches",
    metadata,
    Column(
        "match_id",
        BigInteger,
        ForeignKey(matches.c.match_id),
        primary_key=True,
    ),
    Column("account_id", BigInteger, primary_key=True),
    Column("hero_id", Integer, nullable=False),
    Column("player_slot", Integer, nullable=False),
    Column("won", Boolean, nullable=False),
    Column(
        "analyzed_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
"""One row per linked account per match, written by its analysis."""
