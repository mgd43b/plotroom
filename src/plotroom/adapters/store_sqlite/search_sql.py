"""Full-text queries and updates on a project's committed search records."""

import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.ports.search import SearchHit, SearchPage, SearchRecord

EXCERPT_LIMIT = 240

FIND_RECORD = text("""
    SELECT seq, body, provisional FROM search_records WHERE kind = :kind AND record_id = :id
""")
INSERT_RECORD = text("""
    INSERT INTO search_records (kind, record_id, body, embeddable, provisional)
    VALUES (:kind, :id, :body, :embeddable, :provisional)
    RETURNING seq
""")
CONFIRM_RECORD = text("UPDATE search_records SET provisional = FALSE WHERE seq = :seq")
DELETE_RECORD = text("DELETE FROM search_records WHERE seq = :seq")
DELETE_MESSAGE = text("DELETE FROM message_fts WHERE rowid = :seq")
INSERT_MESSAGE = text("INSERT INTO message_fts (rowid, body) VALUES (:seq, :body)")
COUNT_MATCHES = text("""
    SELECT count(*)
    FROM message_fts JOIN search_records ON search_records.seq = message_fts.rowid
    WHERE message_fts MATCH :query
      AND (:kinds IS NULL OR search_records.kind IN (SELECT value FROM json_each(:kinds)))
""")
# Rank and limit the row IDs before highlighting their bodies. Rank each record
# by its own byte length so hidden records cannot influence visible results.
FIND_MATCHES = text("""
    WITH ranked AS MATERIALIZED (
        SELECT search_records.record_id AS id, search_records.kind, search_records.seq,
               octet_length(search_records.body) AS body_bytes
        FROM message_fts JOIN search_records ON search_records.seq = message_fts.rowid
        WHERE message_fts MATCH :query
          AND (:kinds IS NULL OR search_records.kind IN (SELECT value FROM json_each(:kinds)))
        ORDER BY body_bytes, search_records.seq DESC
        LIMIT :limit
    ), matches AS MATERIALIZED (
        SELECT ranked.*, message_fts.body,
               highlight(message_fts, 0, :open_marker, :close_marker) AS marked_body
        FROM ranked CROSS JOIN message_fts
        WHERE message_fts.rowid = ranked.seq AND message_fts MATCH :query
    ), spans AS (
        SELECT *, instr(marked_body, :open_marker) AS match_start,
               instr(marked_body, :close_marker) - instr(marked_body, :open_marker)
                   - length(:open_marker) AS match_length
        FROM matches
    ), windows AS (
        SELECT *, max(1, match_start - min(60, max(0, :excerpt_limit - match_length)))
                      AS excerpt_start
        FROM spans
    )
    SELECT id, kind,
           substr(body, excerpt_start, :excerpt_limit) AS excerpt,
           match_start - excerpt_start AS match_offset,
           min(:excerpt_limit, match_start - excerpt_start + match_length) AS match_end
    FROM windows ORDER BY body_bytes, seq DESC
""")


async def index_record(
    connection: AsyncConnection, record: SearchRecord, *, only_if_absent: bool
) -> None:
    params = {"kind": record.kind, "id": record.id, "body": record.body}
    current = (await connection.execute(FIND_RECORD, params)).first()
    if current is not None:
        confirms = current.provisional and not record.provisional
        if current.body == record.body:
            if confirms:
                await connection.execute(CONFIRM_RECORD, {"seq": current.seq})
            return
        if only_if_absent and not confirms:
            return
        await connection.execute(DELETE_MESSAGE, {"seq": current.seq})
        await connection.execute(DELETE_RECORD, {"seq": current.seq})
    seq = (
        await connection.execute(
            INSERT_RECORD,
            {**params, "embeddable": bool(record.body.strip()), "provisional": record.provisional},
        )
    ).scalar_one()
    # SQLite's text functions can stop at a NUL. Index it as a word separator.
    await connection.execute(INSERT_MESSAGE, {"seq": seq, "body": record.body.replace("\x00", " ")})


async def search_records(
    connection: AsyncConnection, query: str, kinds: tuple[str, ...] | None, limit: int
) -> SearchPage:
    # Quote the phrase so user-supplied punctuation cannot become FTS query syntax.
    phrase = '"' + query.replace('"', '""') + '"'
    params = {"query": phrase, "kinds": None if kinds is None else json.dumps(kinds)}
    total = (await connection.execute(COUNT_MATCHES, params)).scalar_one()
    # Highlight the complete phrase and give it priority over surrounding text.
    marker = uuid4().hex
    rows = await connection.execute(
        FIND_MATCHES,
        {
            **params,
            "limit": limit,
            "excerpt_limit": EXCERPT_LIMIT,
            "open_marker": f"<{marker}",
            "close_marker": f">{marker}",
        },
    )
    hits = tuple(
        SearchHit(row.id, row.kind, row.excerpt, match_span=(row.match_offset, row.match_end))
        for row in rows
    )
    return SearchPage(hits, total)
