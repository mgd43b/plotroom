"""The ledger's SQL: the writer transaction and the event reads (SQLAlchemy Core, no ORM)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.adapters.store_sqlite import (
    actions_sql,
    github_sql,
    items_sql,
    lanes_sql,
    work_items_sql,
)
from plotroom.core.actions import Action
from plotroom.core.events import Event
from plotroom.core.github import FetchCursor, PullRequest
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
FIND_CLAUDE_TURN = text("""
    SELECT events.* FROM claude_turn_aliases AS matched JOIN events ON events.id = matched.event_id
    WHERE matched.session = :session AND matched.alias = :alias
      AND (:index_alias IS NULL OR NOT EXISTS (
        SELECT 1 FROM claude_turn_aliases AS indexed
        WHERE indexed.event_id = matched.event_id AND indexed.session = matched.session
          AND indexed.alias GLOB 'index:*' AND indexed.alias != :index_alias
      ))
    ORDER BY events.seq DESC LIMIT 1
""")
BIND_CLAUDE_TURN = text(
    "INSERT INTO claude_turn_aliases (session, alias, event_id) VALUES (:session, :alias, :event) "
    "ON CONFLICT DO NOTHING"
)
FIND_NOOP_RECEIPT = text(
    "SELECT receipt FROM noop_command_receipts WHERE command = :command AND request_key = :key"
)
INSERT_NOOP_RECEIPT = text(
    "INSERT INTO noop_command_receipts (command, request_key, receipt) "
    "VALUES (:command, :key, :receipt)"
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

    async def find_claude_turn(self, session: str, aliases: tuple[str, ...]) -> Event | None:
        index = next((alias for alias in aliases if alias.startswith("index:")), None)
        for alias in aliases:
            result = await self._live().execute(
                FIND_CLAUDE_TURN, {"session": session, "alias": alias, "index_alias": index}
            )
            if (row := result.first()) is not None:
                return event_from_row(row)
        return None

    async def bind_claude_turn(self, session: str, aliases: tuple[str, ...], event: str) -> None:
        await self._live().execute(
            BIND_CLAUDE_TURN,
            [{"session": session, "alias": alias, "event": event} for alias in aliases],
        )

    async def allocate_seq(self) -> int:
        result = await self._live().execute(ALLOCATE_SEQ)
        return int(result.scalar_one())

    async def find_noop_receipt(self, command: str, key: str) -> dict[str, Any] | None:
        value = (
            await self._live().execute(FIND_NOOP_RECEIPT, {"command": command, "key": key})
        ).scalar_one_or_none()
        receipt: dict[str, Any] | None = None if value is None else json.loads(value)
        return receipt

    async def insert_noop_receipt(self, command: str, key: str, receipt: dict[str, Any]) -> None:
        await self._live().execute(
            INSERT_NOOP_RECEIPT, {"command": command, "key": key, "receipt": json.dumps(receipt)}
        )

    async def active_item_subjects(self, kind: str) -> set[str]:
        return await items_sql.active_item_subjects(self._live(), kind)

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

    async def branch_lane(self, branch: str) -> Lane | None:
        return await lanes_sql.branch_lane(self._live(), branch)

    async def pull_request(self, repository: str, number: int) -> PullRequest | None:
        return await github_sql.pull_request(self._live(), repository, number)

    async def save_pull_request(self, pull: PullRequest) -> None:
        await github_sql.save_pull_request(self._live(), pull)

    async def fetch_cursor(self, repository: str, resource: str) -> FetchCursor | None:
        return await github_sql.fetch_cursor(self._live(), repository, resource)

    async def save_fetch_cursor(self, repository: str, resource: str, cursor: FetchCursor) -> None:
        await github_sql.save_fetch_cursor(self._live(), repository, resource, cursor)

    async def save_lane(self, lane: Lane) -> None:
        await lanes_sql.save_lane(self._live(), lane)

    async def insert_action(self, action: Action) -> None:
        await actions_sql.insert_action(self._live(), action)

    async def update_action(self, action: Action) -> None:
        await actions_sql.update_action(self._live(), action)

    async def save_item(self, item: Item) -> None:
        await items_sql.save_item(self._live(), item)
