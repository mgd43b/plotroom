"""Persist each lane's READY checklist generation and ordered steps."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lane_checklists",
        sa.Column("lane", sa.Text(), sa.ForeignKey("lanes.id"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("pr", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("pr > 0"),
        sa.CheckConstraint("state IN ('pending', 'active', 'invalidated')"),
    )
    op.create_table(
        "lane_checklist_steps",
        sa.Column("lane", sa.Text(), sa.ForeignKey("lane_checklists.lane"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("completion_ref", sa.Text()),
        sa.CheckConstraint("position >= 0"),
        sa.UniqueConstraint("lane", "step"),
    )
