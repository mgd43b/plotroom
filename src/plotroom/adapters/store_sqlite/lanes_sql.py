"""Reads and writes for the lane board."""

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.lanes import Lane

_FIND = text("SELECT * FROM lanes WHERE id = :id")
_WORKER = text("SELECT * FROM lanes WHERE worker = :worker AND state != 'archived'")
_SAVE = text(
    "INSERT INTO lanes (id, worker, work_items, branch, state, waiting_on, pr, head_sha) VALUES "
    "(:id, :worker, :work_items, :branch, :state, :waiting_on, :pr, :head_sha) "
    "ON CONFLICT(id) DO UPDATE SET state=excluded.state, waiting_on=excluded.waiting_on, "
    "pr=excluded.pr, head_sha=excluded.head_sha"
)
_BOARD = text(
    "SELECT * FROM lanes WHERE state != 'archived' "
    "AND (:role = 'orchestrator' OR :role = 'clerk' OR :role = 'worker:' || worker) ORDER BY id"
)
_COUNTS = text(
    "SELECT state, count(*) AS total FROM lanes "
    "WHERE (:role = 'orchestrator' OR :role = 'clerk' OR :role = 'worker:' || worker) "
    "GROUP BY state ORDER BY state"
)


def _lane(row: Row[Any]) -> Lane:
    fields = dict(row._mapping)  # pyright: ignore[reportPrivateUsage]
    fields["work_items"] = tuple(json.loads(row.work_items))
    return Lane(**fields)


async def find_lane(connection: AsyncConnection, lane_id: str) -> Lane | None:
    row = (await connection.execute(_FIND, {"id": lane_id})).first()
    return None if row is None else _lane(row)


async def worker_lane(connection: AsyncConnection, worker: str) -> Lane | None:
    row = (await connection.execute(_WORKER, {"worker": worker})).first()
    return None if row is None else _lane(row)


async def save_lane(connection: AsyncConnection, lane: Lane) -> None:
    fields = asdict(lane)
    fields["work_items"] = json.dumps(lane.work_items)
    await connection.execute(_SAVE, fields)


async def board_lanes(connection: AsyncConnection, role: str) -> list[Lane]:
    return [_lane(row) for row in await connection.execute(_BOARD, {"role": role})]


async def lane_counts(connection: AsyncConnection, role: str) -> dict[str, int]:
    return {
        str(row.state): int(row.total) for row in await connection.execute(_COUNTS, {"role": role})
    }
