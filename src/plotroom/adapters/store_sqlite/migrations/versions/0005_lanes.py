"""The durable lane board.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lanes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("worker", sa.Text(), nullable=False),
        sa.Column("work_items", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("waiting_on", sa.Text()),
        sa.Column("pr", sa.Integer()),
        sa.Column("head_sha", sa.Text()),
        sa.CheckConstraint("json_valid(work_items)"),
        sa.CheckConstraint("state IN ('active', 'ready', 'archived')"),
    )
    op.create_index(
        "uq_lanes_live_worker",
        "lanes",
        ["worker"],
        unique=True,
        sqlite_where=sa.text("state != 'archived'"),
    )
