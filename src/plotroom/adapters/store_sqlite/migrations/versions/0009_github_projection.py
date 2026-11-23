"""Store the GitHub PR projection and the progress of each resource fetcher."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_pull_requests",
        sa.Column("repository", sa.Text(), primary_key=True),
        sa.Column("number", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("head_repository", sa.Text()),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("number > 0 AND state IN ('open', 'closed', 'merged')"),
    )
    op.create_index("ix_github_pull_state", "github_pull_requests", ["repository", "state"])
    op.create_index(
        "ix_github_pull_branch",
        "github_pull_requests",
        ["repository", "head_repository", "branch", "state"],
    )
    op.create_table(
        "github_fetch_cursors",
        sa.Column("repository", sa.Text(), primary_key=True),
        sa.Column("resource", sa.Text(), primary_key=True),
        sa.Column("watermark", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.CheckConstraint("generation > 0"),
    )
