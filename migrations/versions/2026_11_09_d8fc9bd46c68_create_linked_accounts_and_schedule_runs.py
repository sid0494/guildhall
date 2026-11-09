"""create linked_accounts and schedule_runs

Revision ID: d8fc9bd46c68
Revises: 04fb2d8a852f
Create Date: 2026-11-09 18:47:27.786454
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8fc9bd46c68"
down_revision: str | Sequence[str] | None = "04fb2d8a852f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "linked_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("discord_user_id", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_linked_accounts")),
        sa.UniqueConstraint(
            "plugin",
            "discord_user_id",
            name=op.f("uq_linked_accounts_plugin_discord_user_id"),
        ),
    )
    op.create_table(
        "schedule_runs",
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("fired_for", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("plugin", "job_type", name=op.f("pk_schedule_runs")),
    )


def downgrade() -> None:
    op.drop_table("schedule_runs")
    op.drop_table("linked_accounts")
