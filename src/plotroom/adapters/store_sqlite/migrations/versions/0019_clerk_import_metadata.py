"""Retain clerk import notes, trigger notices, and lane handoff references."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("triggers", sa.Column("import_note", sa.Text()))
    op.add_column("triggers", sa.Column("notify_on_fire", sa.Boolean()))
    op.add_column("lanes", sa.Column("imported_channel", sa.Text()))
    op.add_column("lanes", sa.Column("imported_trigger_id", sa.Text()))
