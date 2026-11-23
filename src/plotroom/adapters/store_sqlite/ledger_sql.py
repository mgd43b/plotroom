"""The ledger's SQL: the writer transaction and the event reads (SQLAlchemy Core, no ORM)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.adapters.store_sqlite import actions_sql, items_sql, lanes_sql, work_items_sql
from plotroom.core.actions import Action
from plotroom.core.events import Event
from plotroom.core.lanes import Lane
from plotroom.core.next_items import Item
from plotroom.ports.work_items import WorkItem

FIND_EVENT = text(
    "SELECT seq, id, source, kind, upstream_id, lane_id, fields, body, observed_at "
    "FROM events WHERE source = :source AND kind = :kind AND upstream_id = :uid"
)
INSERT_EVENT = text(
    "INSERT INTO events (seq, id, source, kind, upstream_id, lane_id, fields, body, observed_at) "
    "VALUES (:seq, :id, :source, :kind, :uid, :lane_id, :fields, :body, :observed_at)"
)
ALLOCATE_SEQ = text("UPDATE project_seq SET value = value + 1 WHERE id = 1 RETURNING value")
HEAD_SEQ = text("SELECT value FROM project_seq WHERE id = 1")
EVENTS_AFTER = text(
    "SELECT seq, id, source, kind, upstream_id, lane_id, fields, body, observed_at "
    "FROM events WHERE seq > :seq ORDER BY seq LIMIT max(:limit, 0)"
)


def event_from_row(row: Row[Any]) -> Event:
    return Event(
        seq=row.seq,
        id=row.id,
        source=row.source,
        kind=row.kind,
        upstream_id=row.upstream_id,
        lane_id=row.lane_id,
        fields=json.loads(row.fields),
        body=row.body,
        observed_at=row.observed_at,
    )


class SqliteTransaction:
    """Implements ``plotroom.ports.Transaction`` on the writer connection's open transaction."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self._expired = False

    def expire(self) -> None:
        """End the handle's life with its transaction: the connection serves the next input."""
        self._expired = True

    def _live(self) -> AsyncConnection:
        if self._expired:
            raise RuntimeError("this transaction has ended; a retained handle cannot write")
        return self._connection

    async def find_event(self, source: str, kind: str, upstream_id: str) -> Event | None:
        result = await self._live().execute(
            FIND_EVENT, {"source": source, "kind": kind, "uid": upstream_id}
        )
        row = result.first()
        return None if row is None else event_from_row(row)

    async def insert_event(self, event: Event) -> None:
        await self._live().execute(
            INSERT_EVENT,
            {
                "seq": event.seq,
                "id": event.id,
                "source": event.source,
                "kind": event.kind,
                "uid": event.upstream_id,
                "lane_id": event.lane_id,
                "fields": json.dumps(event.fields, separators=(",", ":"), allow_nan=False),
                "body": event.body,
                "observed_at": event.observed_at,
            },
        )

    async def allocate_seq(self) -> int:
        result = await self._live().execute(ALLOCATE_SEQ)
        return int(result.scalar_one())

    async def find_item(self, item_id: str) -> Item | None:
        return await items_sql.find_item(self._live(), item_id)

    async def active_item(self, key: str) -> Item | None:
        return await items_sql.active_item(self._live(), key)

    async def ready_work_items(self) -> tuple[WorkItem, ...]:
        return await work_items_sql.ready_work_items(self._live())

    async def replace_work_items(self, items: tuple[WorkItem, ...]) -> None:
        await work_items_sql.replace_work_items(self._live(), items)

    async def claimed_work_items(self) -> set[str]:
        return await work_items_sql.claimed_work_items(self._live())

    async def find_lane(self, lane_id: str) -> Lane | None:
        return await lanes_sql.find_lane(self._live(), lane_id)

    async def worker_lane(self, worker: str) -> Lane | None:
        return await lanes_sql.worker_lane(self._live(), worker)

    async def save_lane(self, lane: Lane) -> None:
        await lanes_sql.save_lane(self._live(), lane)

    async def insert_action(self, action: Action) -> None:
        await actions_sql.insert_action(self._live(), action)

    async def update_action(self, action: Action) -> None:
        await actions_sql.update_action(self._live(), action)

    async def save_item(self, item: Item) -> None:
        await items_sql.save_item(self._live(), item)
