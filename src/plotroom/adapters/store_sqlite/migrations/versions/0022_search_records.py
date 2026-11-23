"""Add source identities and an update sequence to the full-text index."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_records",
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.UniqueConstraint("kind", "record_id"),
        sqlite_autoincrement=True,
    )
    op.execute("""
        INSERT INTO search_records (seq, kind, record_id, body)
        SELECT events.seq, events.kind, events.id, events.body
        FROM message_fts JOIN events ON events.seq = message_fts.rowid
        WHERE events.kind = 'switchboard.message'
    """)
    op.execute("""
        CREATE INDEX search_backfill_comments ON github_resources (repository, scope)
        WHERE resource = 'issue_comments'
    """)
    op.execute("""
        CREATE INDEX search_backfill_pull_events ON events (
            json_extract(fields, '$.repository'), json_extract(fields, '$.number'), seq
        )
        WHERE source = 'github' AND kind IN (
            'github.pr_opened', 'github.pr_merged', 'github.pr_closed',
            'github.pr_reopened', 'github.pr_head_changed'
        )
    """)
