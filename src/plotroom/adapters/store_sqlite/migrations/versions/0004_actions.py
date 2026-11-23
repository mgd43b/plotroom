"""Committed effects and their retry state.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "actions",
        sa.Column("action_key", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("cause_id", sa.Text(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.CheckConstraint("state IN ('pending', 'done', 'failed')"),
        sa.CheckConstraint("attempts >= 0 AND json_valid(payload)"),
    )
    op.create_index("ix_actions_pending", "actions", ["state"])
