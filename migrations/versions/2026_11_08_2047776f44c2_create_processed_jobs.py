"""create processed_jobs

Revision ID: 2047776f44c2
Revises: (none)
Create Date: 2026-11-08 13:23:07.453543
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2047776f44c2"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_jobs",
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("dedupe_key", name=op.f("pk_processed_jobs")),
    )


def downgrade() -> None:
    op.drop_table("processed_jobs")
