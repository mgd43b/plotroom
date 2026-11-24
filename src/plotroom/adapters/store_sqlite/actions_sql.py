"""Persistence for the action runner's queue."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.actions import (
    BRANCH_UPDATE,
    CONTROLLER_MERGE,
    SWITCHBOARD_SEND,
    Action,
    QueuedAction,
)
from plotroom.core.trigger_actions import SEND

_INSERT = text(
    "INSERT INTO actions (action_key, kind, cause_id, payload, attempts, state) "
    "VALUES (:key, :kind, :cause, :payload, 0, 'pending') "
    "ON CONFLICT(action_key) DO NOTHING"
)
_UPDATE = text(
    "UPDATE actions SET attempts = :attempts, state = :state, payload = :payload "
    "WHERE action_key = :key"
)
_BRIDGE_NEXT = text(
    "SELECT * FROM actions WHERE state = 'pending' AND (kind = :switchboard OR "
    "(kind = :send AND json_extract(payload, '$.worker') "
    "IN (SELECT value FROM json_each(:workers))))"
    " AND ((:lease IS NULL AND "
    "coalesce(json_extract(payload, '$._bridge_lease.expires'), '') <= :now)"
    " OR (json_extract(payload, '$._bridge_lease.id') = :lease"
    " AND json_extract(payload, '$._bridge_lease.expires') > :now)) ORDER BY rowid LIMIT 1"
)
_BRIDGE_EXPIRY = text(
    "SELECT min(json_extract(payload, '$._bridge_lease.expires')) FROM actions "
    "WHERE state = 'pending' AND (kind = :switchboard OR "
    "(kind = :send AND json_extract(payload, '$.worker') "
    "IN (SELECT value FROM json_each(:workers))))"
    " AND json_extract(payload, '$._bridge_lease.expires') > :now"
)
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
        _UPDATE,
        {
            "key": action.key,
            "attempts": action.attempts,
            "state": action.state,
            "payload": json.dumps(action.payload, allow_nan=False),
        },
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


def _bridge_parameters(workers: tuple[str, ...], now: str) -> dict[str, str]:
    return {
        "workers": json.dumps(workers),
        "now": now,
        "send": SEND,
        "switchboard": SWITCHBOARD_SEND,
    }


async def bridge_action(
    connection: AsyncConnection,
    workers: tuple[str, ...],
    now: str,
    act_id: str | None,
) -> Action | None:
    row = (
        await connection.execute(
            _BRIDGE_NEXT,
            {**_bridge_parameters(workers, now), "lease": act_id},
        )
    ).first()
    if row is None:
        return None
    return Action(
        row.action_key, row.kind, row.cause_id, json.loads(row.payload), row.attempts, row.state
    )


async def bridge_expiry(
    connection: AsyncConnection, workers: tuple[str, ...], now: str
) -> str | None:
    return (await connection.execute(_BRIDGE_EXPIRY, _bridge_parameters(workers, now))).scalar_one()
