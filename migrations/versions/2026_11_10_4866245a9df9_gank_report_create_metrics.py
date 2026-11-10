"""gank_report create metrics

Revision ID: 4866245a9df9
Revises: e01b3b59536a
Create Date: 2026-11-10 13:23:32.653605
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4866245a9df9"
down_revision: str | Sequence[str] | None = "e01b3b59536a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gank_report_metrics",
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["match_id", "account_id"],
            [
                "gank_report_player_matches.match_id",
                "gank_report_player_matches.account_id",
            ],
            name=op.f("fk_gank_report_metrics_match_id_gank_report_player_matches"),
        ),
        sa.PrimaryKeyConstraint(
            "match_id", "account_id", "metric", name=op.f("pk_gank_report_metrics")
        ),
    )


def downgrade() -> None:
    op.drop_table("gank_report_metrics")
