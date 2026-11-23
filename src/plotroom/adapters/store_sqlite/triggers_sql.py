"""Storage for the trigger inventory and its committed projection inputs."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.triggers import Trigger

_FIND = text("SELECT * FROM triggers WHERE id = :id")
_OPEN = text("SELECT * FROM triggers WHERE state = 'open' ORDER BY created_seq")
_LIST = text("SELECT * FROM triggers ORDER BY created_seq")
_LIST_LANE = text("SELECT * FROM triggers WHERE lane = :lane ORDER BY created_seq")
_COUNTS = text("SELECT state, count(*) AS total FROM triggers GROUP BY state")
_SAVE = text("""
    INSERT INTO triggers
      (id, lane, repository, predicate, action_kind, action_payload, state, bindings,
       created_seq, fired_seq, action_key)
    VALUES
      (:id, :lane, :repository, :predicate, :action_kind, :action_payload, :state, :bindings,
       :created_seq, :fired_seq, :action_key)
    ON CONFLICT(id) DO UPDATE SET state = excluded.state, bindings = excluded.bindings,
      fired_seq = excluded.fired_seq, action_key = excluded.action_key
""")
_BRANCHES = text("""
    SELECT p.number, p.branch FROM github_pull_requests AS p
    JOIN trigger_pr_branch_order AS o
       ON p.repository = o.repository AND p.number = o.number AND p.branch = o.branch
    WHERE p.repository = :repository AND p.complete
    ORDER BY o.observation_order
""")


def _from_row(row: Row[Any]) -> Trigger:
    return Trigger(
        row.id,
        row.lane,
        row.repository,
        json.loads(row.predicate),
        row.action_kind,
        json.loads(row.action_payload),
        row.state,
        json.loads(row.bindings),
        row.created_seq,
        row.fired_seq,
        row.action_key,
    )


async def find_trigger(connection: AsyncConnection, trigger_id: str) -> Trigger | None:
    row = (await connection.execute(_FIND, {"id": trigger_id})).first()
    return _from_row(row) if row is not None else None


async def open_triggers(connection: AsyncConnection) -> list[Trigger]:
    return [_from_row(row) for row in await connection.execute(_OPEN)]


async def list_triggers(connection: AsyncConnection, lane: str | None) -> list[Trigger]:
    rows = (
        await connection.execute(_LIST)
        if lane is None
        else await connection.execute(_LIST_LANE, {"lane": lane})
    )
    return [_from_row(row) for row in rows]


async def trigger_counts(connection: AsyncConnection) -> dict[str, int]:
    return {row.state: row.total for row in await connection.execute(_COUNTS)}


async def save_trigger(connection: AsyncConnection, trigger: Trigger) -> None:
    await connection.execute(
        _SAVE,
        {
            "id": trigger.id,
            "lane": trigger.lane,
            "repository": trigger.repository,
            "predicate": json.dumps(trigger.predicate, allow_nan=False),
            "action_kind": trigger.action_kind,
            "action_payload": json.dumps(trigger.action_payload, allow_nan=False),
            "state": trigger.state,
            "bindings": json.dumps(trigger.bindings),
            "created_seq": trigger.created_seq,
            "fired_seq": trigger.fired_seq,
            "action_key": trigger.action_key,
        },
    )


async def branch_candidates(
    connection: AsyncConnection, repository: str
) -> tuple[tuple[int, str], ...]:
    rows = await connection.execute(_BRANCHES, {"repository": repository})
    return tuple((row.number, row.branch) for row in rows)
