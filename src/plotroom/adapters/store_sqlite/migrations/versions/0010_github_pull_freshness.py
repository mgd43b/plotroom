"""Track the last completed observation and fact version for each projected PR."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_fetch_retries",
        sa.Column("repository", sa.Text(), primary_key=True),
        sa.Column("resource", sa.Text(), primary_key=True),
        sa.Column("until", sa.Text(), nullable=False),
        sa.Column("next_backoff", sa.Integer(), nullable=False),
    )
    op.add_column(
        "github_pull_requests", sa.Column("as_of", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "github_pull_requests",
        sa.Column("complete", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column(
        "github_pull_requests",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """UPDATE github_pull_requests SET as_of = COALESCE(
          (SELECT as_of FROM github_fetch_cursors AS c
           WHERE c.repository = github_pull_requests.repository AND c.resource = 'pull_requests'),
          updated_at
        )"""
    )
