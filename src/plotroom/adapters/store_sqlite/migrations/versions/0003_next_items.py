"""Durable next items (AD-18).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "noop_command_receipts",
        sa.Column("command", sa.Text(), primary_key=True),
        sa.Column("request_key", sa.Text(), primary_key=True),
        sa.Column("receipt", sa.Text(), nullable=False),
        sa.CheckConstraint("json_valid(receipt)"),
    )
    op.create_table(
        "next_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject_ref", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("rank_class", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("opened_seq", sa.Integer(), nullable=False),
        sa.Column("closed_seq", sa.Integer()),
        sa.Column("seq", sa.Integer(), nullable=False, unique=True),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("suggested_actions", sa.Text(), nullable=False),
        sa.CheckConstraint("state IN ('open', 'snoozed', 'closed')"),
        sa.CheckConstraint("(state = 'closed') = (closed_seq IS NOT NULL)"),
        sa.CheckConstraint("json_valid(context) AND json_valid(suggested_actions)"),
    )
    op.create_index(
        "uq_next_items_active_key",
        "next_items",
        ["dedup_key"],
        unique=True,
        sqlite_where=sa.text("state != 'closed'"),
    )
    op.create_index("ix_next_items_rank", "next_items", ["state", "rank_class", "opened_seq"])
