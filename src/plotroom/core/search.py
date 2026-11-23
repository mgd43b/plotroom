"""Backfill retained search records and follow committed messages for embedding."""

import asyncio
import logging
import math
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial

from plotroom.core.events import Event
from plotroom.core.ledger import Ledger
from plotroom.core.search_sources import EVENT_KINDS, event_record
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.search import (
    MESSAGE_KIND,
    BackfillPage,
    BackfillPosition,
    BackfillSource,
    EmbeddedMessage,
    Embedder,
    EmbeddingError,
    EmbeddingIndex,
    SearchBackfill,
)

log = logging.getLogger("plotroom.search")
EMBEDDING_BATCH_SIZE = 32
BACKFILL_BATCH_SIZE = 32


async def committed_batches(
    ledger: Ledger,
    read_cursor: Callable[[], Awaitable[int | None]],
    *,
    limit: int,
    through: int | None = None,
) -> AsyncIterator[tuple[int, list[Event]]]:
    """Follow commits, or yield one final empty page at a finite historical boundary."""
    while True:
        committed = ledger.commit_signal
        after = await read_cursor()
        if after is None:
            return
        events = await ledger.events_after(after, limit)
        if through is not None:
            events = [event for event in events if event.seq <= through]
        if events:
            yield after, events
        elif through is not None:
            yield after, []
            return
        else:
            await committed.wait()


async def backfill_search(
    ledger: Ledger, index: SearchBackfill, *, limit: int = BACKFILL_BATCH_SIZE
) -> None:
    """Backfill one project's retained sources in small, independently committed pages."""
    if index.project_id != ledger.project_id:
        raise ValueError("search backfill must belong to the current project")
    if limit < 1:
        raise ValueError("search backfill requires a positive page size")

    async def commit(
        source: BackfillSource, expected: BackfillPosition, page: BackfillPage
    ) -> None:
        await ledger.run(
            "system",
            partial(
                UnitOfWork.save_search_backfill,
                index=index,
                source=source,
                expected=expected,
                page=page,
            ),
        )

    async def event_cursor() -> int | None:
        position = await index.backfill_position(BackfillSource.EVENTS)
        if position is None:
            return None
        return int(position[0]) if position else 0

    through = await ledger.head_seq()
    while await event_cursor() is not None:
        async for after, events in committed_batches(
            ledger, event_cursor, limit=limit, through=through
        ):
            position = (after,) if after else ()
            page = BackfillPage(
                (events[-1].seq,) if events else None,
                tuple(event_record(event) for event in events if event.kind in EVENT_KINDS),
            )
            await commit(BackfillSource.EVENTS, position, page)

    for source in (
        BackfillSource.DECISIONS,
        BackfillSource.ASKS,
        BackfillSource.COMMENTS,
        BackfillSource.PULLS,
    ):
        while (position := await index.backfill_position(source)) is not None:
            page = await index.backfill_page(source, position, limit)
            await commit(source, position, page)


def pack_vector(vector: tuple[float, ...], dimension: int) -> bytes:
    try:
        if len(vector) != dimension or not all(math.isfinite(value) for value in vector):
            raise EmbeddingError("embedding vector has an invalid dimension or value")
        return struct.pack(f"<{dimension}f", *vector)
    except (OverflowError, struct.error) as exc:
        raise EmbeddingError("embedding vector has a value outside float32 range") from exc


async def follow_message_embeddings(
    ledger: Ledger, index: EmbeddingIndex, embedder: Embedder, *, retry_seconds: float = 5
) -> None:
    if index.project_id != ledger.project_id:
        raise ValueError("embedding index must belong to the current project")
    async for after, events in committed_batches(ledger, index.cursor, limit=EMBEDDING_BATCH_SIZE):
        messages = [event for event in events if event.kind == MESSAGE_KIND and event.body.strip()]
        records: tuple[EmbeddedMessage, ...] = ()
        if messages:
            try:
                vectors = await embedder.embed(tuple(event.body for event in messages))
                if len(vectors) != len(messages):
                    raise EmbeddingError("embedding batch has an invalid vector count")
                records = tuple(
                    EmbeddedMessage(event.id, pack_vector(vector, index.model.dimension))
                    for event, vector in zip(messages, vectors, strict=True)
                )
            except EmbeddingError:
                log.warning(
                    "embedding batch will retry",
                    extra={"project": ledger.project_id},
                )
                await asyncio.sleep(retry_seconds)
                continue

        await ledger.run(
            "system",
            partial(
                UnitOfWork.save_embedding_batch,
                index=index,
                expected=after,
                through=events[-1].seq,
                records=records,
            ),
        )
