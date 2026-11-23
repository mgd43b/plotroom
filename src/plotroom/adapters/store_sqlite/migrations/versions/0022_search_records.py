"""Add source identities and an update sequence to the full-text index."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
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
