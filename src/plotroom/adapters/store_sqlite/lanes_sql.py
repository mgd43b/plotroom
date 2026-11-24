"""Reads and writes for the lane board."""

import json
from dataclasses import asdict
from typing import Any, cast

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.lanes import Lane

_FIND = text("SELECT * FROM lanes WHERE id = :id")
_WORKER = text("SELECT * FROM lanes WHERE worker = :worker AND state != 'archived'")
_SAVE = text(
    "INSERT INTO lanes (id, worker, work_items, branch, state, waiting_on, pr, head_sha, "
    "branch_pattern, imported_channel, imported_trigger_id) "
    "VALUES (:id, :worker, :work_items, :branch, :state, :waiting_on, :pr, "
    ":head_sha, :branch_pattern, :imported_channel, :imported_trigger_id) "
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
    items: Any = json.loads(row.work_items)
    if not isinstance(items, list) or not all(
        isinstance(item, str) for item in cast("list[Any]", items)
    ):
        raise ValueError("invalid work items in stored lane")
    fields["work_items"] = tuple(cast("list[str]", items))
    return Lane(**fields)


async def find_lane(connection: AsyncConnection, lane_id: str) -> Lane | None:
    row = (await connection.execute(_FIND, {"id": lane_id})).first()
    return None if row is None else _lane(row)


async def worker_lane(connection: AsyncConnection, worker: str) -> Lane | None:
    row = (await connection.execute(_WORKER, {"worker": worker})).first()
    return None if row is None else _lane(row)


async def branch_lane(connection: AsyncConnection, branch: str) -> Lane | None:
    rows = (
        await connection.execute(
            text("SELECT * FROM lanes WHERE branch = :branch AND state != 'archived' LIMIT 2"),
            {"branch": branch},
        )
    ).all()
    return _lane(rows[0]) if len(rows) == 1 else None


async def branch_lanes(connection: AsyncConnection, branch: str) -> list[Lane]:
    rows = await connection.execute(
        text("SELECT * FROM lanes WHERE branch = :branch AND state != 'archived' ORDER BY id"),
        {"branch": branch},
    )
    return [_lane(row) for row in rows]


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
