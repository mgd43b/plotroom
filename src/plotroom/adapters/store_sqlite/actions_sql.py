"""Persistence for the action runner's queue."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.actions import BRANCH_UPDATE, CONTROLLER_MERGE, Action, QueuedAction

_INSERT = text(
    "INSERT INTO actions (action_key, kind, cause_id, payload, attempts, state) "
    "VALUES (:key, :kind, :cause, :payload, 0, 'pending') "
    "ON CONFLICT(action_key) DO NOTHING"
)
_UPDATE = text("UPDATE actions SET attempts = :attempts, state = :state WHERE action_key = :key")
_PENDING = text(
    "SELECT rowid AS queue_position, * FROM actions WHERE state = 'pending' AND rowid > :after "
    "AND (:skip_branch_updates = 0 OR kind != :branch_update) "
    "AND (:skip_controller_merges = 0 OR kind != :controller_merge) ORDER BY rowid LIMIT :limit"
)


async def insert_action(connection: AsyncConnection, action: Action) -> None:
    await connection.execute(
        _INSERT,
        {
            "key": action.key,
            "kind": action.kind,
            "cause": action.cause_id,
            "payload": json.dumps(action.payload, allow_nan=False),
        },
    )


async def update_action(connection: AsyncConnection, action: Action) -> None:
    await connection.execute(
        _UPDATE, {"key": action.key, "attempts": action.attempts, "state": action.state}
    )


async def pending_actions(
    connection: AsyncConnection,
    *,
    after: int,
    limit: int,
    skip_branch_updates: bool,
    skip_controller_merges: bool = False,
) -> tuple[QueuedAction, ...]:
    rows = await connection.execute(
        _PENDING,
        {
            "after": after,
            "limit": limit,
            "skip_branch_updates": skip_branch_updates,
            "branch_update": BRANCH_UPDATE,
            "controller_merge": CONTROLLER_MERGE,
            "skip_controller_merges": skip_controller_merges,
        },
    )
    return tuple(
        QueuedAction(
            row.queue_position,
            Action(
                row.action_key,
                row.kind,
                row.cause_id,
                json.loads(row.payload),
                row.attempts,
                row.state,
            ),
        )
        for row in rows
    )
