"""Cursor and vector records for one model in the project's SQLite file."""

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.ports.search import (
    MESSAGE_KIND,
    EmbeddedMessage,
    EmbeddingModel,
    SearchHit,
    SearchPage,
)

EXCERPT_LIMIT = 240

READ_CURSOR = text("""
    SELECT index_seq FROM embedding_cursors WHERE model = :model AND dimension = :dimension
""")
THROUGH_EVENT = text("SELECT 1 FROM events WHERE seq = :through")
BATCH_MESSAGES = text("""
    SELECT id FROM events
    WHERE id IN :ids AND seq > :expected AND seq <= :through AND kind = :kind
""").bindparams(bindparam("ids", expanding=True))
SAVE_CURSOR = text("""
    INSERT INTO embedding_cursors (model, dimension, index_seq)
    VALUES (:model, :dimension, :seq)
    ON CONFLICT (model, dimension) DO UPDATE SET index_seq = excluded.index_seq
""")
SAVE_VECTOR = text("""
    INSERT INTO message_vectors (model, dimension, record_id, vector)
    VALUES (:model, :dimension, :id, :vector)
""")
COUNT_VECTORS = text("""
    SELECT count(*) FROM message_vectors WHERE model = :model AND dimension = :dimension
""")
FIND_VECTORS = text("""
    SELECT events.id, events.kind, substr(CAST(events.body AS BLOB), 1, :excerpt_bytes) AS excerpt
    FROM message_vectors JOIN events ON events.id = message_vectors.record_id
    WHERE model = :model AND dimension = :dimension
    ORDER BY vec_distance_l2(vector, :query), events.seq DESC
    LIMIT :limit
""")


async def read_cursor(connection: AsyncConnection, model: EmbeddingModel) -> int:
    row = await connection.execute(READ_CURSOR, {"model": model.name, "dimension": model.dimension})
    return int(row.scalar_one_or_none() or 0)


async def commit_batch(
    connection: AsyncConnection,
    model: EmbeddingModel,
    expected: int,
    through: int,
    records: tuple[EmbeddedMessage, ...],
) -> bool:
    # BEGIN IMMEDIATE already serializes this check and the writes below.
    if await read_cursor(connection, model) != expected:
        return False
    if through <= expected:
        raise ValueError("embedding batch must advance the cursor")
    endpoint = await connection.execute(THROUGH_EVENT, {"through": through})
    if endpoint.scalar_one_or_none() is None:
        raise ValueError("embedding batch must end at a committed event")
    key = {"model": model.name, "dimension": model.dimension}
    if records:
        ids = {record.id for record in records}
        eligible = await connection.execute(
            BATCH_MESSAGES,
            {"ids": tuple(ids), "expected": expected, "through": through, "kind": MESSAGE_KIND},
        )
        if ids != set(eligible.scalars()):
            raise ValueError("embedding records must be messages within the batch interval")
        await connection.execute(
            SAVE_VECTOR,
            [{**key, "id": record.id, "vector": record.vector} for record in records],
        )
    await connection.execute(SAVE_CURSOR, {**key, "seq": through})
    return True


async def query_vectors(
    connection: AsyncConnection, model: EmbeddingModel, query: bytes, limit: int
) -> SearchPage:
    key = {"model": model.name, "dimension": model.dimension}
    count = await connection.execute(COUNT_VECTORS, key)
    # A BLOB slice retains embedded NULs and bounds the read to four bytes per character.
    rows = await connection.execute(
        FIND_VECTORS, {**key, "query": query, "limit": limit, "excerpt_bytes": 4 * EXCERPT_LIMIT}
    )
    hits = tuple(
        SearchHit(row.id, row.kind, row.excerpt.decode("utf-8", "ignore")[:EXCERPT_LIMIT])
        for row in rows
    )
    return SearchPage(hits, count.scalar_one())
