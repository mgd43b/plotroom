"""SQLite budget projection and committed reads."""

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.budgets import Budget
from plotroom.core.events import format_time

_BUDGET = text("SELECT * FROM budgets WHERE run = :run")
_BUDGETS = text("SELECT * FROM budgets ORDER BY run")
_INSERT = text("""
    INSERT INTO budgets (id, run, lane, cap_usd, provider, key_scope, spent_usd, as_of)
    VALUES (:id, :run, :lane, :cap_usd, :provider, :key_scope, :spent_usd, :as_of)
""")
_OPENROUTER_PREFIX = "openrouter.binding."
_OPENROUTER_RESERVE = text("""
    INSERT INTO project_meta (key, value) VALUES (:key, :run)
    ON CONFLICT (key) DO NOTHING RETURNING key
""")
_OPENROUTER_BUDGETS = text("""
    SELECT budgets.run, budgets.cap_usd, substr(project_meta.key, :start) AS reservation
    FROM budgets JOIN project_meta ON project_meta.value = budgets.run
    JOIN lanes ON lanes.id = budgets.lane
    WHERE budgets.provider = 'openrouter' AND project_meta.key GLOB :pattern
      AND (lanes.state != 'archived' OR budgets.lane IN (
        SELECT json_extract(fields, '$.args.lane') FROM events
        WHERE kind = 'core.lane_archive' AND observed_at > :archive_cutoff
      ))
    ORDER BY budgets.run
""")
_UPDATE_SPEND = text("UPDATE budgets SET spent_usd = :spent_usd, as_of = :as_of WHERE id = :id")


def foreign_openrouter_bindings(data_dir: Path, owners: Mapping[str, str]) -> set[str]:
    """Check earlier reservations even in inactive projects, without opening them for writes."""
    blocked: set[str] = set()
    for path in data_dir.glob("*.db"):
        if all(project_id == path.stem for project_id in owners.values()):
            continue
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone() is None:
                continue
            rows = conn.execute(
                "SELECT key FROM project_meta WHERE key GLOB ?", (_OPENROUTER_PREFIX + "*",)
            )
            for (key,) in rows:
                reservation = str(key).removeprefix(_OPENROUTER_PREFIX)
                owner = owners.get(reservation)
                if owner is not None and owner != path.stem:
                    blocked.add(reservation)
    return blocked


async def reserve_openrouter_key(connection: AsyncConnection, reservation: str, run: str) -> bool:
    inserted = await connection.execute(
        _OPENROUTER_RESERVE, {"key": _OPENROUTER_PREFIX + reservation, "run": run}
    )
    return inserted.scalar_one_or_none() is not None


async def read_openrouter_budgets(
    connection: AsyncConnection, now: datetime
) -> list[tuple[str, float, str]]:
    rows = await connection.execute(
        _OPENROUTER_BUDGETS,
        {
            "start": len(_OPENROUTER_PREFIX) + 1,
            "pattern": _OPENROUTER_PREFIX + "*",
            "archive_cutoff": format_time(now - timedelta(minutes=2)),
        },
    )
    return [(str(row.run), float(row.cap_usd), str(row.reservation)) for row in rows]


async def find_budget(connection: AsyncConnection, run: str) -> Budget | None:
    row = (await connection.execute(_BUDGET, {"run": run})).mappings().one_or_none()
    return Budget(**row) if row is not None else None


async def read_budgets(connection: AsyncConnection, run: str | None) -> list[Budget]:
    if run is None:
        return [Budget(**row) for row in (await connection.execute(_BUDGETS)).mappings()]
    budget = await find_budget(connection, run)
    return [] if budget is None else [budget]


async def insert_budget(connection: AsyncConnection, budget: Budget) -> None:
    await connection.execute(_INSERT, asdict(budget))


async def update_budget_spend(connection: AsyncConnection, budget: Budget) -> None:
    await connection.execute(
        _UPDATE_SPEND,
        {"id": budget.id, "spent_usd": budget.spent_usd, "as_of": budget.as_of},
    )
