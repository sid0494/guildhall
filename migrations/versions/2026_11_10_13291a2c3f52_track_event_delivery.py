"""track event delivery

Revision ID: 13291a2c3f52
Revises: b8ffd0cb9dd8
Create Date: 2026-11-10 18:11:56.603463
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "13291a2c3f52"
down_revision: str | Sequence[str] | None = "b8ffd0cb9dd8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "outbox_events", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "outbox_events",
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbox_events", sa.Column("discord_message_id", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "discord_message_id")
    op.drop_column("outbox_events", "abandoned_at")
    op.drop_column("outbox_events", "sent_at")
    op.drop_column("outbox_events", "attempts")
