"""Persist named operator goals with their original text."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_goals",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("uses_blockers", sa.Boolean(), nullable=False),
        sa.Column("uses_lanes", sa.Boolean(), nullable=False),
        sa.Column("uses_decisions", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("operator_goals")
