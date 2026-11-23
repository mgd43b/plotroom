"""Retain the ordered item changes consumed by wait (AD-9).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_changes",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("item", sa.Text(), nullable=False),
        sa.CheckConstraint("seq > 0 AND json_valid(item)"),
    )
    op.create_index("ix_item_changes_role_seq", "item_changes", ["role", "seq"])
    op.execute(
        """INSERT INTO item_changes (seq, role, item)
        SELECT seq, role, json_object(
          'id', id, 'kind', kind, 'subject_ref', subject_ref, 'dedup_key', dedup_key,
          'role', role, 'rank_class', rank_class, 'state', state, 'opened_seq', opened_seq,
          'closed_seq', closed_seq, 'seq', seq, 'context', json(context),
          'suggested_actions', json(suggested_actions)
        ) FROM next_items"""
    )
