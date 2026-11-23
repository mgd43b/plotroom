"""Persistence for the action runner's queue."""

import json

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.actions import BRANCH_UPDATE, Action

_INSERT = text(
    "INSERT INTO actions (action_key, kind, cause_id, payload, attempts, state) "
    "VALUES (:key, :kind, :cause, :payload, 0, 'pending') "
    "ON CONFLICT(action_key) DO NOTHING"
)
_UPDATE = text("UPDATE actions SET attempts = :attempts, state = :state WHERE action_key = :key")
_PENDING = text(
    "SELECT * FROM actions WHERE state = 'pending' AND action_key NOT IN :skip_keys "
    "AND (:skip_branch_updates = 0 OR kind != :branch_update) ORDER BY rowid LIMIT 1"
).bindparams(bindparam("skip_keys", expanding=True))


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


async def pending_action(
    connection: AsyncConnection,
    *,
    skip_keys: tuple[str, ...] = (),
    skip_branch_updates: bool = False,
) -> Action | None:
    row = (
        await connection.execute(
            _PENDING,
            {
                "skip_keys": skip_keys,
                "skip_branch_updates": skip_branch_updates,
                "branch_update": BRANCH_UPDATE,
            },
        )
    ).first()
    if row is None:
        return None
    return Action(
        row.action_key, row.kind, row.cause_id, json.loads(row.payload), row.attempts, row.state
    )
