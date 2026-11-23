"""The read-only source's durable projection."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.ports.work_items import WorkItem

_READY = text("SELECT ref, title FROM work_items ORDER BY source_order")
_CLEAR = text("DELETE FROM work_items")
_INSERT = text("INSERT INTO work_items (ref, title, source_order) VALUES (:ref, :title, :order)")
_CLAIMED = text(
    "SELECT DISTINCT item.value FROM lanes, json_each(lanes.work_items) AS item "
    "WHERE lanes.state != 'archived'"
)


async def ready_work_items(connection: AsyncConnection) -> tuple[WorkItem, ...]:
    return tuple(WorkItem(row.ref, row.title) for row in await connection.execute(_READY))


async def replace_work_items(connection: AsyncConnection, items: tuple[WorkItem, ...]) -> None:
    if items == await ready_work_items(connection):
        return
    await connection.execute(_CLEAR)
    if items:
        await connection.execute(
            _INSERT,
            [
                {"ref": item.ref, "title": item.title, "order": index}
                for index, item in enumerate(items)
            ],
        )


async def claimed_work_items(connection: AsyncConnection) -> set[str]:
    return {str(row.value) for row in await connection.execute(_CLAIMED)}
