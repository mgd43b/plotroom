"""Backfill, embed and query committed search records."""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from plotroom.core.events import LedgerError
from plotroom.core.search_sources import EVENT_KINDS, event_record
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.search import (
    BackfillPage,
    BackfillPosition,
    BackfillSource,
    EmbeddedRecord,
    Embedder,
    EmbeddingError,
    EmbeddingIndex,
    EmbeddingPending,
    EmbeddingReadiness,
    EmbeddingRejection,
    IndexedEmbedding,
    RejectedRecord,
    Search,
    SearchBackfill,
    SearchPage,
)

if TYPE_CHECKING:
    from plotroom.core.ledger import Ledger

log = logging.getLogger("plotroom.search")
EMBEDDING_BATCH_SIZE = 32
BACKFILL_BATCH_SIZE = 32
_WRITE_RETRY_DELAYS = (0.1, 0.5, 1.0)
type WriteErrorClassifier = Callable[[Exception], str | None]


async def _commit_search_write(
    ledger: Ledger,
    source: str,
    work: Callable[[UnitOfWork], Awaitable[bool]],
    retryable_write_error: WriteErrorClassifier | None,
) -> bool:
    """Retry a whole cursor-checked write outside the writer task."""
    if retryable_write_error is None:
        await ledger.run("system", work)
        return True

    failure_code = None
    for attempt, delay in enumerate((*_WRITE_RETRY_DELAYS, None), start=1):
        fields = {"project": ledger.project_id, "source": source, "attempt": attempt}
        try:
            await ledger.run("system", work)
        except Exception as error:
            failure_code = retryable_write_error(error)
            if failure_code is None or delay is None:
                log.error(
                    "search write stopped",
                    extra={**fields, "failure_code": failure_code or "search_write_failed"},
                )
                return False
            log.warning("search write will retry", extra={**fields, "failure_code": failure_code})
            await asyncio.sleep(delay)
        else:
            if failure_code is not None:
                log.info("search write recovered", extra={**fields, "failure_code": failure_code})
            return True
    raise AssertionError("search write retry schedule must terminate")


class _Sequenced(Protocol):
    @property
    def seq(self) -> int: ...


async def committed_batches[T: _Sequenced](
    ledger: Ledger,
    read_cursor: Callable[[], Awaitable[int | None]],
    read_page: Callable[[int, int], Awaitable[list[T]]],
    *,
    limit: int,
    through: int | None = None,
) -> AsyncIterator[tuple[int, list[T]]]:
    """Follow commits, or yield one final empty page at a finite historical boundary."""
    while True:
        committed = ledger.commit_signal
        after = await read_cursor()
        if after is None:
            return
        records = await read_page(after, limit)
        if through is not None:
            records = [record for record in records if record.seq <= through]
        if records:
            yield after, records
        elif through is not None:
            yield after, []
            return
        else:
            await committed.wait()


async def backfill_search(
    ledger: Ledger,
    index: SearchBackfill,
    *,
    limit: int = BACKFILL_BATCH_SIZE,
    retryable_write_error: WriteErrorClassifier | None = None,
) -> None:
    """Backfill one project's retained sources in small, independently committed pages."""
    if index.project_id != ledger.project_id:
        raise ValueError("search backfill must belong to the current project")
    if limit < 1:
        raise ValueError("search backfill requires a positive page size")

    async def commit(
        source: BackfillSource, expected: BackfillPosition, page: BackfillPage
    ) -> bool:
        return await _commit_search_write(
            ledger,
            f"backfill.{source}",
            partial(
                UnitOfWork.save_search_backfill,
                index=index,
                source=source,
                expected=expected,
                page=page,
            ),
            retryable_write_error,
        )

    async def event_cursor() -> int | None:
        position = await index.backfill_position(BackfillSource.EVENTS)
        if position is None:
            return None
        return int(position[0]) if position else 0

    through = await ledger.head_seq()
    while await event_cursor() is not None:
        async for after, events in committed_batches(
            ledger, event_cursor, ledger.events_after, limit=limit, through=through
        ):
            position = (after,) if after else ()
            page = BackfillPage(
                (events[-1].seq,) if events else None,
                tuple(event_record(event) for event in events if event.kind in EVENT_KINDS),
            )
            if not await commit(BackfillSource.EVENTS, position, page):
                return

    for source in (
        BackfillSource.DECISIONS,
        BackfillSource.ASKS,
        BackfillSource.COMMENTS,
        BackfillSource.PULLS,
    ):
        while (position := await index.backfill_position(source)) is not None:
            page = await index.backfill_page(source, position, limit)
            if not await commit(source, position, page):
                return


def pack_vector(vector: tuple[float, ...], dimension: int) -> bytes:
    try:
        if len(vector) != dimension or not all(math.isfinite(value) for value in vector):
            raise EmbeddingError("embedding vector has an invalid dimension or value")
        return struct.pack(f"<{dimension}f", *vector)
    except (OverflowError, struct.error) as exc:
        raise EmbeddingError("embedding vector has a value outside float32 range") from exc


async def follow_search_embeddings(
    ledger: Ledger,
    index: EmbeddingIndex,
    embedder: Embedder,
    *,
    retry_seconds: float = 5,
    retryable_write_error: WriteErrorClassifier | None = None,
) -> None:
    if index.project_id != ledger.project_id:
        raise ValueError("embedding index must belong to the current project")
    async for after, revisions in committed_batches(
        ledger, index.cursor, index.revisions_after, limit=EMBEDDING_BATCH_SIZE
    ):
        pending = [(item.seq, item.record) for item in revisions if item.record is not None]
        records: tuple[IndexedEmbedding, ...] = ()
        if pending:
            try:
                outcomes = await embedder.embed(tuple(record.body for _, record in pending))
                if len(outcomes) != len(pending):
                    raise EmbeddingError("embedding batch has an invalid outcome count")
                records = tuple(
                    RejectedRecord(seq, record.kind, record.id, outcome)
                    if isinstance(outcome, EmbeddingRejection)
                    else EmbeddedRecord(
                        seq, record.kind, record.id, pack_vector(outcome, index.model.dimension)
                    )
                    for (seq, record), outcome in zip(pending, outcomes, strict=True)
                )
            except EmbeddingError:
                log.warning(
                    "embedding batch will retry",
                    extra={"project": ledger.project_id},
                )
                await asyncio.sleep(retry_seconds)
                continue

        if not await _commit_search_write(
            ledger,
            "embeddings",
            partial(
                UnitOfWork.save_embedding_batch,
                index=index,
                expected=after,
                through=revisions[-1].seq,
                records=records,
            ),
            retryable_write_error,
        ):
            return


@dataclass(frozen=True)
class SemanticSearch:
    index: EmbeddingIndex
    embedder: Embedder

    async def query(
        self, lexical: Search, project: str, text: str, kinds: tuple[str, ...], limit: int
    ) -> SearchPage:
        try:
            readiness = await self.index.preflight(kinds, text)
            if readiness is EmbeddingReadiness.REJECTED_MATCH:
                page = await lexical.query(project, text, kinds, limit)
                return replace(page, mode="lexical", fallback_reason="embedding_rejected")
            if readiness is EmbeddingReadiness.PENDING:
                raise EmbeddingPending
            if readiness is EmbeddingReadiness.EMPTY:
                return SearchPage((), 0, mode="semantic")
            outcomes = await self.embedder.embed((text,))
            if len(outcomes) != 1:
                raise EmbeddingError("search query requires exactly one embedding")
            vector = outcomes[0]
            if isinstance(vector, EmbeddingRejection):
                page = await lexical.query(project, text, kinds, limit)
                return replace(page, mode="lexical", fallback_reason="query_rejected")
            # Keep malformed provider values in the provider failure path.
            pack_vector(vector, self.index.model.dimension)
            page = await self.index.query_vector(project, vector, limit, kinds=kinds, text=text)
            return replace(page, mode="semantic") if page.mode is None else page
        except EmbeddingPending:
            reason = "index_pending"
        except EmbeddingError:
            reason = "provider_unavailable"
        except LedgerError as exc:
            if exc.code != "unavailable":
                raise
            reason = "vector_unavailable"
        page = await lexical.query(project, text, kinds, limit)
        return replace(page, mode="lexical", fallback_reason=reason)
