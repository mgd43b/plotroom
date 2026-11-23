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
        sa.Column("embeddable", sa.Boolean(), nullable=False),
        sa.Column("provisional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("kind", "record_id"),
        sqlite_autoincrement=True,
    )
    # Match Python's str.strip() when marking retained bodies for the embedding follower.
    op.execute("""
        INSERT INTO search_records (seq, kind, record_id, body, embeddable)
        SELECT events.seq, events.kind, events.id, events.body,
               trim(events.body, char(
                   9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
                   8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202,
                   8232, 8233, 8239, 8287, 12288
               )) <> ''
        FROM message_fts JOIN events ON events.seq = message_fts.rowid
        WHERE events.kind = 'switchboard.message'
    """)
    op.create_table(
        "search_vectors",
        sa.Column("model", sa.Text(), primary_key=True),
        sa.Column("dimension", sa.Integer(), primary_key=True),
        sa.Column(
            "record_seq",
            sa.Integer(),
            sa.ForeignKey("search_records.seq", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "dimension > 0 AND length(vector) = 4 * dimension", name="ck_search_vector_length"
        ),
    )
    op.create_index("search_vectors_record_seq", "search_vectors", ["record_seq"])
    op.execute("""
        CREATE INDEX search_records_embeddable_kind_seq ON search_records (kind, seq)
        WHERE embeddable
    """)
    op.execute("""
        INSERT INTO search_vectors (model, dimension, record_seq, vector)
        SELECT v.model, v.dimension, r.seq, v.vector
        FROM message_vectors AS v JOIN search_records AS r ON r.record_id = v.record_id
        WHERE r.kind = 'switchboard.message' AND r.embeddable
    """)
    op.drop_table("message_vectors")
    # Retained vectors can be reused, but event cursors cannot track search-record revisions.
    op.execute("DELETE FROM embedding_cursors")
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
