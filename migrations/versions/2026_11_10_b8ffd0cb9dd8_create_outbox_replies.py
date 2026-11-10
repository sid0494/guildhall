"""create outbox replies

Revision ID: b8ffd0cb9dd8
Revises: 73352b2de7f2
Create Date: 2026-11-10 16:58:15.604594
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8ffd0cb9dd8"
down_revision: str | Sequence[str] | None = "73352b2de7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_replies",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("application_id", sa.Text(), nullable=False),
        sa.Column("interaction_token", sa.Text(), nullable=False),
        sa.Column("message", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_replies")),
    )


def downgrade() -> None:
    op.drop_table("outbox_replies")
