"""Reads and writes of path claims in the project database."""

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.claims import Claim

_LANE_SCOPE = "(:lane IS NULL OR lane_claims.lane = :lane)"
_VISIBLE = (
    "FROM lane_claims JOIN lanes ON lanes.id = lane_claims.lane "
    "WHERE lanes.state != 'archived' "
    "AND (:role IN ('orchestrator', 'clerk') OR :role = 'worker:' || lanes.worker)"
)
_READ = text(
    f"SELECT lane_claims.* {_VISIBLE} AND {_LANE_SCOPE} "
    "ORDER BY lane, glob LIMIT :limit OFFSET :offset"
)
_COUNT = text(f"SELECT count(*) {_VISIBLE}")
_SAVE = text(
    "INSERT INTO lane_claims (lane, glob, reason) VALUES (:lane, :glob, :reason) "
    "ON CONFLICT(lane, glob) DO UPDATE SET reason=excluded.reason"
)
_DELETE = text("DELETE FROM lane_claims WHERE lane = :lane AND glob = :glob")
_DELETE_LANE = text("DELETE FROM lane_claims WHERE lane = :lane")


async def read_claims(
    connection: AsyncConnection,
    role: str,
    lane: str | None = None,
    offset: int = 0,
    limit: int = -1,
) -> list[Claim]:
    rows = await connection.execute(
        _READ, {"role": role, "lane": lane, "offset": offset, "limit": limit}
    )
    return [Claim(row.lane, row.glob, row.reason) for row in rows]


async def count_claims(connection: AsyncConnection, role: str) -> int:
    return int((await connection.execute(_COUNT, {"role": role})).scalar_one())


async def save_claims(connection: AsyncConnection, claims: list[Claim]) -> None:
    await connection.execute(_SAVE, [asdict(entry) for entry in claims])


async def delete_claims(connection: AsyncConnection, lane: str, globs: list[str] | None) -> None:
    if globs is None:
        await connection.execute(_DELETE_LANE, {"lane": lane})
    elif globs:
        await connection.execute(_DELETE, [{"lane": lane, "glob": glob} for glob in globs])
