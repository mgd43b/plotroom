"""Ask records and the comments needed to check them."""

import json
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.ask_checks import Ask, IssueComment, comment_time
from plotroom.core.github_projection import COMMENTS

_ASK_FROM_COMMENT = text(
    "SELECT * FROM human_asks WHERE repository = :repository AND comment_id = :comment_id"
)
_ASK_BY_ID = text("SELECT * FROM human_asks WHERE id = :id")
_SAVE_ASK = text("""
    INSERT INTO human_asks
      (id, repository, issue, text, options, comment_id, comment_created_at, created_at, settled_by)
    VALUES
      (:id, :repository, :issue, :text, :options, :comment_id, :comment_created_at, :created_at,
       :settled_by)
    ON CONFLICT (id) DO UPDATE SET text = excluded.text, options = excluded.options,
      settled_by = excluded.settled_by
""")
_LIST_ASKS = text("""
    SELECT ask.* FROM human_asks AS ask
    WHERE NOT :stale_only OR EXISTS (
        SELECT 1 FROM next_items AS item
        WHERE item.subject_ref = ask.id AND item.kind = 'ask_stale' AND item.state != 'closed'
    )
    ORDER BY ask.created_at, ask.id
""")
_PENDING_ISSUES = text(
    "SELECT DISTINCT issue FROM human_asks WHERE repository = :repository AND settled_by IS NULL"
)
_UNSETTLED = text("""
    SELECT * FROM human_asks WHERE repository = :repository AND settled_by IS NULL ORDER BY id
""")
_ASK_COMMENTS = text("""
    SELECT json_extract(records, '$[0].fields') AS fields
    FROM github_resources
    WHERE repository = :repository AND resource = :resource
      AND json_extract(records, '$[0].fields.number') IN (SELECT value FROM json_each(:issues))
""")


def _ask(row: Row[Any]) -> Ask:
    return Ask(
        ask_id=row.id,
        repository=row.repository,
        issue=row.issue,
        text=row.text,
        options=tuple(json.loads(row.options)),
        comment_id=row.comment_id,
        comment_created_at=row.comment_created_at,
        created_at=row.created_at,
        settled_by=row.settled_by,
    )


async def ask_for_comment(
    connection: AsyncConnection, repository: str, comment_id: int
) -> Ask | None:
    row = (
        await connection.execute(
            _ASK_FROM_COMMENT, {"repository": repository, "comment_id": comment_id}
        )
    ).first()
    return None if row is None else _ask(row)


async def find_ask(connection: AsyncConnection, ask_id: str) -> Ask | None:
    row = (await connection.execute(_ASK_BY_ID, {"id": ask_id})).first()
    return None if row is None else _ask(row)


async def save_ask(connection: AsyncConnection, ask: Ask) -> None:
    await connection.execute(
        _SAVE_ASK,
        {
            "id": ask.ask_id,
            "repository": ask.repository,
            "issue": ask.issue,
            "text": ask.text,
            "options": json.dumps(ask.options),
            "comment_id": ask.comment_id,
            "comment_created_at": ask.comment_created_at,
            "created_at": ask.created_at,
            "settled_by": ask.settled_by,
        },
    )


async def list_asks(connection: AsyncConnection, stale_only: bool) -> list[Ask]:
    return [_ask(row) for row in await connection.execute(_LIST_ASKS, {"stale_only": stale_only})]


async def pending_ask_issues(connection: AsyncConnection, repository: str) -> set[int]:
    rows = await connection.execute(_PENDING_ISSUES, {"repository": repository})
    return {row.issue for row in rows}


async def unsettled_asks(connection: AsyncConnection, repository: str) -> list[Ask]:
    return [_ask(row) for row in await connection.execute(_UNSETTLED, {"repository": repository})]


async def ask_comments(
    connection: AsyncConnection, repository: str, issues: set[int]
) -> dict[int, list[IssueComment]]:
    results: dict[int, list[IssueComment]] = {}
    rows = await connection.execute(
        _ASK_COMMENTS,
        {"repository": repository, "resource": COMMENTS, "issues": json.dumps(sorted(issues))},
    )
    for row in rows:
        fields = json.loads(row.fields)
        results.setdefault(fields["number"], []).append(
            IssueComment(fields["id"], fields.get("author"), comment_time(fields["created_at"]))
        )
    return results
