"""Persist the alternate identities of Claude turn reports."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claude_turn_aliases",
        sa.Column("session", sa.Text(), primary_key=True),
        sa.Column("alias", sa.Text(), primary_key=True),
        sa.Column("event_id", sa.Text(), sa.ForeignKey("events.id"), primary_key=True),
    )
    op.create_index(
        "ix_claude_turn_index",
        "claude_turn_aliases",
        ["session", "alias"],
        unique=True,
        sqlite_where=sa.text("alias GLOB 'index:*'"),
    )
    op.create_index(
        "ix_claude_turn_event",
        "claude_turn_aliases",
        ["event_id", "session", "alias"],
    )
