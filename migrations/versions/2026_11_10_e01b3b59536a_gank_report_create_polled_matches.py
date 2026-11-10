"""gank_report create polled matches

Revision ID: e01b3b59536a
Revises: 7282f2ab9130
Create Date: 2026-11-10 13:01:55.443041
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e01b3b59536a"
down_revision: str | Sequence[str] | None = "7282f2ab9130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gank_report_polled_matches",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "polled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "account_id", "match_id", name=op.f("pk_gank_report_polled_matches")
        ),
    )


def downgrade() -> None:
    op.drop_table("gank_report_polled_matches")
