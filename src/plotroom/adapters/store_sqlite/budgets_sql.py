"""SQLite budget projection and committed reads."""

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.budgets import Budget

_BUDGET = text("SELECT * FROM budgets WHERE run = :run")
_BUDGETS = text("SELECT * FROM budgets ORDER BY run")
_INSERT = text("""
    INSERT INTO budgets (id, run, lane, cap_usd, provider, key_scope, spent_usd, as_of)
    VALUES (:id, :run, :lane, :cap_usd, :provider, :key_scope, :spent_usd, :as_of)
""")
_UPDATE_SPEND = text("UPDATE budgets SET spent_usd = :spent_usd, as_of = :as_of WHERE id = :id")


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
