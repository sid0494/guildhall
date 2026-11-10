"""create interactions seen

Revision ID: 73352b2de7f2
Revises: 4866245a9df9
Create Date: 2026-11-10 14:10:58.637969
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "73352b2de7f2"
down_revision: str | Sequence[str] | None = "4866245a9df9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interactions_seen",
        sa.Column("interaction_id", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("interaction_id", name=op.f("pk_interactions_seen")),
    )


def downgrade() -> None:
    op.drop_table("interactions_seen")
