"""Next item reads and writes in the project transaction."""

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.next_items import Item

_ACTIVE_SUBJECTS = text(
    "SELECT subject_ref FROM next_items WHERE kind = :kind AND state != 'closed'"
)
_BY_ID = text("SELECT * FROM next_items WHERE id = :id")
_ACTIVE = text("SELECT * FROM next_items WHERE dedup_key = :key AND state != 'closed'")
_OPEN = text(
    "SELECT * FROM next_items WHERE state = 'open' AND (:role = 'orchestrator' OR role = :role) "
    "ORDER BY rank_class, opened_seq"
)
_SAVE = text(
    "INSERT INTO next_items "
    "(id, kind, subject_ref, dedup_key, role, rank_class, state, opened_seq, closed_seq, seq, "
    "context, suggested_actions) VALUES "
    "(:id, :kind, :subject_ref, :dedup_key, :role, :rank_class, :state, :opened_seq, "
    ":closed_seq, :seq, :context, :suggested_actions) ON CONFLICT(id) DO UPDATE SET "
    "state=excluded.state, closed_seq=excluded.closed_seq, seq=excluded.seq, "
    "context=excluded.context"
)


_INSERT_CHANGE = text("INSERT INTO item_changes (seq, role, item) VALUES (:seq, :role, :item)")
_NEXT_CHANGE = text(
    "SELECT item FROM item_changes WHERE seq > :cursor "
    "AND (:role = 'orchestrator' OR role = :role) ORDER BY seq LIMIT 1"
)


async def first_change(
    connection: AsyncConnection, role: str, cursor: int
) -> dict[str, Any] | None:
    value = (
        await connection.execute(_NEXT_CHANGE, {"role": role, "cursor": cursor})
    ).scalar_one_or_none()
    item: dict[str, Any] | None = None if value is None else json.loads(value)
    return item


def _item(row: Row[Any]) -> Item:
    fields = dict(row._mapping)  # pyright: ignore[reportPrivateUsage]
    fields["context"] = json.loads(row.context)
    fields["suggested_actions"] = tuple(json.loads(row.suggested_actions))
    return Item(**fields)


async def active_item_subjects(connection: AsyncConnection, kind: str) -> set[str]:
    return {
        str(row.subject_ref) for row in await connection.execute(_ACTIVE_SUBJECTS, {"kind": kind})
    }


async def find_item(connection: AsyncConnection, item_id: str) -> Item | None:
    row = (await connection.execute(_BY_ID, {"id": item_id})).first()
    return None if row is None else _item(row)


async def active_item(connection: AsyncConnection, key: str) -> Item | None:
    row = (await connection.execute(_ACTIVE, {"key": key})).first()
    return None if row is None else _item(row)


async def open_items(connection: AsyncConnection, role: str) -> list[Item]:
    return [_item(row) for row in await connection.execute(_OPEN, {"role": role})]


async def save_item(connection: AsyncConnection, item: Item) -> None:
    fields = asdict(item)
    serialized = json.dumps(fields, allow_nan=False)
    fields["context"] = json.dumps(item.context, allow_nan=False)
    fields["suggested_actions"] = json.dumps(item.suggested_actions)
    await connection.execute(_SAVE, fields)
    await connection.execute(
        _INSERT_CHANGE, {"seq": item.seq, "role": item.role, "item": serialized}
    )
