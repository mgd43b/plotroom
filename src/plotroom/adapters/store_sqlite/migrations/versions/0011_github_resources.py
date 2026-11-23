"""Add read-only GitHub resource projections."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_resources",
        sa.Column("repository", sa.Text(), primary_key=True),
        sa.Column("resource", sa.Text(), primary_key=True),
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("records", sa.Text(), nullable=False),
        sa.Column("attributes", sa.Text(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("github_resources")
