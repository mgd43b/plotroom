"""Persist the lane's current path claims."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lane_claims",
        sa.Column("lane", sa.Text(), sa.ForeignKey("lanes.id"), primary_key=True),
        sa.Column("glob", sa.Text(), primary_key=True),
        sa.Column("reason", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("lane_claims")
