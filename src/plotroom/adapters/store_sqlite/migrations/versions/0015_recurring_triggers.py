"""Persist recurring trigger edges and index their event inputs."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "triggers",
        sa.Column(
            "mode",
            sa.Text(),
            sa.CheckConstraint("mode IN ('once', 'every')"),
            nullable=False,
            server_default="once",
        ),
    )
    op.add_column(
        "triggers", sa.Column("was_true", sa.Boolean(), nullable=False, server_default="0")
    )
    op.add_column(
        "triggers", sa.Column("event_floors", sa.Text(), nullable=False, server_default="{}")
    )
    op.create_index("ix_events_kind_seq", "events", ["kind", "seq"])
    op.create_index("ix_events_lane_seq", "events", ["lane_id", "seq"])
    op.create_index(
        "ix_events_receipt_lane_seq",
        "events",
        [sa.text("json_extract(fields, '$.args.lane')"), "seq"],
        sqlite_where=sa.text(
            "kind IN ('core.lane_start', 'core.lane_update', 'core.lane_ready', "
            "'core.lane_archive', 'core.claim', 'core.release')"
        ),
    )
