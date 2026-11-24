"""Cursor and vector revisions for one model in the project's SQLite file."""

import json
from collections.abc import Mapping
from dataclasses import replace

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from plotroom.adapters.store_sqlite.search_sql import literal_phrase, search_records
from plotroom.core.search_sources import EVENT_KINDS
from plotroom.ports.search import (
    ASK_KIND,
    DECISION_KIND,
    ISSUE_COMMENT_KIND,
    PR_BODY_KIND,
    BackfillSource,
    EmbeddedRecord,
    EmbeddingModel,
    EmbeddingPending,
    EmbeddingReadiness,
    EmbeddingRevision,
    IndexedEmbedding,
    SearchHit,
    SearchPage,
    SearchRecord,
)

EXCERPT_LIMIT = 240
SOURCE_KINDS = {
    BackfillSource.EVENTS: EVENT_KINDS,
    BackfillSource.DECISIONS: {DECISION_KIND},
    BackfillSource.ASKS: {ASK_KIND},
    BackfillSource.COMMENTS: {ISSUE_COMMENT_KIND},
    BackfillSource.PULLS: {PR_BODY_KIND},
}

READ_CURSOR = text("""
    SELECT index_seq FROM embedding_cursors WHERE model = :model AND dimension = :dimension
""")
READ_PAGE = text("""
    SELECT r.seq, r.kind, r.record_id,
           CASE WHEN r.embeddable AND v.record_seq IS NULL AND x.record_seq IS NULL
                THEN r.body END AS body
    FROM search_records AS r
    LEFT JOIN search_vectors AS v
      ON v.record_seq = r.seq AND v.model = :model AND v.dimension = :dimension
    LEFT JOIN embedding_rejections AS x
      ON x.record_seq = r.seq AND x.model = :model AND x.dimension = :dimension
    WHERE r.seq > :after
    ORDER BY r.seq LIMIT :limit
""")
HIGH_WATER = text("SELECT seq FROM sqlite_sequence WHERE name = 'search_records'")
BATCH_REVISIONS = text("""
    SELECT seq, kind, record_id, embeddable FROM search_records
    WHERE seq IN :sequences OR (kind, record_id) IN (
        SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]')
        FROM json_each(:identities)
    )
""").bindparams(bindparam("sequences", expanding=True))
SAVE_CURSOR = text("""
    INSERT INTO embedding_cursors (model, dimension, index_seq)
    VALUES (:model, :dimension, :seq)
    ON CONFLICT (model, dimension) DO UPDATE SET index_seq = excluded.index_seq
""")
SAVE_VECTOR = text("""
    INSERT INTO search_vectors (model, dimension, record_seq, vector)
    VALUES (:model, :dimension, :seq, :vector)
""")
SAVE_REJECTION = text("""
    INSERT INTO embedding_rejections (model, dimension, record_seq, reason)
    VALUES (:model, :dimension, :seq, :reason)
""")
REJECTED_MATCH = text("""
    SELECT EXISTS (
        SELECT 1 FROM message_fts
        JOIN embedding_rejections AS x ON x.record_seq = message_fts.rowid
        JOIN search_records AS r ON r.seq = x.record_seq
        WHERE message_fts MATCH :phrase AND x.model = :model AND x.dimension = :dimension
          AND (:kinds IS NULL OR r.kind IN (SELECT value FROM json_each(:kinds)))
    )
""")
UNFINISHED_SOURCES = text("""
    SELECT EXISTS (
        SELECT 1 FROM json_each(:sources) AS sources
        LEFT JOIN project_meta AS meta ON meta.key = 'search.backfill.' || sources.value
        WHERE meta.value IS NOT 'null'
    )
""")
PENDING_REVISIONS = text("""
    SELECT EXISTS (
        SELECT 1 FROM search_records AS r
        WHERE r.embeddable AND r.kind IN (SELECT value FROM json_each(:kinds))
          AND r.seq > COALESCE((
              SELECT index_seq FROM embedding_cursors
              WHERE model = :model AND dimension = :dimension
          ), 0)
          AND NOT EXISTS (
              SELECT 1 FROM search_vectors AS v
              WHERE v.model = :model AND v.dimension = :dimension AND v.record_seq = r.seq
          )
          AND NOT EXISTS (
              SELECT 1 FROM embedding_rejections AS x
              WHERE x.model = :model AND x.dimension = :dimension AND x.record_seq = r.seq
          )
    )
""")
ELIGIBLE_RECORDS = text("""
    SELECT EXISTS (
        SELECT 1 FROM search_records AS r
        WHERE r.embeddable AND r.kind IN (SELECT value FROM json_each(:kinds))
          AND NOT EXISTS (
              SELECT 1 FROM embedding_rejections AS x
              WHERE x.model = :model AND x.dimension = :dimension AND x.record_seq = r.seq
          )
    )
""")
COUNT_RECORDS = text("""
    SELECT count(*) FILTER (WHERE x.record_seq IS NULL) AS eligible,
           count(v.record_seq) AS indexed
    FROM search_records AS r
    LEFT JOIN search_vectors AS v
      ON v.record_seq = r.seq AND v.model = :model AND v.dimension = :dimension
    LEFT JOIN embedding_rejections AS x
      ON x.record_seq = r.seq AND x.model = :model AND x.dimension = :dimension
    WHERE r.embeddable
      AND (:kinds IS NULL OR r.kind IN (SELECT value FROM json_each(:kinds)))
""")
FIND_VECTORS = text("""
    SELECT r.record_id AS id, r.kind, substr(CAST(r.body AS BLOB), 1, :excerpt_bytes) AS excerpt
    FROM search_vectors AS v JOIN search_records AS r ON v.record_seq = r.seq
    WHERE v.model = :model AND v.dimension = :dimension AND r.embeddable
      AND (:kinds IS NULL OR r.kind IN (SELECT value FROM json_each(:kinds)))
    ORDER BY vec_distance_l2(v.vector, :query), r.seq DESC
    LIMIT :limit
""")


async def read_cursor(connection: AsyncConnection, model: EmbeddingModel) -> int:
    row = await connection.execute(READ_CURSOR, {"model": model.name, "dimension": model.dimension})
    return int(row.scalar_one_or_none() or 0)


async def read_page(
    connection: AsyncConnection, model: EmbeddingModel, after: int, limit: int
) -> list[EmbeddingRevision]:
    key = {"model": model.name, "dimension": model.dimension}
    rows = await connection.execute(READ_PAGE, {**key, "after": after, "limit": limit})
    return [
        EmbeddingRevision(
            row.seq,
            SearchRecord(row.kind, row.record_id, row.body) if row.body is not None else None,
        )
        for row in rows
    ]


async def commit_batch(
    connection: AsyncConnection,
    model: EmbeddingModel,
    expected: int,
    through: int,
    records: tuple[IndexedEmbedding, ...],
) -> bool:
    # BEGIN IMMEDIATE serializes cursor validation, revision checks and writes.
    if await read_cursor(connection, model) != expected:
        return False
    if through <= expected:
        raise ValueError("embedding batch must advance the cursor")
    high_water = (await connection.execute(HIGH_WATER)).scalar_one_or_none()
    if high_water is None or through > high_water:
        raise ValueError("embedding batch must end within committed search revisions")
    key = {"model": model.name, "dimension": model.dimension}
    if records:
        sequences = {record.seq for record in records}
        if len(sequences) != len(records) or not all(
            expected < seq <= through for seq in sequences
        ):
            raise ValueError("embedding records must be revisions within the batch interval")
        identities = {(record.kind, record.id) for record in records}
        result = await connection.execute(
            BATCH_REVISIONS,
            {"sequences": tuple(sequences), "identities": json.dumps(list(identities))},
        )
        rows = result.all()
        by_seq = {row.seq: row for row in rows}
        by_identity = {(row.kind, row.record_id): row for row in rows}
        vectors: list[dict[str, str | int | bytes]] = []
        rejections: list[dict[str, str | int]] = []
        for record in records:
            current = by_identity.get((record.kind, record.id))
            if current is not None and current.seq == record.seq and current.embeddable:
                if isinstance(record, EmbeddedRecord):
                    vectors.append({**key, "seq": record.seq, "vector": record.vector})
                else:
                    rejections.append({**key, "seq": record.seq, "reason": record.reason})
            elif current is None or current.seq <= through or record.seq in by_seq:
                raise ValueError("embedding records must be revisions within the batch interval")
            # Skip dispositions for revisions superseded during the HTTP request.
        if vectors:
            await connection.execute(SAVE_VECTOR, vectors)
        if rejections:
            await connection.execute(SAVE_REJECTION, rejections)
    await connection.execute(SAVE_CURSOR, {**key, "seq": through})
    return True


async def backfill_pending(connection: AsyncConnection, kinds: tuple[str, ...]) -> bool:
    sources = [source for source, members in SOURCE_KINDS.items() if members.intersection(kinds)]
    pending = await connection.execute(UNFINISHED_SOURCES, {"sources": json.dumps(sources)})
    return bool(pending.scalar_one())


async def preflight(
    connection: AsyncConnection,
    model: EmbeddingModel,
    kinds: tuple[str, ...],
    query_text: str | None,
) -> EmbeddingReadiness:
    if not kinds:
        return EmbeddingReadiness.EMPTY
    params = {"model": model.name, "dimension": model.dimension, "kinds": json.dumps(kinds)}
    if await rejected_match(connection, params, query_text):
        return EmbeddingReadiness.REJECTED_MATCH
    if await backfill_pending(connection, kinds):
        return EmbeddingReadiness.PENDING
    # The writer advances the cursor and saves dispositions atomically. Migrated vectors
    # can already cover newer revisions, so only unresolved revisions above it are pending.
    if (await connection.execute(PENDING_REVISIONS, params)).scalar_one():
        return EmbeddingReadiness.PENDING
    if (await connection.execute(ELIGIBLE_RECORDS, params)).scalar_one():
        return EmbeddingReadiness.READY
    return EmbeddingReadiness.EMPTY


async def rejected_match(
    connection: AsyncConnection, key: Mapping[str, str | int | None], query_text: str | None
) -> bool:
    if query_text is None:
        return False
    result = await connection.execute(REJECTED_MATCH, {**key, "phrase": literal_phrase(query_text)})
    return bool(result.scalar_one())


async def query_vectors(
    connection: AsyncConnection,
    model: EmbeddingModel,
    query: bytes,
    limit: int,
    kinds: tuple[str, ...] | None,
    query_text: str | None,
) -> SearchPage:
    key = {
        "model": model.name,
        "dimension": model.dimension,
        "kinds": None if kinds is None else json.dumps(kinds),
    }
    if query_text is not None and await rejected_match(connection, key, query_text):
        page = await search_records(connection, query_text, kinds, limit)
        return replace(page, mode="lexical", fallback_reason="embedding_rejected")
    if kinds is not None and await backfill_pending(connection, kinds):
        raise EmbeddingPending
    eligible, indexed = (await connection.execute(COUNT_RECORDS, key)).one()
    # Direct index reads can inspect an incomplete prefix.
    if kinds is not None and eligible != indexed:
        raise EmbeddingPending
    # A BLOB slice retains embedded NULs and bounds the read to four bytes per character.
    rows = await connection.execute(
        FIND_VECTORS, {**key, "query": query, "limit": limit, "excerpt_bytes": 4 * EXCERPT_LIMIT}
    )
    hits = tuple(
        SearchHit(row.id, row.kind, row.excerpt.decode("utf-8", "ignore")[:EXCERPT_LIMIT])
        for row in rows
    )
    return SearchPage(hits, indexed)
