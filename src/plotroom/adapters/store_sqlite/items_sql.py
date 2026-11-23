"""Next item reads and writes in the project transaction."""

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import Row, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.claims import OVERLAP_KINDS
from plotroom.core.next_items import Item

_ACTIVE_SUBJECTS = text(
    "SELECT subject_ref FROM next_items WHERE kind = :kind AND state != 'closed'"
)
_BY_ID = text("SELECT * FROM next_items WHERE id = :id")
_ACTIVE = text("SELECT * FROM next_items WHERE dedup_key = :key AND state != 'closed'")
_VISIBLE_OPEN = (
    "SELECT * FROM next_items WHERE state = 'open' AND (:role = 'orchestrator' OR role = :role)"
)
_ORDER = "ORDER BY rank_class, opened_seq LIMIT :limit OFFSET :offset"
_OPEN = text(f"{_VISIBLE_OPEN} {_ORDER}")
_OVERLAPS = text(
    "SELECT * FROM next_items WHERE state = 'open' AND (:role = 'orchestrator' OR role = :role) "
    "AND kind IN :kinds "
    "AND EXISTS (SELECT 1 FROM json_each(context, :path) WHERE value = :subject) "
    "ORDER BY rank_class, opened_seq LIMIT :limit OFFSET :offset"
).bindparams(bindparam("kinds", expanding=True))
_OPEN_BY_ID = text(
    "SELECT * FROM next_items WHERE id = :id AND state = 'open' "
    "AND (:role = 'orchestrator' OR role = :role)"
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
_NEXT_CHANGE = text("SELECT item FROM item_changes WHERE seq > :cursor ORDER BY seq LIMIT 1")
_NEXT_ROLE_CHANGE = text(
    "SELECT item FROM item_changes WHERE role = :role AND seq > :cursor ORDER BY seq LIMIT 1"
)


async def first_change(
    connection: AsyncConnection, role: str, cursor: int
) -> dict[str, Any] | None:
    query = _NEXT_CHANGE if role == "orchestrator" else _NEXT_ROLE_CHANGE
    value = (await connection.execute(query, {"role": role, "cursor": cursor})).scalar_one_or_none()
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


async def open_items(
    connection: AsyncConnection,
    role: str,
    *,
    offset: int = 0,
    limit: int = -1,
) -> list[Item]:
    rows = await connection.execute(_OPEN, {"role": role, "offset": offset, "limit": limit})
    return [_item(row) for row in rows]


async def open_item(connection: AsyncConnection, role: str, item_id: str) -> Item | None:
    row = (await connection.execute(_OPEN_BY_ID, {"role": role, "id": item_id})).first()
    return None if row is None else _item(row)


async def open_overlaps(
    connection: AsyncConnection,
    role: str,
    lane: str | None,
    pr: int | None,
    offset: int,
    limit: int,
) -> list[Item]:
    rows = await connection.execute(
        _OVERLAPS,
        {
            "role": role,
            "kinds": OVERLAP_KINDS,
            "path": "$.lanes" if lane is not None else "$.prs",
            "subject": lane if lane is not None else pr,
            "offset": offset,
            "limit": limit,
        },
    )
    return [_item(row) for row in rows]


async def save_item(connection: AsyncConnection, item: Item) -> None:
    fields = asdict(item)
    serialized = json.dumps(fields, allow_nan=False)
    fields["context"] = json.dumps(item.context, allow_nan=False)
    fields["suggested_actions"] = json.dumps(item.suggested_actions)
    await connection.execute(_SAVE, fields)
    await connection.execute(
        _INSERT_CHANGE, {"seq": item.seq, "role": item.role, "item": serialized}
    )
