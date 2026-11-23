"""Store decisions in the project's local SQLite database."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.human_queue import Decision

_INSERT_DECISION = text("""
    INSERT INTO decisions (id, who, exact_words, where_ref, governs, settles, created_at)
    VALUES (:id, :who, :exact_words, :where_ref, :governs, :settles, :created_at)
""")
_LIST_DECISIONS = text("SELECT * FROM decisions ORDER BY rowid")


async def insert_decision(connection: AsyncConnection, decision: Decision) -> None:
    await connection.execute(
        _INSERT_DECISION,
        {
            "id": decision.decision_id,
            "who": decision.who,
            "exact_words": decision.exact_words,
            "where_ref": json.dumps(decision.where_ref),
            "governs": json.dumps(decision.governs),
            "settles": json.dumps(decision.settles),
            "created_at": decision.created_at,
        },
    )


async def list_decisions(connection: AsyncConnection, governs: str | None) -> list[Decision]:
    decisions: list[Decision] = []
    for row in await connection.execute(_LIST_DECISIONS):
        ticket_refs = json.loads(row.governs)
        if governs is None or governs in ticket_refs:
            decisions.append(
                Decision(
                    row.id,
                    row.who,
                    row.exact_words,
                    json.loads(row.where_ref),
                    ticket_refs,
                    json.loads(row.settles),
                    row.created_at,
                )
            )
    return decisions
