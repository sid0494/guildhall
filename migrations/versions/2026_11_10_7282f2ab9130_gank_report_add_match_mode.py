"""gank_report add match mode

Revision ID: 7282f2ab9130
Revises: 377ff92751c7
Create Date: 2026-11-10 12:58:34.107785
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7282f2ab9130"
down_revision: str | Sequence[str] | None = "377ff92751c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gank_report_matches", sa.Column("mode", sa.Text(), nullable=False))
    # Autogenerate does not detect check constraints; added by hand.
    op.create_check_constraint(
        op.f("ck_gank_report_matches_mode"),
        "gank_report_matches",
        "mode IN ('normal', 'turbo')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_gank_report_matches_mode"), "gank_report_matches", type_="check"
    )
    op.drop_column("gank_report_matches", "mode")
