"""Persist project budgets and hands-on action routes."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("run", sa.Text(), nullable=False, unique=True),
        sa.Column("lane", sa.Text(), sa.ForeignKey("lanes.id"), nullable=False),
        sa.Column("cap_usd", sa.Float(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("key_scope", sa.Text(), nullable=False),
        sa.Column("spent_usd", sa.Float(), nullable=False),
        sa.Column("as_of", sa.Text()),
    )
    op.create_table(
        "routes",
        sa.Column("action_kind", sa.Text(), primary_key=True),
        sa.Column("lane", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("routes")
    op.drop_table("budgets")
