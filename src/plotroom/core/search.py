"""Follow committed ledger sequences and embed eligible search records off the writer."""

import asyncio
import logging
import math
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial

from plotroom.core.events import Event
from plotroom.core.ledger import Ledger
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.search import (
    MESSAGE_KIND,
    EmbeddedMessage,
    Embedder,
    EmbeddingError,
    EmbeddingIndex,
)

log = logging.getLogger("plotroom.search")
EMBEDDING_BATCH_SIZE = 32


async def committed_batches(
    ledger: Ledger, read_cursor: Callable[[], Awaitable[int]], *, limit: int
) -> AsyncIterator[tuple[int, list[Event]]]:
    """Yield batches above the caller's durable checkpoint until canceled."""
    while True:
        committed = ledger.commit_signal
        after = await read_cursor()
        events = await ledger.events_after(after, limit)
        if events:
            yield after, events
        else:
            await committed.wait()


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
