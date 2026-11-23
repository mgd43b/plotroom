"""Index Switchboard message text and per-model embeddings."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE VIRTUAL TABLE message_fts USING fts5(body)")
    op.create_table(
        "embedding_cursors",
        sa.Column("model", sa.Text(), primary_key=True),
        sa.Column("dimension", sa.Integer(), primary_key=True),
        sa.Column("index_seq", sa.Integer(), nullable=False),
        sa.CheckConstraint("dimension > 0 AND index_seq > 0", name="ck_embedding_cursor_positive"),
    )
    op.create_table(
        "message_vectors",
        sa.Column("model", sa.Text(), primary_key=True),
        sa.Column("dimension", sa.Integer(), primary_key=True),
        sa.Column("record_id", sa.Text(), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "dimension > 0 AND length(vector) = 4 * dimension", name="ck_message_vector_length"
        ),
    )
