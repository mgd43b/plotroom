"""Full-text queries on a project's committed Switchboard message index."""

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.core.events import Event
from plotroom.ports.search import SearchHit, SearchPage

INSERT_MESSAGE = text("""
    INSERT INTO message_fts (rowid, body)
    SELECT seq, body FROM events WHERE seq = :seq AND id = :id AND kind = :kind
""")
COUNT_MATCHES = text("SELECT count(*) FROM message_fts WHERE message_fts MATCH :query")
FIND_MATCHES = text("""
    WITH matches AS MATERIALIZED (
        SELECT events.id, events.kind, events.seq, message_fts.rank,
               snippet(message_fts, 0, :marker, :marker, '…', 12) AS excerpt
        FROM message_fts JOIN events ON events.seq = message_fts.rowid
        WHERE message_fts MATCH :query
        ORDER BY message_fts.rank, events.seq DESC
        LIMIT :limit
    )
    SELECT id, kind,
           substr(
               replace(substr(excerpt, max(1, instr(excerpt, :marker) - 60)), :marker, ''), 1, 240
           ) AS excerpt
    FROM matches ORDER BY rank, seq DESC
""")


async def insert_message(connection: AsyncConnection, event: Event) -> None:
    result = await connection.execute(
        INSERT_MESSAGE, {"seq": event.seq, "id": event.id, "kind": event.kind}
    )
    if result.rowcount != 1:
        raise RuntimeError("cannot index a message without its matching local event")


async def search_messages(connection: AsyncConnection, query: str, limit: int) -> SearchPage:
    # Quote the phrase so user-supplied punctuation cannot become FTS query syntax.
    phrase = '"' + query.replace('"', '""') + '"'
    total = (await connection.execute(COUNT_MATCHES, {"query": phrase})).scalar_one()
    # The marker keeps a long adjacent token from pushing the match out of the excerpt.
    rows = await connection.execute(
        FIND_MATCHES, {"query": phrase, "limit": limit, "marker": uuid4().hex}
    )
    hits = tuple(SearchHit(row.id, row.kind, row.excerpt) for row in rows)
    return SearchPage(hits, total)
