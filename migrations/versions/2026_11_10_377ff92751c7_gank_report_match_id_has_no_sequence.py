"""gank_report match id has no sequence

Revision ID: 377ff92751c7
Revises: a15e9871ccc6
Create Date: 2026-11-10 12:58:24.018409
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "377ff92751c7"
down_revision: str | Sequence[str] | None = "a15e9871ccc6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The column was created as BIGSERIAL by mistake: match IDs come from
    # OpenDota and must never be generated (docs/adr/0045).
    op.alter_column("gank_report_matches", "match_id", server_default=None)
    op.execute("DROP SEQUENCE gank_report_matches_match_id_seq")


def downgrade() -> None:
    op.execute("CREATE SEQUENCE gank_report_matches_match_id_seq")
    op.alter_column(
        "gank_report_matches",
        "match_id",
        server_default=sa.text("nextval('gank_report_matches_match_id_seq')"),
    )
    op.execute(
        "ALTER SEQUENCE gank_report_matches_match_id_seq"
        " OWNED BY gank_report_matches.match_id"
    )
