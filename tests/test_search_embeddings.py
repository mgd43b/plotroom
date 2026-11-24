"""Fixture-only HTTP, writer and SQLite coverage for search embeddings."""

import asyncio
import json
import sqlite3
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import aiosqlite
import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from plotroom.adapters import embedding_http
from plotroom.adapters.embedding_http import HttpEmbedder
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.adapters.store_sqlite.store import retryable_search_write_error
from plotroom.app.config import ConfigError, EmbedderConfig, load_config
from plotroom.app.serve import Daemon
from plotroom.core import search
from plotroom.core.envelope import ok
from plotroom.core.events import LedgerError, NewEvent
from plotroom.core.human_queue import Decision
from plotroom.core.ledger import Ledger
from plotroom.core.search import (
    SemanticSearch,
    backfill_search,
    follow_search_embeddings,
    pack_vector,
)
from plotroom.core.snapshot import estimate_tokens, render_search
from plotroom.core.unit_of_work import UnitOfWork
from plotroom.ports.search import (
    DECISION_KIND,
    ISSUE_COMMENT_KIND,
    MESSAGE_KIND,
    BackfillSource,
    EmbeddedRecord,
    EmbeddingError,
    EmbeddingIndex,
    EmbeddingModel,
    EmbeddingReadiness,
    EmbeddingRejection,
    IndexedEmbedding,
    RejectedRecord,
    SearchHit,
    SearchPage,
)
from test_daemon import run_daemon, stop
from test_search import message, opened_search
from test_search_backfill import contend_after_checkpoint
from test_search_sources import COMMENT_ID, LATER, START, comment, project_comment

MODEL = EmbeddingModel("fixture-model", 2)


@dataclass
class Request:
    body: dict[str, Any]
    response: asyncio.Future[tuple[int, object, int | None]]

    def send(self, body: object, *, status: int = 200, retry_after: int | None = None) -> None:
        self.response.set_result((status, body, retry_after))

    def reject_input(self, *, retry_after: int | None = None) -> None:
        self.send({"error": {"code": "input_too_large"}}, status=413, retry_after=retry_after)

    def vectors(self, *vectors: list[float]) -> None:
        # Simulate a provider returning results out of order.
        self.send(
            {
                "data": [
                    {"index": index, "embedding": vectors[index]}
                    for index in reversed(range(len(vectors)))
                ],
            }
        )


@dataclass
class Endpoint:
    url: str
    requests: asyncio.Queue[Request]

    async def next_request(self, model: EmbeddingModel, *messages: str) -> Request:
        request = await asyncio.wait_for(self.requests.get(), 5)
        assert request.body == {
            "model": model.name,
            "dimensions": model.dimension,
            "input": list(messages),
        }
        return request


@pytest.fixture
async def endpoint() -> AsyncGenerator[Endpoint]:
    requests: asyncio.Queue[Request] = asyncio.Queue()
    tasks: set[asyncio.Task[None]] = set()

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        try:
            request = (await reader.readuntil(b"\r\n\r\n")).splitlines()
            assert request[0] == b"POST /embeddings HTTP/1.1"
            headers = dict(line.lower().split(b":", 1) for line in request[1:] if line)
            content = await reader.readexactly(int(headers[b"content-length"]))
            body = cast("dict[str, Any]", json.loads(content))
            response = asyncio.get_running_loop().create_future()
            requests.put_nowait(Request(body, response))
            status, data, retry_after = await response
            payload = data if isinstance(data, bytes) else json.dumps(data).encode()
            retry_header = f"Retry-After: {retry_after}\r\n" if retry_after is not None else ""
            writer.write(
                f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\n"
                f"{retry_header}Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    async with await asyncio.start_server(respond, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        try:
            yield Endpoint(f"http://127.0.0.1:{port}/embeddings", requests)
        finally:
            pending = list(tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


@asynccontextmanager
async def following(
    ledger: Ledger, index: EmbeddingIndex, endpoint: Endpoint
) -> AsyncGenerator[None]:
    async with httpx.AsyncClient(trust_env=False) as client:
        task = asyncio.create_task(
            follow_search_embeddings(
                ledger, index, HttpEmbedder(client, endpoint.url, index.model), retry_seconds=0.01
            )
        )
        try:
            yield
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def await_cursor(ledger: Ledger, index: EmbeddingIndex, through: int) -> None:
    async with asyncio.timeout(5):
        while True:
            committed = ledger.commit_signal
            if await index.cursor() == through:
                return
            await committed.wait()


async def test_stalled_or_failing_embedder_leaves_ledger_writable_and_catches_up(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        index = store.embedding_index(MODEL)
        first = (await ledger.append("system", message("a", "seal the hatch"))).event
        async with following(ledger, index, endpoint):
            stalled = await endpoint.next_request(MODEL, "seal the hatch")
            async with asyncio.timeout(3):
                second = (await ledger.append("system", message("b", "prepare supper"))).event
                await ledger.append("system", NewEvent("core", "core.tick", "tick", {}, "excluded"))
            assert await index.cursor() == 0
            stalled.send({}, status=503)
            retry = await endpoint.next_request(MODEL, "seal the hatch", "prepare supper")
            assert await index.cursor() == 0
            retry.vectors([1, 0], [0, 1])
            await await_cursor(ledger, index, 2)
            # The fake vector for "secure doorway" finds a record with no shared words.
            assert await ledger.search("secure doorway", None, 20) == SearchPage((), 0)
            paraphrase = (0.9, 0.1)
            assert await index.query_vector("demo", paraphrase, 1) == SearchPage(
                (SearchHit(first.id, first.kind, first.body),), 2
            )
            assert [hit.id for hit in (await index.query_vector("demo", paraphrase, 2)).hits] == [
                first.id,
                second.id,
            ]
            with pytest.raises(LedgerError, match="current project"):
                await index.query_vector("other", paraphrase, 20)


async def test_embedding_decode_runs_outside_the_daemon_event_loop(
    tmp_path: Path, endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoding = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    decode = HttpEmbedder._decode_batch  # pyright: ignore[reportPrivateUsage]

    def pause_decoder(
        self: HttpEmbedder, content: bytearray, size: int
    ) -> tuple[tuple[float, ...], ...]:
        loop.call_soon_threadsafe(decoding.set)
        assert release.wait(timeout=5)
        return decode(self, content, size)

    monkeypatch.setattr(HttpEmbedder, "_decode_batch", pause_decoder)
    async with opened_search(tmp_path, "demo") as (store, ledger):
        index = store.embedding_index(MODEL)
        await ledger.append("system", message("a", "first fixture"))
        async with following(ledger, index, endpoint):
            request = await endpoint.next_request(MODEL, "first fixture")
            request.vectors([1, 0])
            try:
                await asyncio.wait_for(decoding.wait(), timeout=3)
                async with asyncio.timeout(3):
                    await ledger.append("system", NewEvent("core", "core.tick", "tick", {}))
                assert await index.cursor() == 0
            finally:
                release.set()
            await await_cursor(ledger, index, 1)


async def test_restart_resumes_and_model_or_dimension_change_revisits_history(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    first_body = "one synthetic message"
    second_body = "another synthetic message"
    async with opened_search(tmp_path, "demo") as (store, ledger):
        first = (await ledger.append("system", message("a", first_body))).event
        index = store.embedding_index(MODEL)
        async with following(ledger, index, endpoint):
            request = await endpoint.next_request(MODEL, first_body)
            request.vectors([1, 0])
            await await_cursor(ledger, index, first.seq)

    async with opened_search(tmp_path, "demo") as (store, ledger):
        second = (await ledger.append("system", message("b", second_body))).event
        index = store.embedding_index(MODEL)
        assert await index.cursor() == first.seq
        async with following(ledger, index, endpoint):
            request = await endpoint.next_request(MODEL, second_body)
            request.vectors([0, 1])
            await await_cursor(ledger, index, second.seq)

        for changed in (EmbeddingModel("another-model", 2), EmbeddingModel(MODEL.name, 3)):
            new_index = store.embedding_index(changed)
            assert await new_index.cursor() == 0
            async with following(ledger, new_index, endpoint):
                request = await endpoint.next_request(changed, first_body, second_body)
                request.vectors([0.0] * changed.dimension, [1.0] + [0.0] * (changed.dimension - 1))
                await await_cursor(ledger, new_index, second.seq)
            result = await new_index.query_vector("demo", (0.0,) * changed.dimension, 20)
            assert result.total == 2
            assert [hit.id for hit in result.hits] == [first.id, second.id]

        result = await index.query_vector("demo", (0, 1), 20)
        assert result.total == 2
        assert [hit.id for hit in result.hits] == [second.id, first.id]


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [1],
        [float("nan"), 0],
        [1e100, 0],
        [True, 0],
        b'{"malformed":',
        b"[" * 2000 + b"]" * 2000,
        b'{"data":[{"index":0,"embedding":[1,0]},{"index":0,"embedding":[0,1]}]}',
    ],
)
async def test_invalid_http_batch_is_retried_without_writing_any_part(
    tmp_path: Path, endpoint: Endpoint, invalid: list[float] | bytes | None
) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        await ledger.append("system", message("a", "first"))
        last = await ledger.append("system", message("b", "second"))
        index = store.embedding_index(MODEL)
        async with following(ledger, index, endpoint):
            request = await endpoint.next_request(MODEL, "first", "second")
            if invalid is None:
                request.vectors([0, 1])
            elif isinstance(invalid, bytes):
                request.send(invalid)
            else:
                request.vectors([0, 1], invalid)
            retry = await endpoint.next_request(MODEL, "first", "second")
            assert await index.cursor() == 0
            assert await index.query_vector("demo", (0, 1), 20) == SearchPage((), 0)
            retry.vectors([0, 1], [1, 0])
            await await_cursor(ledger, index, last.event.seq)
            assert (await index.query_vector("demo", (0, 1), 20)).total == 2


async def test_oversized_loopback_response_is_retried(
    tmp_path: Path, endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embedding_http, "MAX_RESPONSE_BYTES", 128)
    async with opened_search(tmp_path, "demo") as (store, ledger):
        last = await ledger.append("system", message("a", "too much response"))
        index = store.embedding_index(MODEL)
        async with following(ledger, index, endpoint):
            request = await endpoint.next_request(MODEL, "too much response")
            request.send({"data": [{"index": 0, "embedding": [1, 0]}], "extra": " " * 200})
            retry = await endpoint.next_request(MODEL, "too much response")
            assert await index.cursor() == 0
            assert saved_rejections(store) == []
            retry.vectors([1, 0])
            await await_cursor(ledger, index, last.event.seq)


@pytest.mark.parametrize("too_large_status", [200, 413])
async def test_oversized_loopback_batch_splits_without_partial_index_commit(
    tmp_path: Path, endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch, too_large_status: int
) -> None:
    monkeypatch.setattr(embedding_http, "MAX_RESPONSE_BYTES", 128)
    first_body, second_body = "  first original\n", "second original"
    async with opened_search(tmp_path, "demo") as (store, ledger):
        first = (await ledger.append("system", message("a", first_body))).event
        second = (await ledger.append("system", message("b", second_body))).event
        index = store.embedding_index(MODEL)
        async with following(ledger, index, endpoint):
            combined = await endpoint.next_request(MODEL, first_body, second_body)
            combined.send(
                {
                    "data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 1]}],
                    "extra": " " * 200,
                },
                status=too_large_status,
            )
            left = await endpoint.next_request(MODEL, first_body)
            left.vectors([1, 0])
            right = await endpoint.next_request(MODEL, second_body)
            assert await index.cursor() == 0
            assert await index.query_vector("demo", (1, 0), 20) == SearchPage((), 0)
            right.vectors([0, 1])
            await await_cursor(ledger, index, second.seq)
            assert await index.query_vector("demo", (1, 0), 20) == SearchPage(
                (
                    SearchHit(first.id, first.kind, first_body),
                    SearchHit(second.id, second.kind, second_body),
                ),
                2,
            )


def saved_rejections(store: SqliteStore) -> list[tuple[str, int, int, str]]:
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute(
            "SELECT model, dimension, record_seq, reason FROM embedding_rejections "
            "ORDER BY model, dimension, record_seq"
        ).fetchall()


@pytest.mark.parametrize(
    ("status", "error", "retry_after"),
    [
        (400, {}, None),
        (401, {}, None),
        (413, {"error": {"code": "input_too_large"}}, 1),
        (413, {"error": {"code": "gateway_capacity"}}, None),
        (413, b'{"error":', None),
        (
            413,
            {
                "error": {"code": "input_too_large"},
                "extra": " " * embedding_http.MAX_REJECTION_BYTES,
            },
            None,
        ),
        (429, {}, None),
        (500, {}, None),
    ],
)
async def test_split_rejection_is_saved_only_after_later_inputs_succeed(
    tmp_path: Path, endpoint: Endpoint, status: int, error: object, retry_after: int | None
) -> None:
    oversized, accepted, later = "  oversized needle\n", "usable fixture", "new fixture"
    kinds = (MESSAGE_KIND,)
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        httpx.AsyncClient(trust_env=False) as client,
    ):
        first = (await ledger.append("system", message("a", oversized))).event
        second = (await ledger.append("system", message("b", accepted))).event
        await backfill_search(ledger, store)
        index = store.embedding_index(MODEL)
        semantic = SemanticSearch(index, HttpEmbedder(client, endpoint.url, MODEL))
        async with following(ledger, index, endpoint):
            for successful in (False, True):
                (await endpoint.next_request(MODEL, oversized, accepted)).send({}, status=413)
                (await endpoint.next_request(MODEL, oversized)).reject_input()
                right = await endpoint.next_request(MODEL, accepted)
                assert await index.cursor() == 0
                assert saved_rejections(store) == []
                assert (await index.query_vector("demo", (1, 0), 10)).total == 0
                if successful:
                    right.vectors([1, 0])
                else:
                    right.send(error, status=status, retry_after=retry_after)
            await await_cursor(ledger, index, 2)
            assert saved_rejections(store) == [(MODEL.name, MODEL.dimension, 1, "input_too_large")]
            assert await index.preflight(kinds) is EmbeddingReadiness.READY
            assert (
                await index.preflight((DECISION_KIND,), "oversized needle")
                is EmbeddingReadiness.EMPTY
            )
            assert (
                await store.embedding_index(EmbeddingModel(MODEL.name, 3)).preflight(
                    kinds, "oversized needle"
                )
                is EmbeddingReadiness.PENDING
            )
            lexical = await semantic.query(store, "demo", "oversized needle", kinds, 10)
            assert lexical == SearchPage(
                (SearchHit(first.id, first.kind, oversized),), 1, "lexical", "embedding_rejected"
            )
            assert endpoint.requests.empty()

            result = asyncio.create_task(semantic.query(store, "demo", "paraphrase", kinds, 10))
            (await endpoint.next_request(MODEL, "paraphrase")).vectors([1, 0])
            assert await result == SearchPage(
                (SearchHit(second.id, second.kind, accepted),), 1, "semantic"
            )

    async with opened_search(tmp_path, "demo") as (store, ledger):
        index = store.embedding_index(MODEL)
        assert await index.cursor() == 2
        assert saved_rejections(store) == [(MODEL.name, MODEL.dimension, 1, "input_too_large")]
        assert [
            (revision.seq, revision.record) for revision in await index.revisions_after(0, 10)
        ] == [
            (1, None),
            (2, None),
        ]
        await ledger.append("system", message("c", later))
        async with following(ledger, index, endpoint):
            (await endpoint.next_request(MODEL, later)).vectors([0, 1])
            await await_cursor(ledger, index, 3)
            assert (await index.query_vector("demo", (0, 1), 10)).total == 2


async def test_bare_413_batch_and_singleton_retry_until_gateway_recovers(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        await ledger.append("system", message("a", "first"))
        await ledger.append("system", message("b", "second"))
        index = store.embedding_index(MODEL)
        async with following(ledger, index, endpoint):
            (await endpoint.next_request(MODEL, "first", "second")).send(b"", status=413)
            (await endpoint.next_request(MODEL, "first")).send(b"", status=413)
            retry = await endpoint.next_request(MODEL, "first", "second")
            assert await index.cursor() == 0
            assert saved_rejections(store) == []
            retry.send(b"", status=413)
            (await endpoint.next_request(MODEL, "first")).vectors([1, 0])
            (await endpoint.next_request(MODEL, "second")).vectors([0, 1])
            await await_cursor(ledger, index, 2)
            assert saved_rejections(store) == []
            assert (await index.query_vector("demo", (1, 0), 10)).total == 2


async def test_new_model_reindexes_rejected_record_and_query_rejection_stays_lexical(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    alternate = EmbeddingModel("alternate-model", MODEL.dimension)
    body = "retained observatory"
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        httpx.AsyncClient(trust_env=False) as client,
    ):
        event = (await ledger.append("system", message("a", body))).event
        await backfill_search(ledger, store)
        original = store.embedding_index(MODEL)
        async with following(ledger, original, endpoint):
            (await endpoint.next_request(MODEL, body)).reject_input()
            await await_cursor(ledger, original, 1)
        replacement = store.embedding_index(alternate)
        async with following(ledger, replacement, endpoint):
            (await endpoint.next_request(alternate, body)).vectors([1, 0])
            await await_cursor(ledger, replacement, 1)
        hit = SearchHit(event.id, event.kind, body)
        assert await replacement.query_vector("demo", (1, 0), 10) == SearchPage((hit,), 1)
        assert await original.query_vector("demo", (1, 0), 10) == SearchPage((), 0)
        original_rejection = [(MODEL.name, MODEL.dimension, 1, "input_too_large")]
        assert saved_rejections(store) == original_rejection

        semantic = SemanticSearch(replacement, HttpEmbedder(client, endpoint.url, alternate))
        result = asyncio.create_task(
            semantic.query(store, "demo", "observatory", (MESSAGE_KIND,), 10)
        )
        (await endpoint.next_request(alternate, "observatory")).reject_input()
        assert await result == SearchPage((hit,), 1, "lexical", "query_rejected")
        assert await replacement.cursor() == 1
        assert saved_rejections(store) == original_rejection


async def test_rejected_revision_during_query_uses_final_lexical_snapshot(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        httpx.AsyncClient(trust_env=False) as client,
    ):
        index = store.embedding_index(MODEL)
        semantic = SemanticSearch(index, HttpEmbedder(client, endpoint.url, MODEL))
        kinds = (ISSUE_COMMENT_KIND,)

        async def save(body: str, updated: str) -> None:
            await ledger.run("system", lambda uow: project_comment(uow, comment(body, updated)))

        await save("original needle", START)
        await backfill_search(ledger, store)
        async with following(ledger, index, endpoint):
            (await endpoint.next_request(MODEL, "original needle")).vectors([1, 0])
            await await_cursor(ledger, index, 1)
            query = asyncio.create_task(semantic.query(store, "demo", "needle", kinds, 10))
            query_request = await endpoint.next_request(MODEL, "needle")
            await save("edited needle", LATER)
            (await endpoint.next_request(MODEL, "edited needle")).reject_input()
            await await_cursor(ledger, index, 2)
            assert await index.preflight(kinds, "different phrase") is EmbeddingReadiness.EMPTY
            query_request.vectors([1, 0])
            assert await query == SearchPage(
                (SearchHit(COMMENT_ID, ISSUE_COMMENT_KIND, "edited needle"),),
                1,
                "lexical",
                "embedding_rejected",
            )
            await save("latest needle", "2026-01-01T00:00:03Z")
            latest = await endpoint.next_request(MODEL, "latest needle")
            assert saved_rejections(store) == []
            latest.vectors([0, 1])
            await await_cursor(ledger, index, 3)
            assert (await index.query_vector("demo", (0, 1), 10)).hits == (
                SearchHit(COMMENT_ID, ISSUE_COMMENT_KIND, "latest needle"),
            )


async def test_invalid_url_from_embedding_client_is_a_typed_error() -> None:
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(EmbeddingError, match="endpoint request failed"):
            await HttpEmbedder(client, "http://☃.test/embeddings", MODEL).embed(("synthetic",))


def test_vector_converts_large_integer_overflow_to_a_typed_error() -> None:
    with pytest.raises(EmbeddingError, match="outside float32 range"):
        pack_vector((10**1000, 0), MODEL.dimension)


async def test_cursor_passes_an_ineligible_page_before_requesting_messages(
    tmp_path: Path, endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search, "EMBEDDING_BATCH_SIZE", 1)
    async with opened_search(tmp_path, "demo") as (store, ledger):
        await ledger.append("system", message("blank", " \u2003\n"))
        await ledger.append("system", NewEvent("core", "core.tick", "tick"))
        await ledger.append("system", message("a", "only eligible message"))
        index = store.embedding_index(MODEL)
        async with following(ledger, index, endpoint):
            request = await endpoint.next_request(MODEL, "only eligible message")
            assert await index.cursor() == 1
            request.vectors([1, 0])
            await await_cursor(ledger, index, 2)


async def test_retry_log_omits_provider_error_details(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    retrying = asyncio.Event()

    class FixtureEmbedder:
        async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            retrying.set()
            raise EmbeddingError("synthetic provider response text")

    async with opened_search(tmp_path, "demo") as (store, ledger):
        await ledger.append("system", message("a", "first"))
        task = asyncio.create_task(
            follow_search_embeddings(ledger, store.embedding_index(MODEL), FixtureEmbedder())
        )
        try:
            await asyncio.wait_for(retrying.wait(), 5)
            (record,) = [r for r in caplog.records if r.message == "embedding batch will retry"]
            assert "synthetic provider response text" not in str(record.__dict__)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_vector_writes_rollback_together_and_reject_stale_or_foreign_attempts(
    tmp_path: Path,
) -> None:
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        opened_search(tmp_path, "other") as (other, other_ledger),
    ):
        first = (await ledger.append("system", message("a", "first"))).event
        nonmessage = (await ledger.append("system", NewEvent("core", "core.tick", "tick"))).event
        last = (await ledger.append("system", message("b", "last"))).event
        foreign = (await other_ledger.append("system", message("a", "other"))).event
        index = store.embedding_index(MODEL)
        blob = pack_vector((1, 0), MODEL.dimension)
        record = EmbeddedRecord(1, first.kind, first.id, blob)
        foreign_record = EmbeddedRecord(2, foreign.kind, foreign.id, blob)

        async def invalid_batch(uow: UnitOfWork) -> bool:
            return await uow.save_embedding_batch(index, 0, 2, (record, foreign_record))

        with pytest.raises(ValueError, match="revisions within the batch interval"):
            await ledger.run("system", invalid_batch)
        assert await index.cursor() == 0
        assert await index.query_vector("demo", (1, 0), 20) == SearchPage((), 0)
        with pytest.raises(RuntimeError, match="another project's transaction"):
            await other_ledger.run("system", invalid_batch)

        async def save(expected: int, through: int, records: tuple[IndexedEmbedding, ...]) -> bool:
            return await ledger.run(
                "system", lambda uow: uow.save_embedding_batch(index, expected, through, records)
            )

        with pytest.raises(ValueError, match="end within committed search revisions"):
            await save(0, 3, (record,))
        with pytest.raises(ValueError, match="revisions within the batch interval"):
            await save(0, 2, (record, EmbeddedRecord(2, nonmessage.kind, nonmessage.id, blob)))
        with pytest.raises(ValueError, match="revisions within the batch interval"):
            await save(0, 1, (record, EmbeddedRecord(2, last.kind, last.id, blob)))
        with pytest.raises(IntegrityError, match="ck_search_vector_length"):
            await save(0, 2, (record, EmbeddedRecord(2, last.kind, last.id, b"short")))
        assert await index.cursor() == 0
        assert await index.query_vector("demo", (1, 0), 20) == SearchPage((), 0)

        async def valid_batch(uow: UnitOfWork) -> bool:
            return await uow.save_embedding_batch(index, 0, first.seq, (record,))

        async def aborted_batch(uow: UnitOfWork) -> None:
            rejection = RejectedRecord(2, last.kind, last.id, EmbeddingRejection.INPUT_TOO_LARGE)
            assert await uow.save_embedding_batch(index, 0, 2, (record, rejection))
            raise ValueError("fixture rollback after cursor write")

        with pytest.raises(ValueError, match="fixture rollback"):
            await ledger.run("system", aborted_batch)
        assert await index.cursor() == 0
        assert saved_rejections(store) == []
        assert await index.query_vector("demo", (1, 0), 20) == SearchPage((), 0)
        assert await ledger.run("system", valid_batch)
        assert not await ledger.run("system", invalid_batch)
        with pytest.raises(ValueError, match="revisions within the batch interval"):
            await save(first.seq, 2, (record,))
        assert await index.cursor() == first.seq
        assert (await index.query_vector("demo", (1, 0), 20)).total == 1
        assert await other.embedding_index(MODEL).query_vector("other", (1, 0), 20) == SearchPage(
            (), 0
        )


@pytest.mark.parametrize("exhaust", [False, True])
async def test_embedding_write_contention_replays_only_the_writer_submission(
    tmp_path: Path,
    endpoint: Endpoint,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    exhaust: bool,
) -> None:
    caplog.set_level("INFO", logger="plotroom.search")
    monkeypatch.setattr(search, "_WRITE_RETRY_DELAYS", (0.0,) * 3)
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        httpx.AsyncClient(trust_env=False) as client,
    ):
        index = store.embedding_index(MODEL)
        await ledger.append("system", message("a", "first"))
        embedder = HttpEmbedder(client, endpoint.url, MODEL)
        task = asyncio.create_task(
            follow_search_embeddings(
                ledger, index, embedder, retryable_write_error=retryable_search_write_error
            )
        )
        try:
            (await endpoint.next_request(MODEL, "first")).vectors([1, 0])
            await await_cursor(ledger, index, 1)

            async def append_batch(uow: UnitOfWork) -> None:
                await uow.append(message("b", "second"))
                await uow.append(message("c", "third"))

            durable: list[tuple[int, list[int]]] = []

            def observe() -> bool:
                with closing(sqlite3.connect(store.path)) as db:
                    (position,) = db.execute("SELECT index_seq FROM embedding_cursors").fetchone()
                    vectors = [
                        seq
                        for (seq,) in db.execute(
                            "SELECT record_seq FROM search_vectors ORDER BY record_seq"
                        )
                    ]
                durable.append((position, vectors))
                return exhaust or len(durable) == 1

            with contend_after_checkpoint(
                store,
                tmp_path / "contended.db",
                lambda sql, _: "INSERT INTO embedding_cursors (model, dimension, index_seq)" in sql,
                observe,
                at_commit=True,
            ):
                await ledger.run("system", append_batch)
                (await endpoint.next_request(MODEL, "second", "third")).vectors([0, 1], [1, 1])
                if exhaust:
                    await asyncio.wait_for(task, 5)
                else:
                    await await_cursor(ledger, index, 3)
            assert endpoint.requests.empty()
            assert durable == [(1, [1])] * (4 if exhaust else 2)
            assert await index.cursor() == (1 if exhaust else 3)
            assert (await index.query_vector("demo", (1, 0), 20)).total == (1 if exhaust else 3)
            assert (await ledger.search("second", None, 20)).total == 1
            logs = [record for record in caplog.records if record.name == "plotroom.search"]
            assert logs[-1].__dict__["project"] == "demo"
            assert logs[-1].__dict__["source"] == "embeddings"
            assert logs[-1].__dict__["attempt"] == (4 if exhaust else 2)
            assert logs[-1].__dict__["failure_code"] == "sqlite_busy"
            assert logs[-1].getMessage() == (
                "search write stopped" if exhaust else "search write recovered"
            )
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


async def test_vector_excerpt_keeps_nul_and_caps_unicode_characters(tmp_path: Path) -> None:
    async with opened_search(tmp_path, "demo") as (store, ledger):
        body = "é\x00" + "😀" * 300
        event = (await ledger.append("system", message("a", body))).event
        index = store.embedding_index(MODEL)
        vector = (1.0, 0.0)
        record = EmbeddedRecord(
            event.seq, event.kind, event.id, pack_vector(vector, MODEL.dimension)
        )
        await ledger.run(
            "system", lambda uow: uow.save_embedding_batch(index, 0, event.seq, (record,))
        )
        assert await index.query_vector("demo", vector, 1) == SearchPage(
            (SearchHit(event.id, event.kind, body[:240]),), 1
        )


async def test_unavailable_vector_extension_preserves_lexical_search(
    tmp_path: Path, endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(_connection: aiosqlite.Connection, _path: str) -> None:
        raise sqlite3.OperationalError("fixture extension not installed")

    monkeypatch.setattr(aiosqlite.Connection, "load_extension", unavailable)
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        httpx.AsyncClient(trust_env=False) as client,
    ):
        event = (await ledger.append("system", message("a", "fixture needle"))).event
        index = store.embedding_index(MODEL)
        with pytest.raises(LedgerError, match="requires sqlite-vec") as error:
            await index.query_vector("demo", (1, 0), 1)
        assert error.value.code == "unavailable"
        await backfill_search(ledger, store)
        async with following(ledger, index, endpoint):
            (await endpoint.next_request(MODEL, "fixture needle")).vectors([1, 0])
            await await_cursor(ledger, index, 1)
            semantic = SemanticSearch(index, HttpEmbedder(client, endpoint.url, MODEL))
            result = asyncio.create_task(
                semantic.query(store, "demo", "needle", (MESSAGE_KIND,), 20)
            )
            (await endpoint.next_request(MODEL, "needle")).vectors([1, 0])
            assert await result == SearchPage(
                (SearchHit(event.id, event.kind, event.body),), 1, "lexical", "vector_unavailable"
            )


async def test_revisions_edited_during_embedding_and_query_cannot_return_stale_vectors(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    async with (
        opened_search(tmp_path, "demo") as (store, ledger),
        httpx.AsyncClient(trust_env=False) as client,
    ):
        index = store.embedding_index(MODEL)
        semantic = SemanticSearch(index, HttpEmbedder(client, endpoint.url, MODEL))
        kinds = (ISSUE_COMMENT_KIND,)

        async def save(body: str, updated: str) -> None:
            await ledger.run("system", lambda uow: project_comment(uow, comment(body, updated)))

        await save("original needle", START)
        assert (await semantic.query(store, "demo", "needle", kinds, 5)).fallback_reason == (
            "index_pending"
        )
        assert endpoint.requests.empty()
        await backfill_search(ledger, store)
        async with following(ledger, index, endpoint):
            original = await endpoint.next_request(MODEL, "original needle")
            await save("edited needle", LATER)
            original.vectors([1, 0])
            edited = await endpoint.next_request(MODEL, "edited needle")
            assert await index.cursor() == 1
            assert (await index.query_vector("demo", (1, 0), 5)).total == 0
            assert await semantic.query(store, "demo", "needle", kinds, 5) == SearchPage(
                (SearchHit(COMMENT_ID, ISSUE_COMMENT_KIND, "edited needle"),),
                1,
                "lexical",
                "index_pending",
            )
            edited.vectors([0, 1])
            await await_cursor(ledger, index, 2)

            result = asyncio.create_task(semantic.query(store, "demo", "needle", kinds, 5))
            request = await endpoint.next_request(MODEL, "needle")
            await save("latest needle", "2026-01-01T00:00:03Z")
            latest = await endpoint.next_request(MODEL, "latest needle")
            request.vectors([0, 1])
            assert await result == SearchPage(
                (SearchHit(COMMENT_ID, ISSUE_COMMENT_KIND, "latest needle"),),
                1,
                "lexical",
                "index_pending",
            )
            assert (await index.query_vector("demo", (0, 1), 5)).total == 0
            latest.vectors([0.5, 0.5])
            await await_cursor(ledger, index, 3)
            assert (await index.query_vector("demo", (0, 1), 5)).hits == (
                SearchHit(COMMENT_ID, ISSUE_COMMENT_KIND, "latest needle"),
            )


async def test_daemon_starts_embedding_only_for_configured_project(
    tmp_path: Path, endpoint: Endpoint
) -> None:
    path = tmp_path / "plotroom.toml"
    path.write_text(
        f"""
        data_dir = "data"
        port = 0
        [projects.disabled]
        [projects.enabled.embedder]
        url = "{endpoint.url}"
        model = "{MODEL.name}"
        dimension = {MODEL.dimension}
    """,
        encoding="utf-8",
    )
    config = load_config(path, {})
    assert config.embedders == {"enabled": EmbedderConfig(endpoint.url, MODEL)}
    daemon = Daemon(config)
    task, _ = await run_daemon(daemon)
    try:
        disabled = daemon.ledgers["disabled"]
        enabled = daemon.ledgers["enabled"]
        await disabled.append("system", message("a", "disabled needle"))
        public = (await enabled.append("system", message("a", "enabled fixture"))).event
        request = await endpoint.next_request(MODEL, "enabled fixture")
        request.vectors([1, 0])
        reader = SqliteStore("enabled", config.data_dir)
        try:
            index = reader.embedding_index(MODEL)
            await await_cursor(enabled, index, 1)
            decision = Decision("dec_fixture", "human", "  seal the hatch\n", {}, [], [], START)
            await enabled.run("system", lambda uow: uow.insert_decision(decision))
            pending = await endpoint.next_request(MODEL, decision.exact_words)
            async with asyncio.timeout(5):
                while True:
                    committed = enabled.commit_signal
                    positions = [
                        await reader.backfill_position(source) for source in BackfillSource
                    ]
                    if all(position is None for position in positions):
                        break
                    await committed.wait()

            assert await index.preflight((MESSAGE_KIND,)) is EmbeddingReadiness.READY
            assert await index.preflight((DECISION_KIND,)) is EmbeddingReadiness.PENDING
            assert await index.preflight((ISSUE_COMMENT_KIND,)) is EmbeddingReadiness.EMPTY
            worker = asyncio.create_task(
                enabled.search("find the phrase", None, 5, role="worker:fixture")
            )
            (await endpoint.next_request(MODEL, "find the phrase")).vectors([1, 0])
            assert await worker == SearchPage(
                (SearchHit(public.id, public.kind, public.body),), 1, "semantic"
            )
            assert (
                await enabled.search("hatch", None, 5, role="orchestrator")
            ).fallback_reason == ("index_pending")
            assert await enabled.search(
                "hatch", (DECISION_KIND,), 5, role="worker:fixture"
            ) == SearchPage((), 0, "semantic")

            pending.vectors([0, 1])
            await await_cursor(enabled, index, 2)
            task_result = asyncio.create_task(
                enabled.search("secure doorway", None, 1, role="orchestrator")
            )
            (await endpoint.next_request(MODEL, "secure doorway")).vectors([0, 1])
            result = await task_result
            assert result == SearchPage(
                (SearchHit(decision.decision_id, DECISION_KIND, decision.exact_words),),
                2,
                "semantic",
            )
            assert render_search(result, 100)["omitted"] == 1

            for limit in (1, 5):
                worker = asyncio.create_task(
                    enabled.search("secure doorway", None, limit, role="worker:fixture")
                )
                (await endpoint.next_request(MODEL, "secure doorway")).vectors([0, 1])
                assert await worker == SearchPage(
                    (SearchHit(public.id, public.kind, public.body),), 1, "semantic"
                )

            for invalid in (None, {"data": [{"index": 0, "embedding": [1e100, 1]}]}):
                fallback = asyncio.create_task(
                    enabled.search("hatch", (DECISION_KIND,), 5, role="orchestrator")
                )
                request = await endpoint.next_request(MODEL, "hatch")
                request.send(invalid, status=503 if invalid is None else 200)
                page = await fallback
                assert page.total == 1
                assert page.hits[0].id == decision.decision_id
                assert page.mode == "lexical"
                assert page.fallback_reason == "provider_unavailable"
                response = render_search(page, 42)
                assert response == {
                    "hits": [],
                    "omitted": 1,
                    "mode": "lexical",
                    "fallback_reason": "provider_unavailable",
                }
                assert estimate_tokens(json.dumps(ok(response), indent=2)) <= 42
        finally:
            await reader.close()
        disabled_result = await disabled.search("needle", None, 20)
        assert disabled_result.total == 1
        assert set(render_search(disabled_result, 100)) == {"hits", "omitted"}
    finally:
        await stop(daemon, task)
    async with opened_search(config.data_dir, "disabled") as (store, _):
        assert await store.embedding_index(MODEL).cursor() == 0
        assert endpoint.requests.empty()


@pytest.mark.parametrize(
    ("settings", "field"),
    [
        ('model = "m"\ndimension = 2', "url"),
        ('url = "file:///tmp/embedding"\nmodel = "m"\ndimension = 2', "url"),
        ('url = "http://☃.test/embeddings"\nmodel = "m"\ndimension = 2', "url"),
        ('url = "http://xn--a.test/embeddings"\nmodel = "m"\ndimension = 2', "url"),
        ('url = "http://127.0.0.1/embedding"\ndimension = 2', "model"),
        ('url = "http://127.0.0.1/embedding"\nmodel = "m"\ndimension = false', "dimension"),
    ],
)
def test_config_requires_a_complete_embedder_table(
    tmp_path: Path, settings: str, field: str
) -> None:
    path = tmp_path / "plotroom.toml"
    path.write_text(f"[projects.demo.embedder]\n{settings}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=rf"projects\.demo\.embedder\.{field}"):
        load_config(path, {})
