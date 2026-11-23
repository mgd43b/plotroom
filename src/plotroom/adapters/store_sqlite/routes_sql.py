"""SQLite operations for hands-on action routes."""

from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

HAS_ROUTES = text("SELECT EXISTS (SELECT 1 FROM routes)")
FIND_ROUTE = text("SELECT lane FROM routes WHERE action_kind = :action_kind")
SAVE_ROUTE = text("""
    INSERT INTO routes (action_kind, lane) VALUES (:action_kind, :lane)
    ON CONFLICT (action_kind) DO UPDATE SET lane = excluded.lane
""")


async def has_routes(connection: AsyncConnection) -> bool:
    return bool((await connection.execute(HAS_ROUTES)).scalar_one())


async def find_route(connection: AsyncConnection, action_kind: str) -> str | None:
    result = await connection.execute(FIND_ROUTE, {"action_kind": action_kind})
    return cast("str | None", result.scalar_one_or_none())


async def save_route(connection: AsyncConnection, action_kind: str, lane: str) -> None:
    await connection.execute(SAVE_ROUTE, {"action_kind": action_kind, "lane": lane})
