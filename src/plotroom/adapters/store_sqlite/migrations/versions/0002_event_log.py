"""The event log and the project ``seq`` counter (AD-2, AD-9).

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("id", sa.Text(), nullable=False, unique=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("upstream_id", sa.Text(), nullable=False),
        sa.Column("lane_id", sa.Text(), nullable=True),
        sa.Column("fields", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("source", "kind", "upstream_id", name="uq_events_identity"),
        sa.CheckConstraint("seq > 0", name="ck_events_seq_positive"),
        sa.CheckConstraint("json_valid(fields)", name="ck_events_fields_json"),
    )
    op.create_table(
        "project_seq",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_project_seq_single_row"),
    )
    op.execute("INSERT INTO project_seq (id, value) VALUES (1, 0)")
