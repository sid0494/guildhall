"""synthetic create records

Revision ID: 04fb2d8a852f
Revises: 990b35dc6624
Create Date: 2026-11-09 13:55:45.192758
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "04fb2d8a852f"
down_revision: str | Sequence[str] | None = "990b35dc6624"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "synthetic_records",
        sa.Column("work_id", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("work_id", name=op.f("pk_synthetic_records")),
    )


def downgrade() -> None:
    op.drop_table("synthetic_records")
