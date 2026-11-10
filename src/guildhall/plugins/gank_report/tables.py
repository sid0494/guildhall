"""Gank Report's tables (docs/adr/0045).

A match row is written once its fetch is final, parsed or not, so its
existence is what `analyze_match` waits for. Player rows are keyed by the
Dota account ID, so a player's history belongs to the game account.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Table,
    func,
)

from guildhall.contract import plugin_metadata

metadata = plugin_metadata()

matches = Table(
    "gank_report_matches",
    metadata,
    Column("match_id", BigInteger, primary_key=True),
    Column("played_at", DateTime(timezone=True), nullable=False),
    Column("duration_seconds", Integer, nullable=False),
    Column("game_mode", Integer, nullable=False),
    Column("lobby_type", Integer, nullable=False),
    Column("parsed", Boolean, nullable=False),
    Column(
        "fetched_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
"""One row per match whose fetch is final."""

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
