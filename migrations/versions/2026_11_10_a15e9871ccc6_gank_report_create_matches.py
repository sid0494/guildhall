"""gank_report create matches

Revision ID: a15e9871ccc6
Revises: d8fc9bd46c68
Create Date: 2026-11-10 12:19:58.165315
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a15e9871ccc6"
down_revision: str | Sequence[str] | None = "d8fc9bd46c68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gank_report_matches",
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("game_mode", sa.Integer(), nullable=False),
        sa.Column("lobby_type", sa.Integer(), nullable=False),
        sa.Column("parsed", sa.Boolean(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("match_id", name=op.f("pk_gank_report_matches")),
    )
    op.create_table(
        "gank_report_player_matches",
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("hero_id", sa.Integer(), nullable=False),
        sa.Column("player_slot", sa.Integer(), nullable=False),
        sa.Column("won", sa.Boolean(), nullable=False),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["gank_report_matches.match_id"],
            name=op.f("fk_gank_report_player_matches_match_id_gank_report_matches"),
        ),
        sa.PrimaryKeyConstraint(
            "match_id", "account_id", name=op.f("pk_gank_report_player_matches")
        ),
    )


def downgrade() -> None:
    op.drop_table("gank_report_player_matches")
    op.drop_table("gank_report_matches")
