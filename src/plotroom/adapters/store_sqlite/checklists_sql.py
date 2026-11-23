"""SQLite persistence for a lane's checklist header and ordered steps."""

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.checklists import Checklist, ChecklistStep

_HEADER = text("SELECT * FROM lane_checklists WHERE lane = :lane")
_STEPS = text("""
    SELECT step, owner, ref, completion_ref
    FROM lane_checklist_steps WHERE lane = :lane ORDER BY position
""")
_SAVE = text("""
    INSERT INTO lane_checklists (lane, version, repository, branch, pr, head_sha, state)
    VALUES (:lane, :version, :repository, :branch, :pr, :head_sha, :state)
    ON CONFLICT (lane) DO UPDATE SET version = excluded.version,
        repository = excluded.repository, branch = excluded.branch, pr = excluded.pr,
        head_sha = excluded.head_sha, state = excluded.state
""")
_CLEAR_STEPS = text("DELETE FROM lane_checklist_steps WHERE lane = :lane")
_INSERT_STEP = text("""
    INSERT INTO lane_checklist_steps (lane, position, step, owner, ref, completion_ref)
    VALUES (:lane, :position, :step, :owner, :ref, :completion_ref)
""")


async def find_checklist(connection: AsyncConnection, lane: str) -> Checklist | None:
    keys = {"lane": lane}
    header = (await connection.execute(_HEADER, keys)).mappings().first()
    if header is None:
        return None
    rows = (await connection.execute(_STEPS, keys)).mappings()
    steps = tuple(ChecklistStep(**row) for row in rows)
    return Checklist(**header, steps=steps)


async def save_checklist(connection: AsyncConnection, checklist: Checklist) -> None:
    keys = {
        "lane": checklist.lane,
        "version": checklist.version,
        "repository": checklist.repository,
        "branch": checklist.branch,
        "pr": checklist.pr,
        "head_sha": checklist.head_sha,
        "state": checklist.state,
    }
    await connection.execute(_SAVE, keys)
    await connection.execute(_CLEAR_STEPS, {"lane": checklist.lane})
    if checklist.steps:
        await connection.execute(
            _INSERT_STEP,
            [
                {"lane": checklist.lane, "position": position, **asdict(step)}
                for position, step in enumerate(checklist.steps)
            ],
        )
