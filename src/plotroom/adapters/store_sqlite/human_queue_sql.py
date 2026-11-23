"""Store decisions in the project's local SQLite database."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.human_queue import Decision

_INSERT_DECISION = text("""
    INSERT INTO decisions (id, who, exact_words, where_ref, governs, settles, created_at)
    VALUES (:id, :who, :exact_words, :where_ref, :governs, :settles, :created_at)
""")
_LIST_DECISIONS = text("""
    SELECT * FROM decisions AS decision
    WHERE :governs IS NULL OR EXISTS (
        SELECT 1 FROM json_each(decision.governs) AS ticket WHERE ticket.value = :governs
    )
    ORDER BY decision.rowid
""")


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
    rows = await connection.execute(_LIST_DECISIONS, {"governs": governs})
    return [decision_from_row(row) for row in rows]


def decision_from_row(row: Row[Any]) -> Decision:
    return Decision(
        row.id,
        row.who,
        row.exact_words,
        json.loads(row.where_ref),
        json.loads(row.governs),
        json.loads(row.settles),
        row.created_at,
    )
