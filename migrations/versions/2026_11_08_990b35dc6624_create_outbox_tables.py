"""create outbox tables

Revision ID: 990b35dc6624
Revises: 2047776f44c2
Create Date: 2026-11-08 13:43:12.484780
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "990b35dc6624"
down_revision: str | Sequence[str] | None = "2047776f44c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_outbox_events_dedupe_key")),
    )
    op.create_table(
        "outbox_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("delay_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "delay_seconds BETWEEN 0 AND 900",
            name=op.f("ck_outbox_jobs_delay_seconds_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_jobs")),
    )
    op.create_index(
        "ix_outbox_jobs_pending",
        "outbox_jobs",
        ["id"],
        unique=False,
        postgresql_where="sent_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_jobs_pending",
        table_name="outbox_jobs",
        postgresql_where="sent_at IS NULL",
    )
    op.drop_table("outbox_jobs")
    op.drop_table("outbox_events")
