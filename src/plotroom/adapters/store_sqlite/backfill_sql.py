"""Bounded reads and transactional checkpoints for historical search."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.adapters.store_sqlite import asks_sql, github_sql, human_queue_sql, search_sql
from plotroom.core.github import PullRequest
from plotroom.core.github_projection import PULL_TEXT
from plotroom.core.search_sources import (
    ask_record,
    comment_record,
    decision_record,
    pull_body_record,
    pull_text_record,
)
from plotroom.ports.search import BackfillPage, BackfillPosition, BackfillSource, SearchRecord

_POSITION = text("SELECT value FROM project_meta WHERE key = :key")
_SAVE_POSITION = text("""
    INSERT INTO project_meta (key, value) VALUES (:key, :value)
    ON CONFLICT (key) DO UPDATE SET value = excluded.value
""")
_DECISIONS = text("SELECT * FROM decisions WHERE id > :after ORDER BY id LIMIT :limit")
_ASKS = text("SELECT * FROM human_asks WHERE id > :after ORDER BY id LIMIT :limit")
_COMMENTS = text("""
    SELECT * FROM github_resources
    WHERE resource = 'issue_comments' AND (repository, scope) > (:repository, :scope)
    ORDER BY repository, scope LIMIT :limit
""")
_PULLS = text("""
    SELECT * FROM github_pull_requests
    WHERE (repository, number) > (:repository, :number)
    ORDER BY repository, number LIMIT :limit
""")
# Keep the predicate identical to the partial index in revision 0022.
_LATEST_PULL = text("""
    SELECT body, json_extract(fields, '$.updated_at') AS updated_at
    FROM events
    WHERE source = 'github' AND kind IN (
        'github.pr_opened', 'github.pr_merged', 'github.pr_closed',
        'github.pr_reopened', 'github.pr_head_changed'
    )
      AND json_extract(fields, '$.repository') = :repository
      AND json_extract(fields, '$.number') = :number
    ORDER BY seq DESC LIMIT 1
""")


async def position(connection: AsyncConnection, source: BackfillSource) -> BackfillPosition:
    value = (
        await connection.execute(_POSITION, {"key": f"search.backfill.{source}"})
    ).scalar_one_or_none()
    if value is None:
        return ()
    decoded = json.loads(value)
    return None if decoded is None else tuple(decoded)


async def commit(
    connection: AsyncConnection,
    source: BackfillSource,
    expected: BackfillPosition,
    page: BackfillPage,
) -> bool:
    if await position(connection, source) != expected:
        return False
    for record in page.records:
        await search_sql.index_record(connection, record, only_if_absent=True)
    await connection.execute(
        _SAVE_POSITION, {"key": f"search.backfill.{source}", "value": json.dumps(page.through)}
    )
    return True


async def read_page(
    connection: AsyncConnection, source: BackfillSource, after: tuple[str | int, ...], limit: int
) -> BackfillPage:
    if source == BackfillSource.DECISIONS or source == BackfillSource.ASKS:
        query = _DECISIONS if source == BackfillSource.DECISIONS else _ASKS
        params = {"after": after[0] if after else "", "limit": limit}
        rows = (await connection.execute(query, params)).all()
        records = (
            tuple(decision_record(human_queue_sql.decision_from_row(row)) for row in rows)
            if source == BackfillSource.DECISIONS
            else tuple(ask_record(asks_sql.ask_from_row(row)) for row in rows)
        )
        return BackfillPage((rows[-1].id,) if rows else None, records)
    if source == BackfillSource.COMMENTS:
        repository, scope = after or ("", "")
        params = {"repository": repository, "scope": scope, "limit": limit}
        rows = (await connection.execute(_COMMENTS, params)).all()
        comments: list[SearchRecord] = []
        for row in rows:
            resource = github_sql.resource_from_row(row)
            if resource.complete and len(resource.records) == 1:
                (comment,) = resource.records
                if comment.key == row.scope and comment.kind == "issue_comment":
                    comments.append(comment_record(row.repository, comment))
        return BackfillPage(
            (rows[-1].repository, rows[-1].scope) if rows else None, tuple(comments)
        )
    if source == BackfillSource.PULLS:
        repository, number = after or ("", 0)
        params = {"repository": repository, "number": number, "limit": limit}
        rows = (await connection.execute(_PULLS, params)).all()
        pulls: list[SearchRecord] = []
        for row in rows:
            record = await _retained_pull_text(connection, github_sql.pull_from_row(row))
            if record is not None:
                pulls.append(record)
        return BackfillPage((rows[-1].repository, rows[-1].number) if rows else None, tuple(pulls))
    raise ValueError("ledger event pages are read through the ledger")


async def _retained_pull_text(
    connection: AsyncConnection, pull: PullRequest
) -> SearchRecord | None:
    latest = (
        await connection.execute(
            _LATEST_PULL, {"repository": pull.repository, "number": pull.number}
        )
    ).first()
    snapshot = await github_sql.github_resource(
        connection, pull.repository, PULL_TEXT, str(pull.number)
    )
    if snapshot is not None:
        # Failed refreshes mark the resource incomplete and retain its verified attributes.
        record = pull_text_record(
            pull, snapshot, updated_since=latest.updated_at if latest else "", allow_incomplete=True
        )
        if record is not None:
            return record
    if latest is None:
        return None
    # Lifecycle metadata can match even when a body-only edit was never recorded.
    return pull_body_record(pull.repository, pull.number, latest.body, provisional=True)
