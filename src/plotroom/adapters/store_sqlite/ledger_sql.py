"""The ledger's SQL: the writer transaction and the event reads (SQLAlchemy Core, no ORM)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.events import Event

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
