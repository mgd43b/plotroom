"""Verify the daemon's registered search input recovery through authenticated loopback MCP."""

import asyncio
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import authenticated_client, tool_json
from plotroom.adapters.store_sqlite import SqliteStore
from plotroom.app import serve
from plotroom.app.config import EmbedderConfig
from plotroom.core.human_queue import Decision
from plotroom.ports.search import DECISION_KIND, BackfillSource
from test_daemon import bearer, make_config, run_daemon, stop
from test_search_backfill import contend_after_checkpoint
from test_search_embeddings import MODEL, Endpoint, await_cursor
from test_search_embeddings import endpoint as endpoint
from test_search_sources import START


async def test_daemon_retries_backfill_and_embedding_writes_in_process(
    tmp_path: Path,
    endpoint: Endpoint,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level("INFO", logger="plotroom.search")
    config = replace(make_config(tmp_path), embedders={"demo": EmbedderConfig(endpoint.url, MODEL)})
    config.data_dir.mkdir()
    token = bearer(config)
    store = SqliteStore("demo", config.data_dir)
    exact_words = "seal the hatch"
    decision = Decision("dec_historical", "human", exact_words, {}, [], [], START)
    empty_checkpoints: dict[str, list[tuple[int, int]]] = {"backfill": [], "embeddings": []}

    def observe(source: str, checkpoint_query: str, records_table: str) -> bool:
        with closing(sqlite3.connect(store.path)) as connection:
            (checkpoint_count,) = connection.execute(checkpoint_query).fetchone()
            (record_count,) = connection.execute(f"SELECT count(*) FROM {records_table}").fetchone()
        attempts = empty_checkpoints[source]
        attempts.append((checkpoint_count, record_count))
        return len(attempts) == 1

    try:
        await store.migrate()
        async with store.transaction() as tx:
            # Retained projection without a search row, as encountered by historical backfill.
            await tx.insert_decision(decision)

        def daemon_store(project_id: str, data_dir: Path) -> SqliteStore:
            assert project_id == store.project_id
            assert data_dir == config.data_dir
            return store

        monkeypatch.setattr(serve, "SqliteStore", daemon_store)
        with (
            contend_after_checkpoint(
                store,
                tmp_path / "busy.db",
                lambda sql, params: (
                    "INSERT INTO project_meta (key, value)" in sql
                    and params == ("search.backfill.decisions", '["dec_historical"]')
                ),
                lambda: observe(
                    "backfill",
                    "SELECT count(*) FROM project_meta WHERE key = 'search.backfill.decisions'",
                    "search_records",
                ),
            ),
            contend_after_checkpoint(
                store,
                tmp_path / "full.db",
                lambda sql, _: "INSERT INTO embedding_cursors (model, dimension, index_seq)" in sql,
                lambda: observe(
                    "embeddings", "SELECT count(*) FROM embedding_cursors", "search_vectors"
                ),
                at_commit=True,
                full=True,
            ),
        ):
            daemon = serve.Daemon(config)
            task, port = await run_daemon(daemon)
            try:
                async with authenticated_client(f"http://127.0.0.1:{port}/mcp", token) as client:
                    (await endpoint.next_request(MODEL, exact_words)).vectors([1, 0])
                    ledger = daemon.ledgers["demo"]
                    await await_cursor(ledger, store.embedding_index(MODEL), 1)
                    async with asyncio.timeout(5):
                        while True:
                            committed = ledger.commit_signal
                            if await store.backfill_position(BackfillSource.DECISIONS) is None:
                                break
                            await committed.wait()

                    paraphrase = "secure doorway"
                    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
                        pending = tasks.create_task(
                            client.call_tool(
                                "search", {"text": paraphrase, "kinds": [DECISION_KIND]}
                            )
                        )
                        (await endpoint.next_request(MODEL, paraphrase)).vectors([1, 0])
                    result = tool_json(pending.result())
                    assert result["ok"], result
                    assert result["data"] == {
                        "hits": [
                            {
                                "id": decision.decision_id,
                                "kind": DECISION_KIND,
                                "excerpt": exact_words,
                            }
                        ],
                        "omitted": 0,
                        "mode": "semantic",
                    }
                    assert endpoint.requests.empty()
            finally:
                await stop(daemon, task)
    finally:
        await store.close()

    # Both the original write and its retry initially saw the same committed empty state.
    assert empty_checkpoints == {"backfill": [(0, 0)] * 2, "embeddings": [(0, 0)] * 2}
    assert [
        (
            record.getMessage(),
            fields["project"],
            fields["source"],
            fields["attempt"],
            fields["failure_code"],
        )
        for record in caplog.records
        if record.name == "plotroom.search"
        for fields in (record.__dict__,)
    ] == [
        ("search write will retry", "demo", "backfill.decisions", 1, "sqlite_busy"),
        ("search write recovered", "demo", "backfill.decisions", 2, "sqlite_busy"),
        ("search write will retry", "demo", "embeddings", 1, "sqlite_full"),
        ("search write recovered", "demo", "embeddings", 2, "sqlite_full"),
    ]
