"""Persist local human decisions and asks."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX issue_comments_by_number
        ON github_resources (repository, json_extract(records, '$[0].fields.number'))
        WHERE resource = 'issue_comments'
    """)
    op.create_table(
        "decisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("who", sa.Text(), nullable=False),
        sa.Column("exact_words", sa.Text(), nullable=False),
        sa.Column("where_ref", sa.Text(), nullable=False),
        sa.Column("governs", sa.Text(), nullable=False),
        sa.Column("settles", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "human_asks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("issue", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("options", sa.Text(), nullable=False),
        sa.Column("comment_id", sa.BigInteger(), nullable=False),
        sa.Column("comment_created_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("settled_by", sa.Text(), sa.ForeignKey("decisions.id")),
        sa.UniqueConstraint("repository", "comment_id"),
    )


def downgrade() -> None:
    op.drop_index("issue_comments_by_number", table_name="github_resources")
    op.drop_table("human_asks")
    op.drop_table("decisions")
