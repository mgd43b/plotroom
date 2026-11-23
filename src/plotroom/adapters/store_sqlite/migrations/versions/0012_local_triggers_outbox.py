"""Persist local triggers and the Claude outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "triggers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("lane", sa.Text()),
        sa.Column("repository", sa.Text()),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("action_kind", sa.Text(), nullable=False),
        sa.Column("action_payload", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("bindings", sa.Text(), nullable=False),
        sa.Column("created_seq", sa.Integer(), nullable=False, unique=True),
        sa.Column("fired_seq", sa.Integer()),
        sa.Column("action_key", sa.Text()),
        sa.CheckConstraint("state IN ('open', 'fired', 'canceled')"),
    )
    op.create_index("ix_trigger_state", "triggers", ["state", "created_seq"])
    op.create_index("ix_trigger_lane", "triggers", ["lane", "created_seq"])
    op.create_table(
        "trigger_pr_branch_order",
        sa.Column("observation_order", sa.Integer(), primary_key=True),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.UniqueConstraint("repository", "number", "branch"),
        sa.ForeignKeyConstraint(
            ["repository", "number"],
            ["github_pull_requests.repository", "github_pull_requests.number"],
            ondelete="CASCADE",
        ),
    )
    # Old projections have no accepted per-branch observation order. Seed their current
    # heads in projection insertion order without reading unprojected event records.
    op.execute("""
        INSERT INTO trigger_pr_branch_order (repository, number, branch)
        SELECT repository, number, branch FROM github_pull_requests WHERE complete ORDER BY rowid
    """)
    op.execute("""
        CREATE TRIGGER trigger_observe_pr_insert AFTER INSERT ON github_pull_requests
        WHEN NEW.complete
        BEGIN
            INSERT INTO trigger_pr_branch_order (repository, number, branch)
            VALUES (NEW.repository, NEW.number, NEW.branch)
            ON CONFLICT(repository, number, branch) DO NOTHING;
        END
    """)
    op.execute("""
        CREATE TRIGGER trigger_observe_pr_branch AFTER UPDATE OF branch, complete
        ON github_pull_requests
        WHEN NEW.complete AND (NEW.branch != OLD.branch OR NOT OLD.complete)
        BEGIN
            INSERT INTO trigger_pr_branch_order (repository, number, branch)
            VALUES (NEW.repository, NEW.number, NEW.branch)
            ON CONFLICT(repository, number, branch) DO NOTHING;
        END
    """)
    op.create_table(
        "claude_outbox",
        sa.Column("action_key", sa.Text(), sa.ForeignKey("actions.action_key"), primary_key=True),
        sa.Column("worker", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, unique=True),
        sa.Column("lease_id", sa.Text(), unique=True),
        sa.Column("expires_at", sa.Text()),
        sa.Column("done", sa.Boolean(), nullable=False, server_default="0"),
        sa.CheckConstraint("(lease_id IS NULL) = (expires_at IS NULL)"),
    )
    op.create_index("ix_outbox_pending", "claude_outbox", ["done", "seq", "expires_at"])
