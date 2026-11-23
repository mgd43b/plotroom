"""Persist merge controller state."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lanes", sa.Column("branch_pattern", sa.Text(), nullable=True))
    op.add_column("github_pull_requests", sa.Column("draft", sa.Boolean(), nullable=True))
    op.add_column("github_pull_requests", sa.Column("merge_commit_sha", sa.Text()))
    op.create_table(
        "merge_verdicts",
        sa.Column("repository", sa.Text(), primary_key=True),
        sa.Column("number", sa.Integer(), primary_key=True),
        sa.Column("event_seq", sa.Integer(), sa.ForeignKey("events.seq"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("merge_verdicts")
    op.drop_column("github_pull_requests", "merge_commit_sha")
    op.drop_column("github_pull_requests", "draft")
    op.drop_column("lanes", "branch_pattern")
