"""Persistence for an operator's named goal text."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.goal_projection import StoredGoal

_SAVE = text("""
    INSERT INTO operator_goals (name, text, updated_at, uses_blockers, uses_lanes, uses_decisions)
    VALUES (:name, :text, :updated_at, :uses_blockers, :uses_lanes, :uses_decisions)
    ON CONFLICT (name) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at,
        uses_blockers = excluded.uses_blockers, uses_lanes = excluded.uses_lanes,
        uses_decisions = excluded.uses_decisions
""")
_LIST = text("""
    SELECT name, text, updated_at, uses_blockers, uses_lanes, uses_decisions FROM operator_goals
    WHERE :name IS NULL OR name = :name
    ORDER BY name
""")


async def save_goal(connection: AsyncConnection, goal: StoredGoal) -> None:
    await connection.execute(
        _SAVE,
        {
            "name": goal.name,
            "text": goal.text,
            "updated_at": goal.updated_at,
            "uses_blockers": goal.uses_blockers,
            "uses_lanes": goal.uses_lanes,
            "uses_decisions": goal.uses_decisions,
        },
    )


async def list_goals(connection: AsyncConnection, name: str | None) -> list[StoredGoal]:
    return [
        StoredGoal(
            row.name,
            row.text,
            row.updated_at,
            bool(row.uses_blockers),
            bool(row.uses_lanes),
            bool(row.uses_decisions),
        )
        for row in await connection.execute(_LIST, {"name": name})
    ]


async def goals_use_blockers(connection: AsyncConnection) -> bool:
    return bool(
        (
            await connection.execute(
                text("SELECT EXISTS(SELECT 1 FROM operator_goals WHERE uses_blockers)")
            )
        ).scalar_one()
    )
