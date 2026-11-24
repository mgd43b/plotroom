"""Track confirmed embedding rejections for a source revision and model."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_rejections",
        sa.Column("model", sa.Text(), primary_key=True),
        sa.Column("dimension", sa.Integer(), primary_key=True),
        sa.Column(
            "record_seq",
            sa.Integer(),
            sa.ForeignKey("search_records.seq", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("dimension > 0", name="ck_embedding_rejection_dimension"),
        sa.CheckConstraint("reason = 'input_too_large'", name="ck_embedding_rejection_reason"),
    )
    op.create_index("embedding_rejections_record_seq", "embedding_rejections", ["record_seq"])


def downgrade() -> None:
    # Revisit skipped records when returning to a schema that cannot remember rejections.
    op.execute("""
        DELETE FROM embedding_cursors
        WHERE (model, dimension) IN (SELECT model, dimension FROM embedding_rejections)
    """)
    op.drop_table("embedding_rejections")
